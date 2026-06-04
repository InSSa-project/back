import logging

import requests
from django.conf import settings

from .models import AiChatReference, AiDocument, ChatMessage, ChatSession

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
            'title': reference.get('title', ''),
            'source_type': reference.get('source_type') or metadata.get('source_type', ''),
            'score': reference.get('score', 0),
            'snippet': reference.get('snippet', ''),
            'chunk_id': reference.get('chunk_id', ''),
            'raw_data_id': reference.get('raw_data_id'),
        }


class AIService:
    def __init__(self, ai_server_client=None):
        self.ai_server_client = ai_server_client or FastAPIAIClient()

    def answer(self, user, message, session_id=None, options=None):
        if getattr(settings, 'AI_SERVER_ENABLED', False):
            payload = self.ai_server_client.chat(user=user, message=message, session_id=session_id)
            self._persist_chat(user=user, message=message, payload=payload, session_id=session_id)
            return payload
        return {
            'answer': 'AI_SERVER_ENABLED=false 상태입니다. RAG 답변을 사용하려면 FastAPI AI 서버를 켜고 설정을 true로 변경하세요.',
            'references': [],
            'intent': 'disabled',
            'query_type': 'UNKNOWN',
            'answer_policy': 'DISABLED',
            'usage': {},
        }

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
        except Exception:
            # Chat persistence should not block the user-facing answer.
            return

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


