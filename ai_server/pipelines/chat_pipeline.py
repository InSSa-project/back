import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from ai_server.classification.query_classifier import QueryClassifier
from ai_server.classification.domain_intent_router import DomainIntent, DomainIntentRouter
from ai_server.classification.personal_context_service import PersonalContextAnswerService
from ai_server.core.config import get_settings
from ai_server.llm.router import LlmRouter
from ai_server.memory.conversation_memory import ConversationMemory
from ai_server.policies.answer_policy import AnswerPolicyRouter, official_no_context_answer
from ai_server.optimization.response_limit import limit_answer
from ai_server.prompts.builder import PromptBuilder
from ai_server.prompts.response_style import ResponseStyle
from ai_server.rag.service import RAGService
from ai_server.retrieval.policy_router import RetrievalPolicyRouter
from ai_server.retrieval.query_parser import ScheduleQueryParser, ScheduleQueryType
from ai_server.schemas.chat import ChatRequest, ChatResponse


class ChatPipeline:
    def __init__(self, rag_service=None, llm_client=None):
        self.settings = get_settings()
        self.classifier = QueryClassifier()
        self.domain_intent_router = DomainIntentRouter()
        self.schedule_query_parser = ScheduleQueryParser()
        self.retrieval_policy_router = RetrievalPolicyRouter()
        self.rag_service = rag_service or RAGService()
        # Compatibility aliases for existing tests and extensions.
        self.schedule_retrieval = self.rag_service.schedule_retrieval
        self.retriever = self.rag_service.retriever
        self.retrieval_evaluator = self.rag_service.evaluator
        self.reranker = self.rag_service.reranker
        self.answer_policy_router = AnswerPolicyRouter()
        self.memory = ConversationMemory()
        self.prompt_builder = PromptBuilder()
        self.response_style = ResponseStyle()
        self.llm = llm_client or LlmRouter().get_provider(self.settings.llm_provider)

    def run(self, request: ChatRequest) -> ChatResponse:
        domain_intent = self.domain_intent_router.route(request.message).intent
        if domain_intent == DomainIntent.CURRENT_DATE:
            return self._current_date_response()
        if domain_intent in {
            DomainIntent.PERSONAL_SCORE,
            DomainIntent.PERSONAL_RISK,
            DomainIntent.RECOMMENDED_SCHEDULE,
            DomainIntent.IMPORTANT_SCHEDULE,
        }:
            return PersonalContextAnswerService(self.llm).answer(
                user_id=request.user_context.user_id,
                question=request.message,
                intent=domain_intent,
            )
        parsed_query = self.schedule_query_parser.parse(request.message)
        query_type = self._resolve_query_type(request.message, parsed_query)
        intent = self._intent_from_query_type(query_type)
        filters = self._build_filters(request)
        retrieval_policy = self.retrieval_policy_router.decide(parsed_query)
        if retrieval_policy.use_schedule_metadata:
            schedule_filters = self._schedule_filters(request.message, filters)
            retrieved = self.rag_service.search_schedules(parsed_query, filters=schedule_filters, question=request.message)
            return self._schedule_db_response(intent, query_type, parsed_query, retrieved)

        rag_result = self.rag_service.search_public(request.message, filters=filters)
        retrieval_evaluation = rag_result.evaluation
        policy = self.answer_policy_router.decide(query_type, retrieval_evaluation)
        if not policy.use_llm:
            return ChatResponse(
                answer=official_no_context_answer(),
                intent=intent,
                query_type=query_type,
                answer_policy=policy.answer_policy,
                references=[],
                usage={
                    'mode': 'official_no_context',
                    'retrieval': retrieval_evaluation.__dict__,
                },
            )

        chunks_for_prompt = [] if retrieval_evaluation.insufficient_context else rag_result.chunks
        prompt_result = self._build_prompt(
            request=request,
            intent=intent,
            query_type=query_type,
            chunks=chunks_for_prompt,
            retrieval_evaluation=retrieval_evaluation,
            answer_policy=policy.answer_policy,
            fallback_prefix=policy.fallback_prefix,
            parsed_query=parsed_query,
        )
        llm_response = self.llm.complete(prompt_result.messages)
        answer, answer_usage = limit_answer(llm_response['answer'])
        return ChatResponse(
            answer=answer,
            intent=intent,
            query_type=query_type,
            answer_policy=policy.answer_policy,
            references=rag_result.references if policy.use_references else [],
            usage={
                **llm_response.get('usage', {}),
                **answer_usage,
                'retrieval': retrieval_evaluation.__dict__,
                'extracted_date': self._format_extracted_date(parsed_query),
                'prompt': prompt_result.metadata,
            },
        )
    def stream(self, request: ChatRequest):
        domain_intent = self.domain_intent_router.route(request.message).intent
        if domain_intent == DomainIntent.CURRENT_DATE:
            response = self._current_date_response()
            yield f'data: {json.dumps({"type": "delta", "content": response.answer}, ensure_ascii=False)}\n\n'
            yield 'data: {"type": "done"}\n\n'
            return
        if domain_intent in {
            DomainIntent.PERSONAL_SCORE,
            DomainIntent.PERSONAL_RISK,
            DomainIntent.RECOMMENDED_SCHEDULE,
            DomainIntent.IMPORTANT_SCHEDULE,
        }:
            response = PersonalContextAnswerService(self.llm).answer(
                user_id=request.user_context.user_id,
                question=request.message,
                intent=domain_intent,
            )
            yield f'data: {json.dumps({"type": "delta", "content": response.answer}, ensure_ascii=False)}\n\n'
            yield 'data: {"type": "done"}\n\n'
            return
        parsed_query = self.schedule_query_parser.parse(request.message)
        query_type = self._resolve_query_type(request.message, parsed_query)
        intent = self._intent_from_query_type(query_type)
        retrieval_policy = self.retrieval_policy_router.decide(parsed_query)
        filters = self._build_filters(request)
        if retrieval_policy.use_schedule_metadata:
            schedule_filters = self._schedule_filters(request.message, filters)
            retrieved = self.rag_service.search_schedules(parsed_query, filters=schedule_filters, question=request.message)
            response = self._schedule_db_response(intent, query_type, parsed_query, retrieved)
            yield f'data: {json.dumps({"type": "delta", "content": response.answer}, ensure_ascii=False)}\n\n'
            yield 'data: {"type": "done"}\n\n'
            return

        rag_result = self.rag_service.search_public(request.message, filters=filters)
        retrieval_evaluation = rag_result.evaluation
        policy = self.answer_policy_router.decide(query_type, retrieval_evaluation)
        if not policy.use_llm:
            yield f'data: {json.dumps({"type": "delta", "content": official_no_context_answer()}, ensure_ascii=False)}\n\n'
            yield 'data: {"type": "done"}\n\n'
            return

        prompt_result = self._build_prompt(
            request=request,
            intent=intent,
            query_type=query_type,
            chunks=[] if retrieval_evaluation.insufficient_context else rag_result.chunks,
            retrieval_evaluation=retrieval_evaluation,
            answer_policy=policy.answer_policy,
            fallback_prefix=policy.fallback_prefix,
            parsed_query=parsed_query,
        )
        for delta in self.llm.stream(prompt_result.messages):
            yield f'data: {json.dumps({"type": "delta", "content": delta}, ensure_ascii=False)}\n\n'
        yield 'data: {"type": "done"}\n\n'
    def _is_current_date_question(self, question: str) -> bool:
        normalized = re.sub(r'\s+', '', question)
        return any(pattern in normalized for pattern in ['오늘날짜', '현재날짜', '오늘며칠', '오늘이몇일', '오늘이무슨날'])

    def _current_date_response(self) -> ChatResponse:
        today = datetime.now(ZoneInfo('Asia/Seoul')).date()
        weekday = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일'][today.weekday()]
        answer = self.response_style.current_date(f'{today.year}년 {today.month}월 {today.day}일 {weekday}')
        return ChatResponse(
            answer=answer,
            intent='current_date',
            query_type='CURRENT_DATE',
            answer_policy='SERVER_DATE_DIRECT',
            references=[],
            usage={
                'mode': 'server_date_direct',
                'llm_tokens': 0,
                'answer_chars': len(answer),
            },
        )

    def _build_prompt(
        self,
        request: ChatRequest,
        intent: str,
        query_type: str,
        chunks,
        retrieval_evaluation,
        answer_policy: str,
        fallback_prefix: str = '',
        parsed_query=None,
    ):
        user_context = request.user_context.model_dump_json()
        memory_context = self.memory.build_context(request.session_id)
        return self.prompt_builder.build_messages(
            question=request.message,
            intent=intent,
            query_type=query_type,
            answer_policy=answer_policy,
            insufficient_context=retrieval_evaluation.insufficient_context,
            extracted_date=self._format_extracted_date(parsed_query),
            retrieval_status=retrieval_evaluation.reason,
            exact_match=bool(parsed_query and parsed_query.start_date and not retrieval_evaluation.insufficient_context),
            chunks=chunks,
            user_context=user_context,
            memory_context=memory_context,
            fallback_prefix=fallback_prefix,
        )

    def _build_filters(self, request: ChatRequest) -> dict:
        return {
            'user_id': request.user_context.user_id,
            'campus': request.user_context.campus,
            'generation': request.user_context.generation,
        }

    def _intent_from_query_type(self, query_type: str) -> str:
        return query_type.lower()

    def _resolve_query_type(self, question: str, parsed_query) -> str:
        if parsed_query.query_type != ScheduleQueryType.GENERAL_CHAT:
            return parsed_query.query_type
        return self.classifier.classify(question)


    def _schedule_filters(self, question: str, filters: dict) -> dict:
        schedule_filters = dict(filters)
        if '개인' in question or '내 일정' in question or '내일정' in question:
            schedule_filters['event_type'] = 'personal'
        return schedule_filters

    def _schedule_db_response(self, intent: str, query_type: str, parsed_query, chunks) -> ChatResponse:
        if not chunks:
            return ChatResponse(
                answer=self._schedule_no_context_answer(parsed_query),
                intent=intent,
                query_type=query_type,
                answer_policy='SCHEDULE_DB_NO_MATCH',
                references=[],
                usage={
                    'mode': 'schedule_db_direct',
                    'retrieved_count': 0,
                    'extracted_date': self._format_extracted_date(parsed_query),
                },
            )
        answer, answer_usage = limit_answer(self._format_schedule_answer(parsed_query, chunks))
        return ChatResponse(
            answer=answer,
            intent=intent,
            query_type=query_type,
            answer_policy='SCHEDULE_DB_DIRECT',
            references=[],
            usage={
                'mode': 'schedule_db_direct',
                'retrieved_count': len(chunks),
                'llm_tokens': 0,
                **answer_usage,
                'extracted_date': self._format_extracted_date(parsed_query),
            },
        )

    def _format_schedule_answer(self, parsed_query, chunks) -> str:
        target = self._format_extracted_date(parsed_query) or '요청한 기간'
        total_count = len(chunks)
        visible_chunks = chunks[: self.settings.max_schedule_answer_items]
        header_target = parsed_query.display_label or f'{target}에 확인된 일정'
        header = self.response_style.schedule_header(header_target, total_count)
        lines = [header]
        for index, chunk in enumerate(visible_chunks, start=1):
            metadata = chunk.metadata or {}
            start_at = self._format_datetime(metadata.get('start_at', ''))
            end_at = self._format_datetime(metadata.get('end_at', ''))
            visibility = '개인' if metadata.get('is_personal') else '공용'
            event_label = self._event_type_label(metadata.get('event_type') or '') or '일정'
            time_text = self._format_time_range(start_at, end_at)
            suffix = f' {time_text}' if time_text else ''
            lines.append(f'{index}. {chunk.title} ({visibility}-{event_label}){suffix}')
        if total_count > len(visible_chunks):
            lines.append(f'외 {total_count - len(visible_chunks)}건은 캘린더에서 확인할 수 있습니다.')
        return '\n'.join(lines)
    def _event_type_label(self, event_type: str) -> str:
        labels = {
            'personal': '개인',
            'holiday': '공휴일',
            'exam': '시험',
            'assignment': '과제/마감',
            'deadline': '마감',
            'lecture': '특강/강의',
            'project': '프로젝트',
            'study': '학습',
            'notice': '공지',
        }
        return labels.get(event_type, event_type)

    def _format_time_range(self, start_at: str, end_at: str) -> str:
        if not start_at:
            return ''
        if not end_at or end_at == start_at:
            return start_at
        start_date, _, start_time = start_at.partition(' ')
        end_date, _, end_time = end_at.partition(' ')
        if start_date == end_date:
            if start_time == '00:00' and end_time in {'23:59', '00:00'}:
                return f'{start_date} 종일'
            return f'{start_date} {start_time}~{end_time}'
        return f'{start_at} ~ {end_at}'
    def _format_datetime(self, value: str) -> str:
        if not value:
            return ''
        try:
            from datetime import datetime

            parsed = datetime.fromisoformat(value)
            return parsed.strftime('%Y-%m-%d %H:%M')
        except ValueError:
            return value
    def _empty_schedule_response(self, intent: str, query_type: str, parsed_query, retrieval_evaluation) -> ChatResponse:
        return ChatResponse(
            answer=self._schedule_no_context_answer(parsed_query),
            intent=intent,
            query_type=query_type,
            answer_policy='SCHEDULE_NO_EXACT_MATCH',
            references=[],
            usage={
                'mode': 'schedule_no_exact_match',
                'extracted_date': self._format_extracted_date(parsed_query),
                'retrieval': retrieval_evaluation.__dict__,
            },
        )

    def _schedule_no_context_answer(self, parsed_query) -> str:
        if parsed_query and parsed_query.display_label:
            return self.response_style.schedule_no_data(parsed_query.display_label)
        target = self._format_extracted_date(parsed_query) or '요청한 기간'
        return self.response_style.schedule_no_data(f'{target}에 확인된 일정')
    def _format_extracted_date(self, parsed_query) -> str:
        if not parsed_query:
            return ''
        if parsed_query.display_label:
            return parsed_query.display_label
        if parsed_query.start_date and parsed_query.start_date == parsed_query.end_date:
            return parsed_query.start_date
        if parsed_query.start_date and parsed_query.end_date:
            return f'{parsed_query.start_date} ~ {parsed_query.end_date}'
        return ''








