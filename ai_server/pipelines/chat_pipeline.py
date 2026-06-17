import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from ai_server.classification.query_classifier import QueryClassifier
from ai_server.classification.domain_intent_router import DomainIntent, DomainIntentRouter
from ai_server.classification.personal_context_service import PersonalContextAnswerService
from ai_server.classification.llm_intent_classifier import LLMIntentClassifier, LLMIntentResult
from ai_server.classification.server_verified_router import ServerVerifiedIntentRouter, VerifiedRoute
from ai_server.core.config import get_settings
from ai_server.llm.router import LlmRouter
from ai_server.memory.conversation_memory import ConversationMemory
from ai_server.policies.answer_policy import AnswerPolicyRouter, official_no_context_answer
from ai_server.optimization.response_limit import limit_answer
from ai_server.prompts.builder import PromptBuilder
from ai_server.prompts.response_style import ResponseStyle
from ai_server.rag.service import RAGService
from ai_server.retrieval.policy_router import RetrievalPolicyRouter
from ai_server.retrieval.query_parser import ParsedQuery, ScheduleQueryParser, ScheduleQueryType
from ai_server.retrieval.schedule_constraints import ScheduleConstraintMerger
from ai_server.retrieval.schedule_formatter import ScheduleAnswerFormatter
from ai_server.schemas.chat import ChatRequest, ChatResponse


