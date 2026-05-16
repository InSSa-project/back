import json

from ai_server.classification.query_classifier import QueryClassifier
from ai_server.core.config import get_settings
from ai_server.llm.router import LlmRouter
from ai_server.memory.conversation_memory import ConversationMemory
from ai_server.optimization.token_budget import TokenBudgetManager
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
        self.token_budget = TokenBudgetManager()

    def run(self, request: ChatRequest) -> ChatResponse:
        parsed_query = self.schedule_query_parser.parse(request.message)
        query_type = self._resolve_query_type(request.message, parsed_query)
        intent = self._intent_from_query_type(query_type)
        filters = self._build_filters(request, intent)
        retrieval_policy = self.retrieval_policy_router.decide(parsed_query)
        retrieved = self._retrieve(request.message, parsed_query, retrieval_policy, filters)
        reranked = self.reranker.rerank(request.message, retrieved)
        retrieval_evaluation = self.retrieval_evaluator.evaluate(reranked)

        if retrieval_policy.use_schedule_metadata and not reranked:
            return self._empty_schedule_response(intent, query_type, parsed_query, retrieval_evaluation)

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
        messages = self._build_messages(
            request=request,
            intent=intent,
            query_type=query_type,
            chunks=chunks_for_prompt,
            retrieval_evaluation=retrieval_evaluation,
            answer_policy=policy.answer_policy,
            fallback_prefix=policy.fallback_prefix,
            parsed_query=parsed_query,
            retrieval_status=retrieval_evaluation.reason,
        )
        llm_response = self.llm.complete(messages)
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
            },
        )

    def stream(self, request: ChatRequest):
        parsed_query = self.schedule_query_parser.parse(request.message)
        query_type = self._resolve_query_type(request.message, parsed_query)
        intent = self._intent_from_query_type(query_type)
        retrieval_policy = self.retrieval_policy_router.decide(parsed_query)
        retrieved = self.reranker.rerank(
            request.message,
            self._retrieve(request.message, parsed_query, retrieval_policy, self._build_filters(request, intent)),
        )
        retrieval_evaluation = self.retrieval_evaluator.evaluate(retrieved)
        if retrieval_policy.use_schedule_metadata and not retrieved:
            yield f'data: {json.dumps({"type": "delta", "content": self._schedule_no_context_answer(parsed_query)}, ensure_ascii=False)}\n\n'
            yield 'data: {"type": "done"}\n\n'
            return
        policy = self.answer_policy_router.decide(query_type, retrieval_evaluation)
        if not policy.use_llm:
            yield f'data: {json.dumps({"type": "delta", "content": official_no_context_answer()}, ensure_ascii=False)}\n\n'
            yield 'data: {"type": "done"}\n\n'
            return
        messages = self._build_messages(
            request=request,
            intent=intent,
            query_type=query_type,
            chunks=[] if retrieval_evaluation.insufficient_context else retrieved,
            retrieval_evaluation=retrieval_evaluation,
            answer_policy=policy.answer_policy,
            fallback_prefix=policy.fallback_prefix,
            parsed_query=parsed_query,
            retrieval_status=retrieval_evaluation.reason,
        )
        for delta in self.llm.stream(messages):
            yield f'data: {json.dumps({"type": "delta", "content": delta}, ensure_ascii=False)}\n\n'
        yield 'data: {"type": "done"}\n\n'

    def _build_messages(
        self,
        request: ChatRequest,
        intent: str,
        query_type: str,
        chunks,
        retrieval_evaluation,
        answer_policy: str,
        fallback_prefix: str = '',
        parsed_query=None,
        retrieval_status: str = '',
    ) -> list[dict]:
        retrieved_context = self.token_budget.trim_context('\n\n'.join(chunk.content for chunk in chunks))
        user_context = request.user_context.model_dump_json()
        memory_context = self.memory.build_context(request.session_id)
        few_shot_examples = self._select_few_shot(intent)
        return self.prompt_builder.build_messages(
            question=request.message,
            intent=intent,
            query_type=query_type,
            answer_policy=answer_policy,
            insufficient_context=retrieval_evaluation.insufficient_context,
            extracted_date=self._format_extracted_date(parsed_query),
            retrieval_status=retrieval_status,
            exact_match=bool(parsed_query and parsed_query.start_date and not retrieval_evaluation.insufficient_context),
            retrieved_context=retrieved_context,
            user_context=user_context,
            memory_context=memory_context,
            few_shot_examples=few_shot_examples,
            fallback_prefix=fallback_prefix,
        )

    def _build_filters(self, request: ChatRequest, intent: str) -> dict:
        return {
            'campus': request.user_context.campus,
            'generation': request.user_context.generation,
            'intent': intent,
        }

    def _select_few_shot(self, intent: str) -> str:
        if intent == 'schedule_exact_date':
            return 'Answer only for the exact requested date. Do not mention nearby dates.'
        if intent == 'schedule_month':
            return 'Summarize schedules in the requested month as a concise dated list.'
        if intent == 'schedule_range':
            return 'Summarize schedules only inside the requested date range.'
        if intent == 'general_tech':
            return 'Explain with practical engineering examples and do not label it as SSAFY policy.'
        if intent == 'general_advice':
            return 'Give realistic steps and clearly mark advice as non-official guidance.'
        return 'Use concise, cautious INSSA style examples.'

    def _intent_from_query_type(self, query_type: str) -> str:
        return query_type.lower()

    def _resolve_query_type(self, question: str, parsed_query) -> str:
        if parsed_query.query_type != ScheduleQueryType.GENERAL_CHAT:
            return parsed_query.query_type
        return self.classifier.classify(question)

    def _retrieve(self, question: str, parsed_query, retrieval_policy, filters: dict):
        if retrieval_policy.use_schedule_metadata:
            return self.schedule_retrieval.retrieve(parsed_query, filters=filters)
        return self.retriever.retrieve(question, filters=filters)

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
        target = self._format_extracted_date(parsed_query)
        if parsed_query.query_type == ScheduleQueryType.SCHEDULE_EXACT_DATE:
            return f'{target}에 해당하는 일정은 현재 등록된 SSAFY 자료에서 확인되지 않습니다.'
        return f'{target} 범위에 해당하는 일정은 현재 등록된 SSAFY 자료에서 확인되지 않습니다.'

    def _format_extracted_date(self, parsed_query) -> str:
        if not parsed_query:
            return ''
        if parsed_query.start_date and parsed_query.start_date == parsed_query.end_date:
            return parsed_query.start_date
        if parsed_query.start_date and parsed_query.end_date:
            return f'{parsed_query.start_date} ~ {parsed_query.end_date}'
        return ''
