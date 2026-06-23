import json
import logging
import time
from pathlib import Path

import requests
from django.conf import settings
from django.utils import timezone

from .models import AiChatReference, AiDocument, AiPipelineRun, AiQualityLog, ChatMessage, ChatSession

logger = logging.getLogger(__name__)


class FastAPIAIClient:
    def __init__(self, base_url=None):
        self.base_url = (base_url or getattr(settings, 'AI_SERVER_BASE_URL', 'http://localhost:8001')).rstrip('/')
        self.timeout = getattr(settings, 'AI_REQUEST_TIMEOUT', 20)

    def chat(self, user, message, session_id=None):
        try:
            response = requests.post(
                f'{self.base_url}/v1/chat',
                json={
                    'session_id': session_id,
                    'message': message,
                    'user_context': {
                        'user_id': user.id,
                        'campus': getattr(user, 'campus', ''),
                        'generation': getattr(user, 'generation', ''),
                        'track': getattr(user, 'track', ''),
                        'risk_level': getattr(getattr(user, 'risk_status', None), 'risk_level', ''),
                    },
                    'stream': False,
                },
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            logger.warning('ai_server_timeout error_type=%s', exc.__class__.__name__)
            return self._error_payload('AI 응답 시간이 초과됐어요. 잠시 후 다시 시도해 주세요.', 'ai_server_timeout', exc)
        except requests.RequestException as exc:
            logger.warning('ai_server_connection_error error_type=%s', exc.__class__.__name__)
            return self._error_payload('AI 서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.', 'ai_server_connection_error', exc)

        if not response.ok:
            logger.warning('ai_server_error status=%s', response.status_code)
            return self._error_payload('AI 서버가 요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.', 'ai_server_error')

        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning('ai_server_non_json error_type=%s', exc.__class__.__name__)
            return self._error_payload('AI 서버 응답을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.', 'ai_server_non_json', exc)

        payload['references'] = [self._normalize_reference(ref) for ref in payload.get('references', [])]
        return payload

    def _error_payload(self, answer, mode, exc=None):
        usage = {'mode': mode}
        if exc is not None:
            usage['error_type'] = exc.__class__.__name__
        return {
            'answer': answer,
            'references': [],
            'intent': 'error',
            'query_type': 'UNKNOWN',
            'answer_policy': 'ERROR',
            'usage': usage,
        }

    def _normalize_reference(self, reference):
        metadata = reference.get('metadata') or {}
        return {
            'document_id': reference.get('ai_document_id'),
            'ai_document_id': reference.get('ai_document_id'),
            'title': reference.get('title', ''),
            'source_type': reference.get('source_type') or metadata.get('source_type', ''),
            'source_url': self._reference_url(reference, metadata),
            'detail_url': self._reference_detail_url(reference, metadata),
            'score': reference.get('score', 0),
            'snippet': reference.get('snippet', ''),
            'chunk_id': reference.get('chunk_id', ''),
            'raw_data_id': reference.get('raw_data_id'),
            'metadata': metadata,
        }

    def _reference_url(self, reference, metadata):
        return (
            reference.get('source_url')
            or metadata.get('source_url')
            or metadata.get('original_url')
            or metadata.get('url')
            or ''
        )

    def _reference_detail_url(self, reference, metadata):
        return (
            reference.get('detail_url')
            or metadata.get('detail_url')
            or self._reference_url(reference, metadata)
            or ''
        )


class AIService:
    def __init__(self, ai_server_client=None):
        self.ai_server_client = ai_server_client or FastAPIAIClient()

    def answer(self, user, message, session_id=None, options=None):
        started_at = time.monotonic()
        if getattr(settings, 'AI_SERVER_ENABLED', False):
            payload = self.ai_server_client.chat(user=user, message=message, session_id=session_id)
            self._annotate_route_metrics(payload)
            persisted_session = self._persist_chat(user=user, message=message, payload=payload, session_id=session_id)
            if persisted_session:
                payload['session_id'] = persisted_session.id
            self._persist_pipeline_run(
                user=user,
                message=message,
                payload=payload,
                session=persisted_session,
                latency_ms=self._elapsed_ms(started_at),
            )
            self._append_route_log(user=user, message=message, payload=payload, session_id=session_id)
            return payload
        payload = {
            'answer': 'AI_SERVER_ENABLED=false ?????. RAG ??? ????? FastAPI AI ??? ?? ??? true? ?????.',
            'references': [],
            'intent': 'disabled',
            'query_type': 'UNKNOWN',
            'answer_policy': 'DISABLED',
            'usage': {},
        }
        self._annotate_route_metrics(payload)
        self._persist_pipeline_run(
            user=user,
            message=message,
            payload=payload,
            session=None,
            latency_ms=self._elapsed_ms(started_at),
        )
        self._append_route_log(user=user, message=message, payload=payload, session_id=session_id)
        return payload

    def _elapsed_ms(self, started_at):
        return max(0, int((time.monotonic() - started_at) * 1000))

    def _annotate_route_metrics(self, payload):
        usage = dict(payload.get('usage') or {})
        answer_policy = str(payload.get('answer_policy') or '')
        mode = str(usage.get('mode') or '')
        references = payload.get('references') or []
        route_stage = self._route_stage(answer_policy, mode, references)
        used_llm_intent = answer_policy.startswith('LLM_INTENT_') or answer_policy.startswith('SERVER_VERIFIED_') or bool(usage.get('llm_intent'))
        used_rag = bool(references) or route_stage in {'rag', 'rag_llm'} or answer_policy in {'RAG_GROUNDED'}
        used_db = route_stage in {'db', 'llm_intent_db', 'personal_db', 'personal_db_llm'}
        used_llm = (
            used_llm_intent
            or route_stage in {'llm', 'rag_llm', 'personal_db_llm'}
            or bool(usage.get('total_tokens') or usage.get('prompt_tokens') or usage.get('completion_tokens'))
        )
        usage.update(
            {
                'route_metric_version': 1,
                'route_stage': route_stage,
                'used_db': used_db,
                'used_rag': used_rag,
                'used_llm': used_llm,
                'llm_intent_used': used_llm_intent,
                'llm_conversion': used_llm and route_stage not in {'db', 'personal_db'},
            }
        )
        payload['usage'] = usage

    def _route_stage(self, answer_policy: str, mode: str, references: list) -> str:
        if answer_policy in {'ERROR'} or mode.endswith('_error') or mode.startswith('ai_server_'):
            return 'error'
        if answer_policy == 'DISABLED':
            return 'disabled'
        if answer_policy.startswith('LLM_INTENT_') or answer_policy.startswith('SERVER_VERIFIED_'):
            return 'llm_intent_db' if 'SCHEDULE_DB' in answer_policy else 'llm_intent'
        if answer_policy.startswith('SCHEDULE_MEMORY') or mode == 'schedule_memory':
            return 'db'
        if answer_policy.startswith('SCHEDULE_DB') or mode == 'schedule_db_direct':
            return 'db'
        if answer_policy == 'PERSONAL_CONTEXT_LLM' or mode == 'personal_context_llm':
            return 'personal_db_llm'
        if answer_policy.startswith('PERSONAL_CONTEXT'):
            return 'personal_db'
        if answer_policy == 'RAG_GROUNDED' or references:
            return 'rag_llm'
        if answer_policy in {'OFFICIAL_NO_CONTEXT'} or mode == 'official_no_context':
            return 'rag'
        if answer_policy in {'GENERAL_KNOWLEDGE_FALLBACK', 'GENERAL_ADVICE_FALLBACK', 'CAUTIOUS_FALLBACK'}:
            return 'llm'
        return 'unknown'

    def _append_route_log(self, user, message, payload, session_id=None):
        try:
            path = self._route_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            usage = payload.get('usage') or {}
            record = {
                'created_at': timezone.now().isoformat(),
                'user_id': getattr(user, 'id', None),
                'session_id': session_id,
                'question': message,
                'answer': payload.get('answer', ''),
                'answer_preview': (payload.get('answer') or '').replace('\n', ' ')[:200],
                'intent': payload.get('intent', ''),
                'query_type': payload.get('query_type', ''),
                'answer_policy': payload.get('answer_policy', ''),
                'route_stage': usage.get('route_stage', 'unknown'),
                'used_db': usage.get('used_db', False),
                'used_rag': usage.get('used_rag', False),
                'used_llm': usage.get('used_llm', False),
                'llm_intent_used': usage.get('llm_intent_used', False),
                'llm_conversion': usage.get('llm_conversion', False),
                'retrieved_count': usage.get('retrieved_count'),
                'fallback_retrieved_count': usage.get('fallback_retrieved_count'),
                'prompt_tokens': usage.get('prompt_tokens'),
                'completion_tokens': usage.get('completion_tokens'),
                'total_tokens': usage.get('total_tokens'),
                'answer_chars': len(payload.get('answer') or ''),
            }
            with path.open('a', encoding='utf-8') as file:
                file.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
        except Exception as exc:
            logger.debug('ai_route_metric_log_failed error_type=%s', exc.__class__.__name__)

    def _route_log_path(self):
        configured = getattr(settings, 'AI_ROUTE_LOG_PATH', '')
        if configured:
            return Path(configured)
        return Path(settings.BASE_DIR) / 'var' / 'logs' / 'ai_route_metrics.jsonl'

    def _persist_chat(self, user, message, payload, session_id=None):
        try:
            session = self._get_or_create_session(user, message, session_id)
            ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_USER, content=message)
            assistant_message = ChatMessage.objects.create(
                session=session,
                role=ChatMessage.ROLE_ASSISTANT,
                content=payload.get('answer', ''),
                usage_json=payload.get('usage') or {},
            )
            for reference in payload.get('references', []):
                document_id = reference.get('document_id')
                if not document_id:
                    continue
                document = AiDocument.objects.filter(id=document_id).first()
                if document:
                    AiChatReference.objects.create(
                        chat_message=assistant_message,
                        ai_document=document,
                        relevance_score=reference.get('score') or 0,
                    )
            return session
        except Exception:
            # Chat persistence should not block the user-facing answer.
            return None

    def _persist_pipeline_run(self, user, message, payload, session=None, latency_ms=None):
        try:
            usage = payload.get('usage') or {}
            references = payload.get('references') or []
            answer_policy = payload.get('answer_policy', '')
            failure_type = self._failure_type(payload)
            run = AiPipelineRun.objects.create(
                user=user if getattr(user, 'is_authenticated', False) else None,
                session=session,
                question=message,
                answer=payload.get('answer', ''),
                intent=payload.get('intent', ''),
                query_type=payload.get('query_type', ''),
                answer_policy=answer_policy,
                route_stage=usage.get('route_stage', ''),
                failure_type=failure_type,
                is_success=not bool(failure_type),
                retrieved_context_json=self._retrieved_context_snapshot(usage, references),
                references_json=references,
                usage_json=usage,
                latency_ms=latency_ms,
            )
            self._persist_quality_log(run)
        except Exception as exc:
            logger.debug('ai_pipeline_run_persist_failed error_type=%s', exc.__class__.__name__)

    def _persist_quality_log(self, run):
        try:
            metrics = self._quality_metrics(run)
            AiQualityLog.objects.create(
                pipeline_run=run,
                user=run.user,
                evaluator='auto',
                quality_score=metrics['quality_score'],
                latency_ms=metrics['latency_ms'],
                retrieved_count=metrics['retrieved_count'],
                is_fallback=metrics['is_fallback'],
                is_error=metrics['is_error'],
                is_no_context=metrics['is_no_context'],
                metrics_json=metrics,
            )
        except Exception as exc:
            logger.debug('ai_quality_log_persist_failed error_type=%s', exc.__class__.__name__)

    def _quality_metrics(self, run):
        usage = run.usage_json or {}
        context = run.retrieved_context_json or {}
        policy = str(run.answer_policy or '')
        answer = run.answer or ''
        retrieved_count = self._safe_int(context.get('retrieved_count'))
        fallback_count = self._safe_int(context.get('fallback_retrieved_count'))
        reference_count = self._safe_int(context.get('reference_count'))
        latency_ms = self._safe_int(run.latency_ms)
        is_fallback = 'FALLBACK' in policy
        is_no_context = any(marker in policy for marker in ('NO_CONTEXT', 'NO_MATCH', 'NO_EXACT_MATCH'))
        is_error = bool(run.failure_type) or policy == 'ERROR'

        score = 1.0
        if is_error:
            score = 0.1
        elif is_no_context:
            score = 0.45
        elif is_fallback:
            score = 0.65

        if not is_error and not is_no_context:
            if retrieved_count == 0 and reference_count == 0 and 'SCHEDULE_DB_DIRECT' not in policy:
                score -= 0.15
            if fallback_count:
                score -= 0.1
            if latency_ms >= 20000:
                score -= 0.2
            elif latency_ms >= 10000:
                score -= 0.1
            if len(answer.strip()) < 10:
                score -= 0.1

        score = max(0.0, min(1.0, round(score, 3)))
        return {
            'quality_score': score,
            'is_error': is_error,
            'is_fallback': is_fallback,
            'is_no_context': is_no_context,
            'retrieved_count': retrieved_count,
            'fallback_retrieved_count': fallback_count,
            'reference_count': reference_count,
            'latency_ms': latency_ms,
            'answer_chars': len(answer),
            'mode': usage.get('mode', ''),
            'answer_policy': policy,
        }

    def _safe_int(self, value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _failure_type(self, payload):
        usage = payload.get('usage') or {}
        if payload.get('answer_policy') == 'ERROR':
            return str(usage.get('mode') or 'error')
        mode = str(usage.get('mode') or '')
        if mode.endswith('_error') or mode.startswith('ai_server_'):
            return mode
        return ''

    def _retrieved_context_snapshot(self, usage, references):
        return {
            'retrieval': usage.get('retrieval', {}),
            'prompt': usage.get('prompt', {}),
            'reference_count': len(references),
            'retrieved_count': usage.get('retrieved_count'),
            'fallback_retrieved_count': usage.get('fallback_retrieved_count'),
        }

    def _get_or_create_session(self, user, message, session_id=None):
        if session_id:
            session = ChatSession.objects.filter(id=session_id, user=user).first()
            if session:
                return session
        title = (message[:40] or '새 채팅').strip()
        return ChatSession.objects.create(user=user, title=title)



# Backward-compatible aliases for existing imports and tests.
AiServerClient = FastAPIAIClient
AiChatService = AIService