class ChatPipeline:
    def __init__(self, rag_service=None, llm_client=None):
        self.settings = get_settings()
        self.classifier = QueryClassifier()
        self.domain_intent_router = DomainIntentRouter()
        self.server_verified_router = ServerVerifiedIntentRouter()
        self.schedule_query_parser = ScheduleQueryParser()
        self.schedule_constraint_merger = ScheduleConstraintMerger(self.schedule_query_parser.date_extractor)
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
        self.schedule_formatter = ScheduleAnswerFormatter()
        self.llm = llm_client or LlmRouter().get_provider(self.settings.llm_provider)

    def run(self, request: ChatRequest) -> ChatResponse:
        if self._is_low_information_message(request.message):
            return self._low_information_response()

        parsed_query = self.schedule_query_parser.parse(request.message)
        classified = self.classifier.classify(request.message)
        verified_decision = self.server_verified_router.decide(request.message, parsed_query, classified)

        followup_response = self._schedule_followup_response(request.message, request.session_id)
        if followup_response:
            return followup_response

        if verified_decision.route == VerifiedRoute.CURRENT_DATE:
            return self._current_date_response()
        if verified_decision.route in {
            VerifiedRoute.PERSONAL_SCORE,
            VerifiedRoute.PERSONAL_RISK,
            VerifiedRoute.RECOMMENDED_SCHEDULE,
        }:
            return PersonalContextAnswerService(self.llm).answer(
                user_id=request.user_context.user_id,
                question=request.message,
                intent=self._domain_intent_from_verified_route(verified_decision.route),
            )

        query_type = self._resolve_query_type(request.message, parsed_query)
        if verified_decision.route == VerifiedRoute.RAG:
            query_type = 'SSAFY_OFFICIAL'
        intent = self._intent_from_query_type(query_type)
        filters = self._build_filters(request)

        if verified_decision.route == VerifiedRoute.IMPORTANT_SCHEDULE:
            if 'important' not in verified_decision.filters:
                verified_decision.filters = [*verified_decision.filters, 'important']
            response = self._server_verified_schedule_response(request.message, parsed_query, filters, verified_decision)
            if response:
                return response

        if verified_decision.route == VerifiedRoute.LLM_INTENT:
            response = self._server_verified_schedule_response(request.message, parsed_query, filters, verified_decision)
            if response:
                return response

        retrieval_policy = self.retrieval_policy_router.decide(parsed_query)
        if retrieval_policy.use_schedule_metadata and query_type == parsed_query.query_type:
            schedule_filters = self._schedule_filters(request.message, filters)
            retrieved = self.rag_service.search_schedules(parsed_query, filters=schedule_filters, question=request.message)
            if verified_decision.filters or verified_decision.exclude_filters or verified_decision.rank:
                response = self._server_verified_schedule_response(request.message, parsed_query, schedule_filters, verified_decision, chunks=retrieved)
                if response:
                    return response
            llm_intent_response = self._llm_intent_schedule_response(request.message, parsed_query, schedule_filters, retrieved)
            if llm_intent_response:
                return llm_intent_response
            fallback_chunks = self._schedule_fallback_chunks(request.message, parsed_query, schedule_filters, retrieved)
            return self._schedule_db_response(intent, query_type, parsed_query, retrieved, fallback_chunks=fallback_chunks)

        llm_intent_response = self._llm_intent_general_response(request.message, parsed_query, filters, query_type)
        if llm_intent_response:
            return llm_intent_response

        if verified_decision.route == VerifiedRoute.RAG and verified_decision.reason == 'notice_question':
            rag_result = self.rag_service.search_notices(request.message, parsed_query=parsed_query)
        else:
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
                    'server_verified_route': verified_decision.route,
                    'server_verified_reason': verified_decision.reason,
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
                'server_verified_route': verified_decision.route,
                'server_verified_reason': verified_decision.reason,
            },
        )
    def stream(self, request: ChatRequest):
        response = self.run(request)
        yield f'data: {json.dumps({"type": "delta", "content": response.answer}, ensure_ascii=False)}\n\n'
        yield 'data: {"type": "done"}\n\n'

    def _is_low_information_message(self, message: str) -> bool:
        normalized = (message or '').strip()
        if not normalized:
            return True
        return not re.search(r'[A-Za-z0-9\uac00-\ud7a3]', normalized)

    def _low_information_response(self) -> ChatResponse:
        return ChatResponse(
            answer='\uc870\uae08\ub9cc \ub354 \uad6c\uccb4\uc801\uc73c\ub85c \ubb3c\uc5b4\ubd10 \uc8fc\uc138\uc694. \uc608: \uc624\ub298 \uc77c\uc815, \uacfc\ub77d \uc0c1\ud0dc, \uacf5\uc9c0 \uc694\uc57d\ucc98\ub7fc \uc9c8\ubb38\ud558\uba74 \ubc14\ub85c \ud655\uc778\ud574\ub4dc\ub9b4\uac8c\uc694.',
            intent='general_chat',
            query_type='UNKNOWN',
            answer_policy='LOW_INFORMATION_INPUT',
            references=[],
            usage={'mode': 'low_information_input', 'rag_used': False, 'llm_tokens': 0},
        )

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


    def _schedule_followup_response(self, question: str, session_id: int | None):
        followup_type = self._schedule_followup_type(question)
        if not followup_type:
            return None
        state = self.memory.last_schedule_state(session_id)
        results = list(state.get('results') or [])
        if not results:
            return None

        filtered = results
        if followup_type == 'public_only':
            filtered = [item for item in results if item.get('visibility') == '\uacf5\uc6a9']
        elif followup_type == 'personal_excluded':
            filtered = [item for item in results if item.get('visibility') != '\uac1c\uc778']

        if not filtered:
            answer = '\uc774\uc804 \uc77c\uc815 \uacb0\uacfc\uc5d0\uc11c \uc870\uac74\uc5d0 \ub9de\ub294 \uc77c\uc815\uc740 \uc5c6\uc5b4\uc694.'
            return ChatResponse(
                answer=answer,
                intent='schedule_followup',
                query_type='SCHEDULE_FOLLOWUP',
                answer_policy='SCHEDULE_MEMORY_NO_MATCH',
                references=[],
                usage={'mode': 'schedule_memory', 'retrieved_count': 0},
            )

        selected_index = int(state.get('selected_index') or 0)
        if followup_type == 'next':
            index = selected_index + 1 if selected_index else 2
            if index > len(filtered):
                return self._schedule_followup_no_more_response(filtered, state, followup_type)
            return self._schedule_followup_selected_response(filtered, index, state, followup_type)
        if followup_type == 'nearest':
            return self._schedule_followup_selected_response(filtered, 1, state, followup_type)
        if followup_type == 'end_time':
            index = selected_index or 1
            return self._schedule_followup_end_time_response(filtered, index, state)
        if followup_type in {'public_only', 'personal_excluded'}:
            return self._schedule_followup_list_response(filtered, state, followup_type)
        return None

    def _schedule_followup_type(self, question: str) -> str:
        compact = re.sub(r'\s+', '', question or '').lower()
        if not compact:
            return ''
        if any(word in compact for word in ('\uadf8\uac74\uc5b8\uc81c\ub05d\ub098', '\uc5b8\uc81c\ub05d\ub098', '\uc885\ub8cc', '\ub05d\ub098')):
            return 'end_time'
        if any(word in compact for word in ('\uadf8\ub2e4\uc74c\uac70', '\ub2e4\uc74c\uac70', '\ub2e4\uc74c\uc77c\uc815', '\ub450\ubc88\uc9f8')):
            return 'next'
        if any(word in compact for word in ('\uadf8\uc911\uc81c\uc77c\uac00\uae4c\uc6b4\uac70', '\uc81c\uc77c\uac00\uae4c\uc6b4\uac70', '\uac00\uc7a5\uac00\uae4c\uc6b4\uac70')):
            return 'nearest'
        if any(word in compact for word in ('\uacf5\uc6a9\ub9cc\ub2e4\uc2dc', '\uacf5\uc6a9\ub9cc', '\uacf5\uc2dd\ub9cc')):
            return 'public_only'
        if any(word in compact for word in ('\uac1c\uc778\uc77c\uc815\ube7c\uace0', '\uac1c\uc778\ube7c\uace0', '\ub0b4\uc77c\uc815\ube7c\uace0')):
            return 'personal_excluded'
        return ''


    def _schedule_followup_no_more_response(self, results: list[dict], state: dict, followup_type: str):
        answer = '\uc774\uc804 \uacb0\uacfc\uc5d0\uc11c \ub2e4\uc74c \uc77c\uc815\uc740 \uc5c6\uc5b4\uc694.'
        return ChatResponse(
            answer=answer,
            intent='schedule_followup',
            query_type='SCHEDULE_FOLLOWUP',
            answer_policy='SCHEDULE_MEMORY_NO_MATCH',
            references=[],
            usage=self._schedule_memory_usage(state, results, selected_index=int(state.get('selected_index') or 0), followup_type=followup_type),
        )
    def _schedule_followup_selected_response(self, results: list[dict], index: int, state: dict, followup_type: str):
        index = max(1, min(index, len(results)))
        item = results[index - 1]
        line = self._format_memory_schedule_line(index, item)
        answer = f'\uc774\uc804 \uacb0\uacfc \uae30\uc900\uc73c\ub85c {line}'
        return ChatResponse(
            answer=answer,
            intent='schedule_followup',
            query_type='SCHEDULE_FOLLOWUP',
            answer_policy='SCHEDULE_MEMORY_DIRECT',
            references=[],
            usage=self._schedule_memory_usage(state, results, selected_index=index, followup_type=followup_type),
        )

    def _schedule_followup_end_time_response(self, results: list[dict], index: int, state: dict):
        index = max(1, min(index, len(results)))
        item = results[index - 1]
        end_at = item.get('end_at') or '\uc885\ub8cc \uc2dc\uac04 \uc815\ubcf4\uac00 \uc5c6\uc5b4\uc694'
        title = item.get('title') or '\uc77c\uc815'
        answer = f"{title}\uc740 {end_at}\uc5d0 \ub05d\ub098\uc694."
        return ChatResponse(
            answer=answer,
            intent='schedule_followup',
            query_type='SCHEDULE_FOLLOWUP',
            answer_policy='SCHEDULE_MEMORY_DIRECT',
            references=[],
            usage=self._schedule_memory_usage(state, results, selected_index=index, followup_type='end_time'),
        )

    def _schedule_followup_list_response(self, results: list[dict], state: dict, followup_type: str):
        limit = min(len(results), self.settings.max_schedule_answer_items)
        label = '\uc870\uac74\uc5d0 \ub9de\ub294 \uc77c\uc815'
        lines = [f'\uc774\uc804 \uacb0\uacfc\uc5d0\uc11c {label}\uc740 {len(results)}\uac74\uc774\uc5d0\uc694.']
        for index, item in enumerate(results[:limit], start=1):
            lines.append(self._format_memory_schedule_line(index, item))
        return ChatResponse(
            answer='\n'.join(lines),
            intent='schedule_followup',
            query_type='SCHEDULE_FOLLOWUP',
            answer_policy='SCHEDULE_MEMORY_DIRECT',
            references=[],
            usage=self._schedule_memory_usage(state, results, selected_index=0, followup_type=followup_type),
        )

    def _format_memory_schedule_line(self, index: int, item: dict) -> str:
        time_text = self._format_time_range(item.get('start_at', ''), item.get('end_at', ''))
        suffix = f' {time_text}' if time_text else ''
        title = item.get('title') or '\uc77c\uc815'
        visibility = item.get('visibility', '')
        event_label = item.get('event_label', '')
        return f"{index}. {title} ({visibility}-{event_label}){suffix}"

    def _schedule_memory_usage(self, state: dict, results: list[dict], selected_index: int, followup_type: str) -> dict:
        return {
            'mode': 'schedule_memory',
            'retrieved_count': len(results),
            'followup_type': followup_type,
            'selected_schedule_index': selected_index,
            'last_schedule_query': state.get('query') or {},
            'last_schedule_results': results,
            'llm_tokens': 0,
        }

    def _domain_intent_from_verified_route(self, route: str) -> str:
        mapping = {
            VerifiedRoute.PERSONAL_SCORE: DomainIntent.PERSONAL_SCORE,
            VerifiedRoute.PERSONAL_RISK: DomainIntent.PERSONAL_RISK,
            VerifiedRoute.RECOMMENDED_SCHEDULE: DomainIntent.RECOMMENDED_SCHEDULE,
            VerifiedRoute.IMPORTANT_SCHEDULE: DomainIntent.IMPORTANT_SCHEDULE,
        }
        return mapping.get(route, DomainIntent.GENERAL)

    def _server_verified_schedule_response(self, question: str, parsed_query, filters: dict, decision, chunks=None):
        constraints = self.schedule_constraint_merger.from_decision(parsed_query, decision)
        verified_query = constraints.to_parsed_query()
        schedule_filters = self._filters_from_constraints(filters, constraints)
        if chunks is None:
            chunks = self.rag_service.search_schedules(verified_query, filters=schedule_filters, question='')
        else:
            chunks = list(chunks)
        chunks = self._apply_constraint_filters(chunks, constraints)
        chunks = self._sort_constraint_chunks(chunks, constraints)
        chunks = self._apply_rank(chunks, constraints.rank)
        response = self._schedule_db_response('server_verified_schedule', verified_query.query_type, verified_query, chunks)
        response.answer_policy = f'SERVER_VERIFIED_{response.answer_policy}'
        response.usage.update(
            {
                'server_verified_route': decision.route,
                'server_verified_reason': decision.reason,
                **constraints.as_usage(),
            }
        )
        return response

    def _filters_from_constraints(self, filters: dict, constraints) -> dict:
        schedule_filters = dict(filters)
        if constraints.scope == 'personal':
            schedule_filters['event_type'] = 'personal'
        return schedule_filters

    def _apply_constraint_filters(self, chunks, constraints):
        result = list(chunks or [])
        include_filters = set(constraints.include_filters or [])
        exclude_filters = set(constraints.exclude_filters or [])
        if constraints.scope == 'public':
            result = [chunk for chunk in result if str((chunk.metadata or {}).get('visibility') or '') == 'public']
        if include_filters:
            result = [chunk for chunk in result if self._chunk_matches_all_filters(chunk, include_filters)]
        if exclude_filters:
            result = [chunk for chunk in result if not self._chunk_matches_any_filter(chunk, exclude_filters)]
        return result

    def _sort_constraint_chunks(self, chunks, constraints):
        if 'important' in (constraints.include_filters or []):
            return sorted(chunks or [], key=lambda chunk: (self._chunk_priority(chunk), self._chunk_start(chunk), chunk.title))
        return sorted(chunks or [], key=lambda chunk: (self._chunk_start(chunk), self._chunk_priority(chunk), chunk.title))

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
        classified = self.classifier.classify(question)
        if self._is_official_fact_priority(question, classified):
            return classified
        if parsed_query.query_type != ScheduleQueryType.GENERAL_CHAT:
            return parsed_query.query_type
        return classified

    def _is_official_fact_priority(self, question: str, classified: str) -> bool:
        if classified != 'SSAFY_OFFICIAL':
            return False
        normalized = re.sub(r'\s+', ' ', (question or '').lower()).strip()
        official_markers = (
            '\uae30\uc900', '\ud1b5\uacfc', '\uc870\uac74', '\uaddc\uc815', '\uc810\uc218', '\ud69f\uc218', '\ucc98\ub9ac',
            '\uc218\ub8cc', '\uacfc\ub77d', '\ucd9c\uacb0', '\uacb0\uc11d', '\uc9c0\uac01', '\ud569\uaca9', '\ubd88\ud569\uaca9',
        )
        return any(marker in normalized for marker in official_markers)


    def _llm_intent_general_response(self, question: str, parsed_query, filters: dict, query_type: str):
        if query_type != ScheduleQueryType.GENERAL_CHAT:
            return None
        if not self._has_llm_intent_fallback_marker(question):
            return None
        intent_result = self._classify_llm_intent(question, parsed_query)
        if not intent_result.is_schedule_intent or intent_result.confidence < 0.55:
            return None
        constraints = self.schedule_constraint_merger.from_llm_intent(parsed_query, intent_result)
        verified_query = constraints.to_parsed_query()
        chunks = self.rag_service.search_schedules(verified_query, filters=self._filters_from_constraints(dict(filters), constraints), question='')
        chunks = self._apply_constraint_filters(chunks, constraints)
        chunks = self._sort_constraint_chunks(chunks, constraints)
        chunks = self._apply_rank(chunks, constraints.rank)
        if not chunks:
            return None
        return self._llm_schedule_response(intent_result, verified_query, chunks)

    def _llm_intent_schedule_response(self, question: str, parsed_query, filters: dict, chunks):
        if not self._should_try_llm_intent_for_schedule(question, parsed_query, chunks):
            return None
        intent_result = self._classify_llm_intent(question, parsed_query)
        if not intent_result.is_schedule_intent or intent_result.confidence < 0.55:
            return None
        constraints = self.schedule_constraint_merger.from_llm_intent(parsed_query, intent_result)
        verified_query = constraints.to_parsed_query()
        chunks = self.rag_service.search_schedules(verified_query, filters=self._filters_from_constraints(dict(filters), constraints), question='')
        chunks = self._apply_constraint_filters(chunks, constraints)
        chunks = self._sort_constraint_chunks(chunks, constraints)
        chunks = self._apply_rank(chunks, constraints.rank)
        if not chunks:
            return None
        return self._llm_schedule_response(intent_result, verified_query, chunks)

    def _classify_llm_intent(self, question: str, parsed_query):
        summary = {
            'query_type': getattr(parsed_query, 'query_type', ''),
            'start_date': getattr(parsed_query, 'start_date', ''),
            'end_date': getattr(parsed_query, 'end_date', ''),
            'display_label': getattr(parsed_query, 'display_label', ''),
            'result_limit': getattr(parsed_query, 'result_limit', 0),
        }
        return LLMIntentClassifier(self.llm).classify(question, summary)

    def _should_try_llm_intent_for_schedule(self, question: str, parsed_query, chunks) -> bool:
        if chunks:
            return False
        if not self.settings.llm_provider:
            return False
        if parsed_query and parsed_query.exact_match_required and not self._has_llm_intent_fallback_marker(question):
            return False
        return self._has_llm_intent_fallback_marker(question) or bool(getattr(parsed_query, 'result_limit', 0))

    def _has_llm_intent_fallback_marker(self, question: str) -> bool:
        normalized = re.sub(r'\s+', '', question or '').lower()
        markers = (
            '\uc911\uc694', '\uc6b0\uc120\uc21c\uc704', '\uc870\uc2ec', '\ucd94\ucc9c', '\ubb50\ubd80\ud130',
            '\ubb34\uc5c7\ubd80\ud130', '\uba3c\uc800', '\ube61\ube61', '\ub450\ubc88\uc9f8', '\ub450\ubc88\uc9f8\ub85c',
            '\uccab\ubc88\uc9f8', '\uc138\ubc88\uc9f8', '\ub2e4\uc74c\uc73c\ub85c', '\uc81c\uc77c', '\uac00\uc7a5\uac00\uae4c\uc6b4',
            '\ub9d0\uace0', '\ube7c\uace0', '\uc81c\uc678', '\ud544\uc694', '\uc900\ube44', '\uc911\uc694\uc77c\uc815',
        )
        return any(marker in normalized for marker in markers)

    def _verified_schedule_query(self, parsed_query, intent_result) -> ParsedQuery:
        start_date = intent_result.start_date or getattr(parsed_query, 'start_date', '')
        end_date = intent_result.end_date or getattr(parsed_query, 'end_date', '')
        if not start_date or not end_date:
            start_date, end_date, _exact = self.schedule_query_parser.date_extractor.future_range(30)
        display_label = getattr(parsed_query, 'display_label', '') or self._llm_display_label(intent_result)
        return ParsedQuery(
            query_type=ScheduleQueryType.SCHEDULE_RANGE,
            start_date=start_date,
            end_date=end_date,
            exact_match_required=False,
            display_label=display_label,
            result_limit=0,
        )

    def _llm_display_label(self, intent_result) -> str:
        if intent_result.rank:
            return f'{intent_result.rank}\ubc88\uc9f8\ub85c \uac00\uae4c\uc6b4 \uc77c\uc815'
        if intent_result.intent == 'important_schedule' or 'important' in intent_result.filters:
            return '\uc911\uc694 \uc77c\uc815'
        if intent_result.intent == 'schedule_recommendation':
            return '\ucd94\ucc9c \uc77c\uc815'
        return '\uc694\uccad\ud55c \uc77c\uc815'

    def _apply_llm_schedule_filters(self, chunks, intent_result):
        filters = set(intent_result.filters or [])
        excludes = set(intent_result.exclude_filters or [])
        result = list(chunks or [])
        if filters:
            result = [chunk for chunk in result if self._chunk_matches_any_filter(chunk, filters)]
        if excludes:
            result = [chunk for chunk in result if not self._chunk_matches_any_filter(chunk, excludes)]
        return result

    def _chunk_matches_any_filter(self, chunk, filters: set[str]) -> bool:
        return any(self._chunk_matches_filter(chunk, item) for item in filters)

    def _chunk_matches_all_filters(self, chunk, filters: set[str]) -> bool:
        for group in self._filter_groups(filters):
            if not any(self._chunk_matches_filter(chunk, item) for item in group):
                return False
        return True

    def _filter_groups(self, filters: set[str]) -> list[set[str]]:
        remaining = set(filters or set())
        groups = []
        deadline_group = {'deadline', 'assignment'} & remaining
        if deadline_group:
            groups.append(deadline_group)
            remaining -= deadline_group
        for item in sorted(remaining):
            groups.append({item})
        return groups

    def _chunk_matches_filter(self, chunk, filter_name: str) -> bool:
        metadata = chunk.metadata or {}
        event_type = str(metadata.get('event_type') or '').lower()
        source_type = str(metadata.get('source_type') or '').lower()
        visibility = str(metadata.get('visibility') or '').lower()
        title = str(chunk.title or '').lower()
        text = f'{title} {event_type} {source_type}'
        if filter_name in {'exam', 'subject_exam', 'monthly_exam'}:
            if filter_name == 'subject_exam':
                return event_type == 'exam' and '\uacfc\ubaa9\ud3c9\uac00' in text
            if filter_name == 'monthly_exam':
                return event_type == 'exam' and '\uc6d4\ub9d0\ud3c9\uac00' in text
            return event_type == 'exam' or any(word in text for word in ('\ud3c9\uac00', '\uc2dc\ud5d8', '\uacfc\ubaa9\ud3c9\uac00', '\uc6d4\ub9d0\ud3c9\uac00'))
        if filter_name in {'deadline', 'assignment'}:
            return event_type in {'deadline', 'assignment'} or any(word in text for word in ('\ub9c8\uac10', '\uc81c\ucd9c', '\uacfc\uc81c'))
        if filter_name == 'project':
            return event_type == 'project' or '\ud504\ub85c\uc81d\ud2b8' in text
        if filter_name == 'personal':
            return visibility == 'personal' or event_type == 'personal'
        if filter_name == 'public':
            return visibility == 'public'
        if filter_name == 'holiday':
            return event_type == 'holiday' or source_type == 'holiday'
        if filter_name == 'notice':
            return event_type == 'notice' or source_type == 'notice'
        if filter_name == 'study':
            return event_type == 'study'
        if filter_name == 'online_week':
            return '\uc628\ub77c\uc778\uc704\ud06c' in text or '\uc628\ub77c\uc778 \uc704\ud06c' in text
        if filter_name == 'important':
            return self._is_important_chunk(chunk)
        return False

    def _is_important_chunk(self, chunk) -> bool:
        metadata = chunk.metadata or {}
        event_type = str(metadata.get('event_type') or '').lower()
        source_type = str(metadata.get('source_type') or '').lower()
        title = str(chunk.title or '').lower()
        if self._is_routine_public_chunk(chunk):
            return False
        if bool(metadata.get('is_important')):
            return True
        if event_type in {'exam', 'deadline', 'assignment', 'project'}:
            return True
        if event_type == 'notice' and not any(word in title for word in ('\ud2b9\uac15', '\uba58\ud1a0\ub9c1', '\ub9c8\uac10', '\uc81c\ucd9c', '\ud3c9\uac00', '\uc2dc\ud5d8', '\ud504\ub85c\uc81d\ud2b8')):
            return False
        return any(word in title for word in ('\ud3c9\uac00', '\uc2dc\ud5d8', '\ub9c8\uac10', '\uc81c\ucd9c', '\ud504\ub85c\uc81d\ud2b8', '\ud2b9\uac15', '\uba58\ud1a0\ub9c1'))

    def _is_routine_public_chunk(self, chunk) -> bool:
        metadata = chunk.metadata or {}
        event_type = str(metadata.get('event_type') or '').lower()
        source_type = str(metadata.get('source_type') or '').lower()
        visibility = str(metadata.get('visibility') or '').lower()
        title = str(chunk.title or '').lower().replace(' ', '')
        if source_type == 'holiday' or event_type in {'holiday', 'study'}:
            return True
        if visibility == 'public' and event_type == 'notice' and not any(word in title for word in ('\ud2b9\uac15', '\uba58\ud1a0\ub9c1', '\ub9c8\uac10', '\uc81c\ucd9c', '\ud3c9\uac00', '\uc2dc\ud5d8', '\ud504\ub85c\uc81d\ud2b8')):
            return True
        return '\uc628\ub77c\uc778\uc704\ud06c' in title or 'onlineweek' in title

    def _sort_llm_schedule_chunks(self, chunks, intent_result):
        if not chunks:
            return []
        if intent_result.intent in {'schedule_recommendation', 'important_schedule'} or 'important' in intent_result.filters:
            return sorted(chunks, key=lambda chunk: (self._chunk_priority(chunk), self._chunk_start(chunk), chunk.title))
        return sorted(chunks, key=lambda chunk: (self._chunk_start(chunk), self._chunk_priority(chunk), chunk.title))

    def _chunk_priority(self, chunk) -> int:
        metadata = chunk.metadata or {}
        event_type = str(metadata.get('event_type') or '').lower()
        source_type = str(metadata.get('source_type') or '').lower()
        if bool(metadata.get('is_important')):
            return 0
        if event_type == 'exam':
            return 1
        if event_type in {'deadline', 'assignment'}:
            return 2
        if event_type == 'project':
            return 3
        if event_type == 'personal':
            return 4
        if source_type == 'holiday' or event_type == 'holiday':
            return 9
        return 5

    def _chunk_start(self, chunk) -> str:
        metadata = chunk.metadata or {}
        return str(metadata.get('start_at') or metadata.get('start_date') or '')

    def _apply_rank(self, chunks, rank: int):
        if not rank:
            return chunks
        if len(chunks) < rank:
            return []
        return [chunks[rank - 1]]

    def _llm_schedule_response(self, intent_result, parsed_query, chunks) -> ChatResponse:
        response = self._schedule_db_response(
            intent=intent_result.intent,
            query_type=parsed_query.query_type,
            parsed_query=parsed_query,
            chunks=chunks,
        )
        response.answer_policy = f'LLM_INTENT_{response.answer_policy}'
        response.usage.update(
            {
                'llm_intent': intent_result.intent,
                'llm_intent_confidence': intent_result.confidence,
                'llm_intent_filters': intent_result.filters,
                'llm_intent_exclude_filters': intent_result.exclude_filters,
                'llm_intent_rank': intent_result.rank,
                'llm_intent_reason': intent_result.reason,
            }
        )
        return response

    def _schedule_filters(self, question: str, filters: dict) -> dict:
        schedule_filters = dict(filters)
        if '개인' in question or '내 일정' in question or '내일정' in question:
            schedule_filters['event_type'] = 'personal'
        return schedule_filters

    def _schedule_db_response(self, intent: str, query_type: str, parsed_query, chunks, fallback_chunks=None) -> ChatResponse:
        if not chunks:
            fallback_chunks = fallback_chunks or []
            if fallback_chunks:
                format_result = self.schedule_formatter.prepare(fallback_chunks)
                answer, answer_usage = limit_answer(self._format_schedule_fallback_answer(parsed_query, format_result))
                return ChatResponse(
                    answer=answer,
                    intent=intent,
                    query_type=query_type,
                    answer_policy='SCHEDULE_DB_FALLBACK',
                    references=[],
                    usage={
                        'mode': 'schedule_db_direct',
                        'retrieved_count': 0,
                        'fallback_retrieved_count': len(fallback_chunks),
                        'cleaned_count': format_result.cleaned_count,
                        'duplicate_removed_count': format_result.duplicate_removed_count,
                        'noise_removed_count': format_result.noise_removed_count,
                        'long_event_count': format_result.long_event_count,
                        'llm_tokens': 0,
                        **answer_usage,
                        'extracted_date': self._format_extracted_date(parsed_query),
                    },
                )
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
        format_result = self.schedule_formatter.prepare(chunks)
        if format_result.cleaned_count == 0:
            return ChatResponse(
                answer=self._schedule_no_context_answer(parsed_query),
                intent=intent,
                query_type=query_type,
                answer_policy='SCHEDULE_DB_NO_MATCH',
                references=[],
                usage={
                    'mode': 'schedule_db_direct',
                    'retrieved_count': len(chunks),
                    'cleaned_count': 0,
                    'duplicate_removed_count': format_result.duplicate_removed_count,
                    'noise_removed_count': format_result.noise_removed_count,
                    'long_event_count': 0,
                    'extracted_date': self._format_extracted_date(parsed_query),
                },
            )
        answer, answer_usage = limit_answer(self._format_schedule_answer(parsed_query, format_result))
        memory_payload = self._schedule_memory_payload(parsed_query, format_result)
        return ChatResponse(
            answer=answer,
            intent=intent,
            query_type=query_type,
            answer_policy='SCHEDULE_DB_DIRECT',
            references=[],
            usage={
                'mode': 'schedule_db_direct',
                'retrieved_count': len(chunks),
                'cleaned_count': format_result.cleaned_count,
                'duplicate_removed_count': format_result.duplicate_removed_count,
                'noise_removed_count': format_result.noise_removed_count,
                'long_event_count': format_result.long_event_count,
                'llm_tokens': 0,
                **answer_usage,
                'extracted_date': self._format_extracted_date(parsed_query),
                **memory_payload,
            },
        )


    def _schedule_memory_payload(self, parsed_query, format_result) -> dict:
        events = [*format_result.regular_events, *format_result.long_events]
        return {
            'last_schedule_query': {
                'query_type': getattr(parsed_query, 'query_type', ''),
                'start_date': getattr(parsed_query, 'start_date', ''),
                'end_date': getattr(parsed_query, 'end_date', ''),
                'display_label': getattr(parsed_query, 'display_label', ''),
            },
            'last_schedule_results': [self._schedule_memory_event(event) for event in events[: self.settings.max_schedule_answer_items]],
            'selected_schedule_index': 0,
        }

    def _schedule_memory_event(self, event) -> dict:
        return {
            'title': event.title,
            'start_at': event.start_at,
            'end_at': event.end_at,
            'visibility': event.visibility,
            'event_label': event.event_label,
            'event_type': event.event_type,
            'is_long': event.is_long,
        }

    def _schedule_fallback_chunks(self, question: str, parsed_query, filters: dict, chunks) -> list:
        if chunks:
            return []
        if not parsed_query or not parsed_query.start_date or not parsed_query.end_date:
            return []
        normalized = re.sub(r'\s+', '', question or '').lower()
        deadline_words = ('\ub9c8\uac10', '\uc81c\ucd9c', '\uacfc\uc81c')
        if not any(word in normalized for word in deadline_words):
            return []
        return self.rag_service.search_schedules(parsed_query, filters=filters, question='')

    def _format_schedule_fallback_answer(self, parsed_query, format_result) -> str:
        target = self._format_extracted_date(parsed_query) or '\uc694\uccad\ud55c \uae30\uac04'
        lines = [f'{target}\uc5d0 \ub9c8\uac10\uc73c\ub85c \ubd84\ub958\ub41c \uc77c\uc815\uc740 \uc5c6\uc5b4\uc694.']
        if format_result.cleaned_count:
            lines.append(f'\ub2e4\ub9cc \uac19\uc740 \uae30\uac04\uc758 \uc804\uccb4 \uc77c\uc815\uc740 {format_result.cleaned_count}\uac74 \uc788\uc5b4\uc694.')
            regular_events = format_result.regular_events[: min(self.schedule_formatter.REGULAR_LIMIT, self.settings.max_schedule_answer_items)]
            long_events = format_result.long_events[: min(self.schedule_formatter.LONG_LIMIT, self.settings.max_schedule_answer_items)]
            for index, event in enumerate(regular_events, start=1):
                lines.append(self._format_schedule_event_line(index, event))
            if long_events:
                lines.append('')
                lines.append('[\uc9c4\ud589 \uc911\uc778 \uc7a5\uae30 \uc77c\uc815]')
                for index, event in enumerate(long_events, start=1):
                    lines.append(self._format_schedule_event_line(index, event))
        return '\n'.join(lines)

    def _format_schedule_answer(self, parsed_query, format_result) -> str:
        target = self._format_extracted_date(parsed_query) or '\uc694\uccad\ud55c \uae30\uac04'
        header_target = parsed_query.display_label or f'{target}\uc5d0 \ud655\uc778\ub41c \uc77c\uc815'
        total_count = format_result.cleaned_count
        header = self.response_style.schedule_header(header_target, total_count)
        lines = [header]

        regular_limit = min(self.schedule_formatter.REGULAR_LIMIT, self.settings.max_schedule_answer_items)
        long_limit = min(self.schedule_formatter.LONG_LIMIT, self.settings.max_schedule_answer_items)
        regular_events = format_result.regular_events[:regular_limit]
        long_events = format_result.long_events[:long_limit]

        if regular_events and long_events:
            lines.append('')
            lines.append('[\uc77c\ubc18 \uc77c\uc815]')
        for index, event in enumerate(regular_events, start=1):
            lines.append(self._format_schedule_event_line(index, event))

        if long_events:
            lines.append('')
            lines.append('[\uc9c4\ud589 \uc911\uc778 \uc7a5\uae30 \uc77c\uc815]')
            for index, event in enumerate(long_events, start=1):
                lines.append(self._format_schedule_event_line(index, event))

        visible_count = len(regular_events) + len(long_events)
        if total_count > visible_count:
            lines.append(f'\uc678 {total_count - visible_count}\uac74\uc740 \uce98\ub9b0\ub354\uc5d0\uc11c \ud655\uc778\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.')
        return '\n'.join(lines)

    def _format_schedule_event_line(self, index: int, event) -> str:
        time_text = self._format_time_range(event.start_at, event.end_at)
        suffix = f' {time_text}' if time_text else ''
        return f'{index}. {event.title} ({event.visibility}-{event.event_label}){suffix}'
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
