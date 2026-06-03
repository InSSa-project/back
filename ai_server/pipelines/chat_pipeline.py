import json

from ai_server.classification.query_classifier import QueryClassifier
from ai_server.core.config import get_settings
from ai_server.llm.router import LlmRouter
from ai_server.memory.conversation_memory import ConversationMemory
from ai_server.policies.answer_policy import AnswerPolicyRouter, official_no_context_answer
from ai_server.prompts.builder import PromptBuilder
from ai_server.references.tracker import ReferenceTracker
from ai_server.rerankers.simple_reranker import SimpleReranker
from ai_server.retrieval.policy_router import RetrievalPolicyRouter
from ai_server.retrieval.query_parser import ScheduleQueryParser, ScheduleQueryType
from ai_server.retrieval.schedule_retrieval import ScheduleRetrievalService
from ai_server.retrievers.evaluator import RetrievalEvaluator
from ai_server.retrievers.hybrid_retriever import HybridRetriever
from ai_server.schemas.chat import ChatRequest, ChatResponse


class ChatPipeline:
    def __init__(self):
        self.settings = get_settings()
        self.classifier = QueryClassifier()
        self.schedule_query_parser = ScheduleQueryParser()
        self.retrieval_policy_router = RetrievalPolicyRouter()
        self.schedule_retrieval = ScheduleRetrievalService()
        self.retriever = HybridRetriever()
        self.retrieval_evaluator = RetrievalEvaluator()
        self.answer_policy_router = AnswerPolicyRouter()
        self.reranker = SimpleReranker()
        self.memory = ConversationMemory()
        self.prompt_builder = PromptBuilder()
        self.llm = LlmRouter().get_provider(self.settings.llm_provider)
        self.reference_tracker = ReferenceTracker()

    def run(self, request: ChatRequest) -> ChatResponse:
        parsed_query = self.schedule_query_parser.parse(request.message)
        query_type = self._resolve_query_type(request.message, parsed_query)
        intent = self._intent_from_query_type(query_type)
        filters = self._build_filters(request)
        retrieval_policy = self.retrieval_policy_router.decide(parsed_query)
        retrieved = self._retrieve(request.message, parsed_query, retrieval_policy, filters)
        if retrieval_policy.use_schedule_metadata:
            return self._schedule_db_response(intent, query_type, parsed_query, retrieved)

        reranked = self.reranker.rerank(request.message, retrieved)
        retrieval_evaluation = self.retrieval_evaluator.evaluate(reranked)

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

        chunks_for_prompt = [] if retrieval_evaluation.insufficient_context else reranked
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
        return ChatResponse(
            answer=llm_response['answer'],
            intent=intent,
            query_type=query_type,
            answer_policy=policy.answer_policy,
            references=self.reference_tracker.from_chunks(chunks_for_prompt) if policy.use_references else [],
            usage={
                **llm_response.get('usage', {}),
                'retrieval': retrieval_evaluation.__dict__,
                'extracted_date': self._format_extracted_date(parsed_query),
                'prompt': prompt_result.metadata,
            },
        )
    def stream(self, request: ChatRequest):
        parsed_query = self.schedule_query_parser.parse(request.message)
        query_type = self._resolve_query_type(request.message, parsed_query)
        intent = self._intent_from_query_type(query_type)
        retrieval_policy = self.retrieval_policy_router.decide(parsed_query)
        retrieved = self._retrieve(request.message, parsed_query, retrieval_policy, self._build_filters(request))
        if retrieval_policy.use_schedule_metadata:
            response = self._schedule_db_response(intent, query_type, parsed_query, retrieved)
            yield f'data: {json.dumps({"type": "delta", "content": response.answer}, ensure_ascii=False)}\n\n'
            yield 'data: {"type": "done"}\n\n'
            return

        retrieved = self.reranker.rerank(request.message, retrieved)
        retrieval_evaluation = self.retrieval_evaluator.evaluate(retrieved)

        policy = self.answer_policy_router.decide(query_type, retrieval_evaluation)
        if not policy.use_llm:
            yield f'data: {json.dumps({"type": "delta", "content": official_no_context_answer()}, ensure_ascii=False)}\n\n'
            yield 'data: {"type": "done"}\n\n'
            return

        prompt_result = self._build_prompt(
            request=request,
            intent=intent,
            query_type=query_type,
            chunks=[] if retrieval_evaluation.insufficient_context else retrieved,
            retrieval_evaluation=retrieval_evaluation,
            answer_policy=policy.answer_policy,
            fallback_prefix=policy.fallback_prefix,
            parsed_query=parsed_query,
        )
        for delta in self.llm.stream(prompt_result.messages):
            yield f'data: {json.dumps({"type": "delta", "content": delta}, ensure_ascii=False)}\n\n'
        yield 'data: {"type": "done"}\n\n'
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

    def _retrieve(self, question: str, parsed_query, retrieval_policy, filters: dict):
        if retrieval_policy.use_schedule_metadata:
            filters = self._schedule_filters(question, filters)
            return self.schedule_retrieval.retrieve(parsed_query, filters=filters, query=question)
        return self.retriever.retrieve(question, filters=filters)

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
        return ChatResponse(
            answer=self._format_schedule_answer(parsed_query, chunks),
            intent=intent,
            query_type=query_type,
            answer_policy='SCHEDULE_DB_DIRECT',
            references=[],
            usage={
                'mode': 'schedule_db_direct',
                'retrieved_count': len(chunks),
                'extracted_date': self._format_extracted_date(parsed_query),
            },
        )

    def _format_schedule_answer(self, parsed_query, chunks) -> str:
        target = self._format_extracted_date(parsed_query) or '요청한 기간'
        lines = [f'{target}에 확인된 일정은 {len(chunks)}건입니다.']
        for index, chunk in enumerate(chunks, start=1):
            metadata = chunk.metadata or {}
            start_at = self._format_datetime(metadata.get('start_at', ''))
            end_at = self._format_datetime(metadata.get('end_at', ''))
            visibility = '개인' if metadata.get('is_personal') else '공용'
            event_label = self._event_type_label(metadata.get('event_type') or '') or '일정'
            time_text = self._format_time_range(start_at, end_at)
            suffix = f' {time_text}' if time_text else ''
            lines.append(f'{index}. {chunk.title} ({visibility}-{event_label}){suffix}')
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
        target = self._format_extracted_date(parsed_query) or '요청한 기간'
        return f'{target}에 확인된 일정이 없습니다.'
    def _format_extracted_date(self, parsed_query) -> str:
        if not parsed_query:
            return ''
        if parsed_query.start_date and parsed_query.start_date == parsed_query.end_date:
            return parsed_query.start_date
        if parsed_query.start_date and parsed_query.end_date:
            return f'{parsed_query.start_date} ~ {parsed_query.end_date}'
        return ''
