from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ai_server.classification.query_classifier import QueryClassifier, QueryType
from ai_server.classification.confidence_gate import ConfidenceGate, ConfidenceGateDecision
from ai_server.classification.domain_intent_router import DomainIntent, DomainIntentRouter
from ai_server.classification.llm_intent_classifier import LLMIntentClassifier
from ai_server.classification.rule_parser import RuleParser
from ai_server.classification.server_verified_parser import ServerVerifiedParser
from ai_server.pipelines.chat_pipeline import ChatPipeline
from ai_server.policies.answer_policy import AnswerPolicy, AnswerPolicyRouter
from ai_server.prompts.builder import PromptBuilder
from ai_server.retrieval.policy_router import RetrievalPolicyRouter
from ai_server.retrieval.query_parser import DateExtractor, ScheduleQueryParser, ScheduleQueryType
from ai_server.retrieval.schedule_retrieval import ScheduleRetrievalService
from ai_server.retrievers.evaluator import RetrievalEvaluator
from ai_server.rag.schemas.documents import RetrievedChunk
from ai_server.schemas.chat import ChatRequest, UserContext
from ai_server.validation.result_validator import ResultValidator
from ai_server.vectorstores.faiss_store import FaissVectorStore

from datetime import date, timedelta
import json
import os
from pathlib import Path
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings

from apps.ai.models import AiDocument, AiPipelineRun, AiQualityLog, ChatMessage, ChatSession
from apps.risk.models import EvaluationResult
from ai_server.core.config import get_settings
from ai_server.optimization.response_limit import limit_answer
from ai_server.llm.gemini_provider import GeminiProvider
from apps.ai.services import AiChatService
from schedules.models import ScheduleEvent
from sync.models import RawSsafyData


class RagFallbackPolicyTests(SimpleTestCase):
    def test_query_classifier_types(self):
        classifier = QueryClassifier()
        self.assertEqual(classifier.classify('월말평가 통과 기준 알려줘'), QueryType.SSAFY_OFFICIAL)
        self.assertEqual(classifier.classify('과락 기준이 뭐야?'), QueryType.SSAFY_OFFICIAL)
        self.assertEqual(classifier.classify('Django에서 ForeignKey 뭐야?'), QueryType.GENERAL_TECH)
        self.assertEqual(classifier.classify('과락 맞았는데 너무 힘들다.'), QueryType.GENERAL_ADVICE)
        self.assertEqual(classifier.classify('수업 따라가기 힘들다.'), QueryType.GENERAL_ADVICE)
        self.assertEqual(classifier.classify('공부 방향이 맞는지 모르겠다.'), QueryType.GENERAL_ADVICE)
        self.assertEqual(classifier.classify('배달음식 추천해줘'), QueryType.GENERAL_ADVICE)
    def test_rule_parser_high_confidence_schedule_query(self):
        pipeline = ChatPipeline()
        question = '오늘 일정 알려줘'
        parsed_query = pipeline.schedule_query_parser.parse(question)
        classified = pipeline.classifier.classify(question)
        verified = pipeline.server_verified_router.decide(question, parsed_query, classified)

        result = RuleParser().parse(question, parsed_query, classified, verified)
        gate = ConfidenceGate().decide(result)

        self.assertEqual(result.intent, 'schedule_query')
        self.assertGreaterEqual(result.confidence, 0.75)
        self.assertEqual(gate.decision, ConfidenceGateDecision.DIRECT_RETRIEVAL)

    def test_rule_parser_low_confidence_unknown_query(self):
        pipeline = ChatPipeline()
        question = '그거 어떻게 돼?'
        parsed_query = pipeline.schedule_query_parser.parse(question)
        classified = pipeline.classifier.classify(question)
        verified = pipeline.server_verified_router.decide(question, parsed_query, classified)

        result = RuleParser().parse(question, parsed_query, classified, verified)
        gate = ConfidenceGate().decide(result)

        self.assertEqual(result.intent, 'unknown')
        self.assertLess(result.confidence, 0.45)
        self.assertEqual(gate.decision, ConfidenceGateDecision.LLM_INTENT_CLASSIFIER)

    def test_chat_pipeline_attaches_rule_parser_usage(self):
        request = ChatRequest(
            message='???',
            user_context=UserContext(user_id=1),
        )

        response = ChatPipeline().run(request)

        self.assertIn('rule_parser', response.usage)
        self.assertIn('confidence_gate', response.usage)
        self.assertEqual(response.usage['confidence_gate']['decision'], ConfidenceGateDecision.LLM_INTENT_CLASSIFIER)

    def test_empty_retrieval_official_uses_no_context_policy(self):
        evaluation = RetrievalEvaluator(threshold=0.5).evaluate([])
        decision = AnswerPolicyRouter().decide(QueryType.SSAFY_OFFICIAL, evaluation)
        self.assertTrue(evaluation.insufficient_context)
        self.assertEqual(decision.answer_policy, AnswerPolicy.OFFICIAL_NO_CONTEXT)
        self.assertFalse(decision.use_llm)

    def test_empty_retrieval_general_tech_uses_general_fallback(self):
        evaluation = RetrievalEvaluator(threshold=0.5).evaluate([])
        decision = AnswerPolicyRouter().decide(QueryType.GENERAL_TECH, evaluation)
        self.assertEqual(decision.answer_policy, AnswerPolicy.GENERAL_KNOWLEDGE_FALLBACK)
        self.assertTrue(decision.use_llm)
        self.assertFalse(decision.use_references)

    def test_schedule_query_parser_extracts_exact_date(self):
        parser = ScheduleQueryParser(DateExtractor(today=date(2026, 5, 17)))
        parsed = parser.parse('5월 18일 일정 알려줘')
        self.assertEqual(parsed.query_type, ScheduleQueryType.SCHEDULE_EXACT_DATE)
        self.assertEqual(parsed.start_date, '2026-05-18')
        self.assertTrue(parsed.exact_match_required)

    def test_schedule_query_parser_prioritizes_explicit_korean_year(self):
        parser = ScheduleQueryParser(DateExtractor(today=date(2026, 6, 4)))
        parsed = parser.parse('2099년 1월 1일 일정 알려줘')

        self.assertEqual(parsed.start_date, '2099-01-01')
        self.assertEqual(parsed.end_date, '2099-01-01')

    def test_schedule_query_parser_extracts_relative_date(self):
        parser = ScheduleQueryParser(DateExtractor(today=date(2026, 5, 27)))
        parsed = parser.parse('내일 일정 알려줘')
        self.assertEqual(parsed.query_type, ScheduleQueryType.SCHEDULE_EXACT_DATE)
        self.assertEqual(parsed.start_date, '2026-05-28')
        self.assertTrue(parsed.exact_match_required)

    def test_schedule_query_parser_routes_schedule_without_date_to_upcoming_range(self):
        parser = ScheduleQueryParser(DateExtractor(today=date(2026, 5, 27)))
        parsed = parser.parse('개인 일정 알려줘')
        self.assertEqual(parsed.query_type, ScheduleQueryType.SCHEDULE_RANGE)
        self.assertEqual(parsed.start_date, '2025-05-27')
        self.assertEqual(parsed.end_date, '2027-05-27')
        self.assertFalse(parsed.exact_match_required)

    def test_upcoming_schedule_query_uses_future_range_and_small_limit(self):
        parser = ScheduleQueryParser(DateExtractor(today=date(2026, 6, 4)))
        parsed = parser.parse('가까운 평가 일정 알려줘')

        self.assertEqual(parsed.query_type, ScheduleQueryType.SCHEDULE_RANGE)
        self.assertEqual(parsed.start_date, '2026-06-04')
        self.assertEqual(parsed.end_date, '2027-06-04')
        self.assertEqual(parsed.display_label, '오늘 이후 가까운 일정')
        self.assertEqual(parsed.result_limit, 3)

    def test_closest_schedule_query_returns_single_result(self):
        parser = ScheduleQueryParser(DateExtractor(today=date(2026, 6, 4)))
        parsed = parser.parse('가장 가까운 시험 일정 알려줘')

        self.assertEqual(parsed.start_date, '2026-06-04')
        self.assertEqual(parsed.display_label, '가장 가까운 일정')
        self.assertEqual(parsed.result_limit, 1)

    def test_schedule_policy_disables_semantic_fallback_for_exact_date(self):
        parser = ScheduleQueryParser(DateExtractor(today=date(2026, 5, 17)))
        parsed = parser.parse('5월 18일 일정 알려줘')
        policy = RetrievalPolicyRouter().decide(parsed)
        self.assertTrue(policy.use_schedule_metadata)
        self.assertFalse(policy.allow_semantic_fallback)

    def test_vectorstore_metadata_date_search_does_not_return_other_dates(self):
        index_path = Path(settings.BASE_DIR) / 'var' / 'test' / f'{uuid4().hex}.json'
        try:
            store = FaissVectorStore(path=str(index_path))
            store.upsert([
                {
                    'chunk_id': '1:0',
                    'ai_document_id': 1,
                    'raw_data_id': 1,
                    'title': '5월 월말평가',
                    'content': '5월 24일 월말평가',
                    'document_type': 'EXAM',
                    'metadata': {'start_date': '2026-05-24', 'end_date': '2026-05-24', 'source_type': 'EXAM'},
                    'vector': [1.0],
                }
            ])
            chunks = store.search_by_metadata(start_date='2026-05-18', end_date='2026-05-18', exact=True)
            self.assertEqual(chunks, [])
        finally:
            if index_path.exists():
                index_path.unlink()

    def test_schedule_retrieval_prioritizes_title_keyword_in_wide_range(self):
        class StubVectorStore:
            def search_by_metadata(self, **_kwargs):
                return [
                    RetrievedChunk(
                        chunk_id='1:0',
                        ai_document_id=1,
                        raw_data_id=None,
                        title='5월 18일 실습',
                        content='practice',
                        document_type='SCHEDULE_PRACTICE',
                        metadata={'start_date': '2026-05-18', 'end_date': '2026-05-18'},
                        score=1.0,
                    ),
                    RetrievedChunk(
                        chunk_id='2:0',
                        ai_document_id=2,
                        raw_data_id=None,
                        title='하하하ㅏ핳하ㅏ하핳',
                        content='personal',
                        document_type='SCHEDULE_PERSONAL',
                        metadata={'start_date': '2026-05-24', 'end_date': '2026-05-24'},
                        score=1.0,
                    ),
                ]

        parsed = ScheduleQueryParser(DateExtractor(today=date(2026, 5, 27))).parse('하하하 일정 알려줘')
        chunks = ScheduleRetrievalService(vectorstore=StubVectorStore()).retrieve(parsed, query='하하하 일정 알려줘')
        self.assertEqual(chunks[0].title, '하하하ㅏ핳하ㅏ하핳')

    def test_vectorstore_relative_path_is_project_root_based(self):
        current_directory = Path.cwd()
        nested_directory = Path(settings.BASE_DIR) / 'ai_server'
        try:
            os.chdir(nested_directory)
            store = FaissVectorStore(path='var/test/faiss_relative_path.json')
            self.assertEqual(store.path, Path(settings.BASE_DIR) / 'var' / 'test' / 'faiss_relative_path.json')
        finally:
            os.chdir(current_directory)

    def test_prompt_builder_selects_tech_prompts_without_rag_policy(self):
        result = PromptBuilder().build_messages(
            question='Django에서 ForeignKey 뭐야?',
            intent='general_tech',
            query_type='GENERAL_TECH',
            answer_policy='GENERAL_KNOWLEDGE_FALLBACK',
            insufficient_context=True,
            extracted_date='',
            retrieval_status='NO_RETRIEVED_CHUNKS',
            exact_match=False,
            chunks=[],
            user_context='{}',
            memory_context='',
            fallback_prefix='SSAFY 공식 자료 기준은 아니지만',
        )
        self.assertIn('styles/inssa_voice.md', result.metadata['used_prompt_files'])
        self.assertIn('policies/general_tech_policy.md', result.metadata['used_prompt_files'])
        self.assertNotIn('policies/schedule_policy.md', result.metadata['used_prompt_files'])
        self.assertLessEqual(result.metadata['context_count'], 4)
        self.assertEqual(result.metadata['current_date'], timezone.localdate().isoformat())



class ScheduleDbChatPipelineTests(TestCase):
    def setUp(self):
        self.user_a = get_user_model().objects.create_user(
            username='ai-user-a',
            email='ai-user-a@example.com',
            password='password',
        )
        self.user_b = get_user_model().objects.create_user(
            username='ai-user-b',
            email='ai-user-b@example.com',
            password='password',
        )
        self.start_at = timezone.make_aware(timezone.datetime(2026, 6, 1, 9, 0))
        ScheduleEvent.objects.create(
            title='공식 SSAFY 일정',
            start_at=self.start_at,
            end_at=self.start_at + timedelta(hours=1),
            event_type='notice',
            source_type='notice',
        )
        ScheduleEvent.objects.create(
            title='공휴일 일정',
            start_at=self.start_at,
            end_at=self.start_at + timedelta(hours=1),
            event_type='holiday',
            source_type='holiday',
        )
        ScheduleEvent.objects.create(
            owner=self.user_a,
            title='A 개인 일정',
            start_at=self.start_at,
            end_at=self.start_at + timedelta(hours=1),
            event_type='personal',
            source_type='manual',
        )
        ScheduleEvent.objects.create(
            owner=self.user_b,
            title='B 개인 일정',
            start_at=self.start_at,
            end_at=self.start_at + timedelta(hours=1),
            event_type='personal',
            source_type='manual',
        )

    def _ask(self, user, message='2026-06-01 일정 알려줘'):
        return ChatPipeline().run(
            ChatRequest(
                message=message,
                user_context=UserContext(user_id=user.id, campus='', generation='', track='', risk_level=''),
            )
        )

    def test_schedule_question_returns_public_and_own_personal_events(self):
        response = self._ask(self.user_a)

        self.assertEqual(response.answer_policy, 'SCHEDULE_DB_DIRECT')
        self.assertIn('공식 SSAFY 일정', response.answer)
        self.assertIn('공휴일 일정', response.answer)
        self.assertIn('A 개인 일정', response.answer)
        self.assertNotIn('B 개인 일정', response.answer)

    def test_other_user_only_sees_own_personal_events(self):
        response = self._ask(self.user_b)

        self.assertIn('공식 SSAFY 일정', response.answer)
        self.assertIn('공휴일 일정', response.answer)
        self.assertIn('B 개인 일정', response.answer)
        self.assertNotIn('A 개인 일정', response.answer)

    def test_exact_date_does_not_include_event_ending_at_day_start(self):
        previous_day_start = timezone.make_aware(timezone.datetime(2026, 1, 5, 0, 0))
        target_day_start = timezone.make_aware(timezone.datetime(2026, 1, 6, 9, 0))
        ScheduleEvent.objects.create(
            title='전날 자정 종료 일정',
            start_at=previous_day_start,
            end_at=previous_day_start + timedelta(days=1),
            event_type='notice',
            source_type='notice',
        )
        ScheduleEvent.objects.create(
            title='1월 6일 실제 일정',
            start_at=target_day_start,
            end_at=target_day_start + timedelta(hours=1),
            event_type='notice',
            source_type='notice',
        )

        response = self._ask(self.user_a, message='1월 6일 일정 알려줘')

        self.assertIn('1월 6일 실제 일정', response.answer)
        self.assertNotIn('전날 자정 종료 일정', response.answer)

    def test_week_query_ignores_filler_words_and_returns_public_schedule(self):
        online_week_start = timezone.make_aware(timezone.datetime(2026, 6, 2, 9, 0))
        ScheduleEvent.objects.create(
            title='온라인 위크',
            start_at=online_week_start,
            end_at=online_week_start + timedelta(days=1),
            event_type='study',
            source_type='notice',
        )

        parsed = ScheduleQueryParser(DateExtractor(today=date(2026, 6, 5))).parse('이번주 큰 일정 뭐있니')
        chunks = ScheduleRetrievalService().retrieve(
            parsed,
            filters={'user_id': self.user_a.id},
            query='이번주 큰 일정 뭐있니',
        )

        self.assertEqual(parsed.query_type, ScheduleQueryType.SCHEDULE_RANGE)
        self.assertIn('온라인 위크', [chunk.title for chunk in chunks])

    def test_upcoming_evaluation_returns_only_nearest_future_evaluations(self):
        today = timezone.localdate()
        day_start = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
        ScheduleEvent.objects.create(
            title='이미 지난 과목평가',
            start_at=day_start - timedelta(days=1),
            end_at=day_start - timedelta(days=1) + timedelta(hours=1),
            event_type='exam',
            source_type='notice',
        )
        expected = []
        for index in range(1, 5):
            event = ScheduleEvent.objects.create(
                title=f'다가올 과목평가 {index}',
                start_at=day_start + timedelta(days=index),
                end_at=day_start + timedelta(days=index, hours=1),
                event_type='exam',
                source_type='notice',
            )
            expected.append(event)
        ScheduleEvent.objects.create(
            title='다가올 일반 공지',
            start_at=day_start + timedelta(hours=1),
            end_at=day_start + timedelta(hours=2),
            event_type='notice',
            source_type='notice',
        )

        response = self._ask(self.user_a, message='가까운 평가 일정 알려줘')

        self.assertIn('오늘 이후 가까운 일정은 3건입니다', response.answer)
        for event in expected[:3]:
            self.assertIn(event.title, response.answer)
        self.assertNotIn(expected[3].title, response.answer)
        self.assertNotIn('이미 지난 과목평가', response.answer)
        self.assertNotIn('다가올 일반 공지', response.answer)

    def test_upcoming_evaluation_does_not_fall_back_to_unrelated_schedule(self):
        today = timezone.localdate()
        day_start = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
        ScheduleEvent.objects.create(
            title='가까운 일반 공지',
            start_at=day_start + timedelta(days=1),
            end_at=day_start + timedelta(days=1, hours=1),
            event_type='notice',
            source_type='notice',
        )

        response = self._ask(self.user_a, message='가까운 평가 일정 알려줘')

        self.assertEqual(response.answer_policy, 'SCHEDULE_DB_NO_MATCH')
        self.assertIn('확인해봤지만 오늘 이후 가까운 일정은 없어요', response.answer)
        self.assertNotIn('가까운 일반 공지', response.answer)

    def test_empty_schedule_date_returns_no_confirmed_schedule_message(self):
        response = self._ask(self.user_a, message='2026-06-03 일정 알려줘')

        self.assertEqual(response.answer_policy, 'SCHEDULE_DB_NO_MATCH')
        self.assertIn('확인', response.answer)
        self.assertIn('없', response.answer)

    def test_schedule_answer_lists_at_most_configured_items(self):
        for index in range(10):
            ScheduleEvent.objects.create(
                title=f'추가 중요 일정 {index}',
                start_at=self.start_at,
                end_at=self.start_at + timedelta(hours=1),
                event_type='notice',
                source_type='notice',
            )

        response = self._ask(self.user_a)
        numbered_lines = [line for line in response.answer.splitlines() if line.partition('.')[0].isdigit()]

        self.assertEqual(len(numbered_lines), get_settings().max_schedule_answer_items)
        self.assertIn('캘린더에서 확인할 수 있습니다', response.answer)
        self.assertLessEqual(len(response.answer), get_settings().max_answer_chars)
        self.assertEqual(response.usage['llm_tokens'], 0)

    def test_closest_exam_matches_subject_and_monthly_evaluations(self):
        today = timezone.localdate()
        day_start = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
        closest = ScheduleEvent.objects.create(
            title='과목평가',
            start_at=day_start + timedelta(days=1),
            end_at=day_start + timedelta(days=1, hours=1),
            event_type='exam',
            source_type='notice',
        )
        ScheduleEvent.objects.create(
            title='월말평가',
            start_at=day_start + timedelta(days=3),
            end_at=day_start + timedelta(days=3, hours=1),
            event_type='exam',
            source_type='notice',
        )

        response = self._ask(self.user_a, message='가장 가까운 시험 일정 알려줘')

        self.assertEqual(response.answer_policy, 'SCHEDULE_DB_DIRECT')
        self.assertIn(closest.title, response.answer)
        self.assertIn('가장 가까운 일정은 1건입니다', response.answer)
        self.assertNotIn('월말평가', response.answer)

    def test_current_date_question_uses_korean_server_date_without_llm(self):
        response = self._ask(self.user_a, message='오늘 날짜가 며칠이야?')

        self.assertEqual(response.answer_policy, 'SERVER_DATE_DIRECT')
        self.assertEqual(response.usage['llm_tokens'], 0)
        self.assertIn(str(timezone.localdate().year), response.answer)

    def test_non_schedule_question_keeps_existing_rag_llm_flow(self):
        response = self._ask(self.user_a, message='Django ForeignKey 설명해줘')

        self.assertNotIn(response.answer_policy, {'SCHEDULE_DB_DIRECT', 'SCHEDULE_DB_NO_MATCH'})
class CrawledDataRagIngestionSmokeTests(TestCase):
    def test_crawl_command_creates_raw_events_ai_documents_and_vectors(self):
        index_path = Path(settings.BASE_DIR) / 'var' / 'test' / f'{uuid4().hex}.json'
        try:
            with patch.dict(
                'os.environ',
                {
                    'EMBEDDING_PROVIDER': 'local',
                    'VECTORSTORE_PROVIDER': 'faiss',
                    'VECTORSTORE_PATH': str(index_path),
                },
            ):
                get_settings.cache_clear()
                call_command('crawl_ssafy_notices', '--mode', 'sample')

            self.assertGreater(RawSsafyData.objects.count(), 0)
            self.assertGreater(ScheduleEvent.objects.count(), 0)
            self.assertGreater(AiDocument.objects.filter(sync_raw_data__isnull=False).count(), 0)
            self.assertTrue(index_path.exists())
            self.assertIn('"records"', index_path.read_text(encoding='utf-8'))
        finally:
            get_settings.cache_clear()
            if index_path.exists():
                index_path.unlink()

class AiResponseLimitTests(SimpleTestCase):
    def test_long_answer_is_limited_and_reports_estimated_usage(self):
        answer, usage = limit_answer('긴 답변입니다. ' * 200)

        self.assertLessEqual(len(answer), get_settings().max_answer_chars)
        self.assertTrue(usage['answer_truncated'])
        self.assertGreater(usage['estimated_output_tokens'], 0)

    def test_gemini_usage_is_normalized_for_average_calculation(self):
        usage = GeminiProvider()._normalize_usage({
            'promptTokenCount': 100,
            'candidatesTokenCount': 25,
            'totalTokenCount': 125,
        })

        self.assertEqual(usage, {'prompt_tokens': 100, 'completion_tokens': 25, 'total_tokens': 125})


class LLMIntentClassifierTests(SimpleTestCase):
    class StubLlm:
        def __init__(self, answer):
            self.answer = answer
            self.messages = []

        def complete(self, messages):
            self.messages = messages
            return {'answer': self.answer}

    def test_json_only_classifier_accepts_nested_date_range(self):
        llm = self.StubLlm(
            """
            {
              "intent": "schedule_query",
              "confidence": 0.82,
              "route": "hybrid",
              "data_sources": ["schedule", "rag", "bad_source"],
              "date_range": {
                "type": "this_week",
                "start_date": "2026-06-22",
                "end_date": "2026-06-28"
              },
              "filters": ["important", "exam", "unknown_filter"],
              "exclude_filters": ["personal"],
              "rank": 2,
              "requires_personal_context": false,
              "answer_mode": "direct",
              "reason": "이번 주 중요 시험 일정 질문"
            }
            """
        )

        result = LLMIntentClassifier(llm).classify('이번 주 중요한 시험 일정 알려줘')

        self.assertEqual(result.intent, 'schedule_query')
        self.assertEqual(result.confidence, 0.82)
        self.assertEqual(result.route, 'hybrid')
        self.assertEqual(result.data_sources, ['schedule', 'rag'])
        self.assertEqual(result.date_range_type, 'this_week')
        self.assertEqual(result.start_date, '2026-06-22')
        self.assertEqual(result.end_date, '2026-06-28')
        self.assertEqual(result.filters, ['important', 'exam'])
        self.assertEqual(result.exclude_filters, ['personal'])
        self.assertEqual(result.rank, 2)
        self.assertFalse(result.requires_personal_context)
        self.assertIn('JSON only', llm.messages[0]['content'])

    def test_json_only_classifier_sanitizes_invalid_values(self):
        llm = self.StubLlm(
            '{"intent":"drop_database","confidence":3,"date_range":{"type":"forever","start_date":"tomorrow"},'
            '"route":"shell","data_sources":["schedule","secret"],'
            '"filters":["bad","exam"],"exclude_filters":"personal","rank":999}'
        )

        result = LLMIntentClassifier(llm).classify('애매한 질문')

        self.assertEqual(result.intent, 'unknown')
        self.assertEqual(result.confidence, 1.0)
        self.assertEqual(result.route, 'none')
        self.assertEqual(result.data_sources, ['schedule'])
        self.assertEqual(result.date_range_type, '')
        self.assertEqual(result.start_date, '')
        self.assertEqual(result.filters, ['exam'])
        self.assertEqual(result.exclude_filters, [])
        self.assertEqual(result.rank, 20)

    def test_json_only_classifier_returns_unknown_for_invalid_json(self):
        result = LLMIntentClassifier(self.StubLlm('답변 문장입니다.')).classify('애매한 질문')

        self.assertEqual(result.intent, 'unknown')
        self.assertEqual(result.confidence, 0.0)
        self.assertEqual(result.raw, {})


class ServerVerifiedParserTests(SimpleTestCase):
    def test_corrects_invalid_route_for_schedule_intent(self):
        class StubIntent:
            intent = 'schedule_query'
            confidence = 0.8
            route = 'llm'
            data_sources = ['schedule', 'score', 'rag']

        plan = ServerVerifiedParser().verify(StubIntent())

        self.assertTrue(plan.allowed)
        self.assertEqual(plan.route, 'db')
        self.assertEqual(plan.data_sources, ['schedule', 'rag'])
        self.assertEqual(plan.reason, 'route_corrected')

    def test_blocks_low_confidence_intent(self):
        class StubIntent:
            intent = 'schedule_query'
            confidence = 0.2
            route = 'db'
            data_sources = ['schedule']

        plan = ServerVerifiedParser().verify(StubIntent())

        self.assertFalse(plan.allowed)
        self.assertEqual(plan.route, 'clarify')
        self.assertEqual(plan.data_sources, [])


class ResultValidatorTests(SimpleTestCase):
    def test_schedule_empty_range_requests_llm_intent_retry(self):
        parser = ScheduleQueryParser(DateExtractor(today=date(2026, 6, 22)))
        parsed_query = parser.parse('시험 일정 알려줘')

        result = ResultValidator().validate_schedule([], parsed_query)

        self.assertEqual(result.status, 'empty')
        self.assertTrue(result.should_retry)
        self.assertEqual(result.retry_route, 'llm_intent')

    def test_schedule_exact_empty_does_not_retry(self):
        parser = ScheduleQueryParser(DateExtractor(today=date(2026, 6, 22)))
        parsed_query = parser.parse('2026년 6월 22일 일정 알려줘')

        result = ResultValidator().validate_schedule([], parsed_query)

        self.assertEqual(result.reason, 'exact_date_no_rows')
        self.assertFalse(result.should_retry)


class LoraDatasetTests(SimpleTestCase):
    def test_chat_messages_split_into_prompt_and_output(self):
        from ai_server.finetuning.lora_dataset import split_prompt_and_output

        prompt, output = split_prompt_and_output(
            {
                'messages': [
                    {'role': 'system', 'content': 'You are inSSa.'},
                    {'role': 'user', 'content': 'What is due?'},
                    {'role': 'assistant', 'content': 'The project is due Friday.'},
                ],
            }
        )

        self.assertIn('<|system|>', prompt)
        self.assertIn('<|user|>', prompt)
        self.assertTrue(prompt.endswith('<|assistant|>\n'))
        self.assertEqual(output, 'The project is due Friday.')

    def test_instruction_records_still_work(self):
        from ai_server.finetuning.lora_dataset import split_prompt_and_output

        prompt, output = split_prompt_and_output(
            {
                'instruction': 'Summarize this notice.',
                'input': 'Deadline is Friday.',
                'output': 'Submit by Friday.',
            }
        )

        self.assertIn('### Instruction:', prompt)
        self.assertIn('Deadline is Friday.', prompt)
        self.assertEqual(output, 'Submit by Friday.')


class AiUsagePersistenceTests(TestCase):
    def test_assistant_usage_is_saved_for_future_average_calculation(self):
        user = get_user_model().objects.create_user(
            username='usage-user',
            email='usage@example.com',
            password='password',
        )
        AiChatService()._persist_chat(
            user=user,
            message='짧게 답해줘',
            payload={
                'answer': '짧은 답변입니다.',
                'references': [],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120},
            },
        )

        saved = ChatMessage.objects.get(role=ChatMessage.ROLE_ASSISTANT)
        self.assertEqual(saved.usage_json['completion_tokens'], 20)
        self.assertEqual(saved.usage_json['total_tokens'], 120)

    def test_pipeline_run_is_saved_with_answer_metadata(self):
        user = get_user_model().objects.create_user(
            username='pipeline-user',
            email='pipeline@example.com',
            password='password',
        )

        class StubClient:
            def chat(self, **_kwargs):
                return {
                    'answer': '답변입니다.',
                    'references': [{'document_id': 1, 'title': '공지', 'score': 0.9}],
                    'intent': 'schedule',
                    'query_type': 'SCHEDULE_RANGE',
                    'answer_policy': 'RAG_GROUNDED',
                    'usage': {
                        'mode': 'gemini',
                        'retrieved_count': 2,
                        'retrieval': {'status': 'ok'},
                    },
                }

        with patch.object(settings, 'AI_SERVER_ENABLED', True):
            payload = AiChatService(ai_server_client=StubClient()).answer(user=user, message='오늘 일정 알려줘')

        self.assertIn('session_id', payload)
        self.assertEqual(ChatMessage.objects.filter(session_id=payload['session_id']).count(), 2)
        run = AiPipelineRun.objects.get()
        self.assertEqual(run.user, user)
        self.assertEqual(run.question, '오늘 일정 알려줘')
        self.assertEqual(run.answer, '답변입니다.')
        self.assertEqual(run.intent, 'schedule')
        self.assertEqual(run.query_type, 'SCHEDULE_RANGE')
        self.assertEqual(run.answer_policy, 'RAG_GROUNDED')
        self.assertEqual(run.references_json[0]['title'], '공지')
        self.assertEqual(run.retrieved_context_json['retrieved_count'], 2)
        self.assertTrue(run.is_success)
        quality_log = AiQualityLog.objects.get(pipeline_run=run)
        self.assertEqual(quality_log.user, user)
        self.assertEqual(quality_log.evaluator, 'auto')
        self.assertGreater(quality_log.quality_score, 0)
        self.assertEqual(quality_log.metrics_json['retrieved_count'], 2)

    def test_pipeline_run_marks_error_payload_as_failure(self):
        user = get_user_model().objects.create_user(
            username='pipeline-error-user',
            email='pipeline-error@example.com',
            password='password',
        )

        class StubClient:
            def chat(self, **_kwargs):
                return {
                    'answer': 'AI 서버 연결 실패',
                    'references': [],
                    'intent': 'error',
                    'query_type': 'UNKNOWN',
                    'answer_policy': 'ERROR',
                    'usage': {'mode': 'ai_server_connection_error'},
                }

        with patch.object(settings, 'AI_SERVER_ENABLED', True):
            AiChatService(ai_server_client=StubClient()).answer(user=user, message='안녕')

        run = AiPipelineRun.objects.get()
        self.assertFalse(run.is_success)
        self.assertEqual(run.failure_type, 'ai_server_connection_error')
        quality_log = AiQualityLog.objects.get(pipeline_run=run)
        self.assertEqual(quality_log.quality_score, 0.1)

    def test_quality_metrics_api_returns_summary_timeseries_and_intent_breakdown(self):
        user = get_user_model().objects.create_user(
            username='quality-user',
            email='quality@example.com',
            password='password',
        )
        other_user = get_user_model().objects.create_user(
            username='quality-other-user',
            email='quality-other@example.com',
            password='password',
        )
        run = AiPipelineRun.objects.create(
            user=user,
            question='오늘 일정 알려줘',
            answer='오늘 일정은 1건입니다.',
            intent='schedule_query',
            query_type='SCHEDULE_RANGE',
            answer_policy='SCHEDULE_DB_DIRECT',
            is_success=True,
            retrieved_context_json={'retrieved_count': 1, 'reference_count': 0},
            usage_json={'mode': 'schedule_db_direct'},
            latency_ms=120,
        )
        AiQualityLog.objects.create(
            pipeline_run=run,
            user=user,
            quality_score=0.95,
            latency_ms=120,
            retrieved_count=1,
            is_fallback=False,
            is_error=False,
            is_no_context=False,
            metrics_json={
                'latency_ms': 120,
                'retrieved_count': 1,
                'is_fallback': False,
                'is_error': False,
                'is_no_context': False,
            },
        )
        other_run = AiPipelineRun.objects.create(
            user=other_user,
            question='안녕',
            answer='안녕하세요.',
            intent='general_chat',
            answer_policy='ERROR',
            is_success=False,
            failure_type='ai_server_connection_error',
        )
        AiQualityLog.objects.create(
            pipeline_run=other_run,
            user=other_user,
            quality_score=0.1,
            latency_ms=0,
            retrieved_count=0,
            is_fallback=False,
            is_error=True,
            is_no_context=False,
            metrics_json={'latency_ms': 0, 'retrieved_count': 0, 'is_fallback': False, 'is_error': True},
        )

        self.client.force_login(user)
        summary = self.client.get('/api/v1/ai/quality/summary?days=30').json()['data']
        self.assertEqual(summary['total_runs'], 1)
        self.assertEqual(summary['avg_quality_score'], 0.95)
        self.assertEqual(summary['success_rate'], 1.0)

        timeseries = self.client.get('/api/v1/ai/quality/timeseries?days=30').json()['data']
        self.assertEqual(timeseries['items'][0]['total_runs'], 1)

        by_intent = self.client.get('/api/v1/ai/quality/by-intent?days=30').json()['data']
        self.assertEqual(by_intent['items'][0]['intent'], 'schedule_query')

    def test_lora_dataset_export_includes_notice_conversation_and_intent_samples(self):
        user = get_user_model().objects.create_user(
            username='lora-export-user',
            email='lora-export@example.com',
            password='password',
        )
        AiDocument.objects.create(
            title='SSAFY notice',
            content='Project deadline is Friday. Submit your repository URL.',
            document_type='NOTICE',
            metadata_json={'date': '2026-06-22'},
        )
        session = ChatSession.objects.create(user=user, title='LoRA sample')
        ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_USER, content='What is due this week?')
        ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_ASSISTANT, content='The project deadline is Friday.')
        AiPipelineRun.objects.create(
            user=user,
            session=session,
            question='Show me this week schedule',
            answer='You have one deadline this week.',
            intent='schedule_query',
            query_type='SCHEDULE_RANGE',
            answer_policy='SCHEDULE_DB_DIRECT',
            usage_json={
                'verified_route': 'db',
                'verified_data_sources': ['schedule'],
                'rule_parser': {'confidence': 0.91},
                'confidence_gate': {'decision': 'direct_retrieval'},
            },
        )

        output = Path(settings.BASE_DIR) / 'tmp' / f'lora_export_{uuid4().hex}.jsonl'
        try:
            call_command(
                'export_lora_dataset',
                output=str(output),
                limit=3,
                include='notices,conversation,intent',
                verbosity=0,
            )
            records = [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines() if line.strip()]
        finally:
            output.unlink(missing_ok=True)

        task_types = {record['metadata']['task_type'] for record in records}
        self.assertIn('notice_summary', task_types)
        self.assertIn('conversation_context_answer', task_types)
        self.assertIn('intent_json', task_types)
        for record in records:
            self.assertEqual([message['role'] for message in record['messages']], ['system', 'user', 'assistant'])
            self.assertTrue(record['messages'][0]['content'])
            self.assertTrue(record['messages'][1]['content'])
            self.assertTrue(record['messages'][2]['content'])

    def test_lora_dataset_export_includes_mentor_advice_samples(self):
        AiDocument.objects.create(
            title='What project should I build?',
            content='Mentor story about choosing a project by finding weak points and practicing execution.',
            document_type='SYNC_MENTORING_NOTICE',
            metadata_json={'source_type': 'mentoring_notice'},
        )

        output = Path(settings.BASE_DIR) / 'tmp' / f'lora_mentor_export_{uuid4().hex}.jsonl'
        try:
            call_command(
                'export_lora_dataset',
                output=str(output),
                limit=1,
                include='mentor_advice',
                verbosity=0,
            )
            records = [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines() if line.strip()]
        finally:
            output.unlink(missing_ok=True)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record['metadata']['task_type'], 'mentor_advice')
        self.assertEqual(record['metadata']['source_type'], 'mentoring_notice')
        self.assertEqual([message['role'] for message in record['messages']], ['system', 'user', 'assistant'])
        self.assertIn('멘토', record['messages'][1]['content'])
        self.assertIn('멘토 글', record['messages'][2]['content'])




    def test_lora_dataset_export_skips_error_conversation_answers(self):
        user = get_user_model().objects.create_user(
            username='lora-error-filter-user',
            email='lora-error-filter@example.com',
            password='password',
        )
        session = ChatSession.objects.create(user=user, title='Error sample')
        ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_USER, content='hello')
        ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_ASSISTANT, content='AI 답변을 생성하지 못했어요. 잠시 후 다시 시도해 주세요.')

        output = Path(settings.BASE_DIR) / 'tmp' / f'lora_error_filter_{uuid4().hex}.jsonl'
        try:
            call_command(
                'export_lora_dataset',
                output=str(output),
                limit=5,
                include='conversation',
                verbosity=0,
            )
            records = [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines() if line.strip()]
        finally:
            output.unlink(missing_ok=True)

        self.assertEqual(records, [])

    def test_lora_dataset_validator_reports_task_types_and_pii_warnings(self):
        output = Path(settings.BASE_DIR) / 'tmp' / f'lora_validate_{uuid4().hex}.jsonl'
        record = {
            'messages': [
                {'role': 'system', 'content': 'You are inSSa.'},
                {'role': 'user', 'content': 'Please review me@example.com'},
                {'role': 'assistant', 'content': 'I will avoid storing personal data.'},
            ],
            'metadata': {'task_type': 'mentor_advice'},
        }
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(record) + '\n', encoding='utf-8')
            stdout = StringIO()
            call_command('validate_lora_dataset', input=str(output), stdout=stdout)
        finally:
            output.unlink(missing_ok=True)

        value = stdout.getvalue()
        self.assertIn('total_count=1', value)
        self.assertIn('mentor_advice:1', value)
        self.assertIn('email_like:1', value)
        self.assertIn('blocking_issue_count=0', value)

    def test_lora_dataset_split_preserves_records(self):
        source = Path(settings.BASE_DIR) / 'tmp' / f'lora_split_source_{uuid4().hex}.jsonl'
        train_output = Path(settings.BASE_DIR) / 'tmp' / f'lora_split_train_{uuid4().hex}.jsonl'
        eval_output = Path(settings.BASE_DIR) / 'tmp' / f'lora_split_eval_{uuid4().hex}.jsonl'
        records = []
        for index in range(10):
            records.append(
                {
                    'messages': [
                        {'role': 'system', 'content': 'system'},
                        {'role': 'user', 'content': f'user {index}'},
                        {'role': 'assistant', 'content': f'assistant {index}'},
                    ],
                    'metadata': {'task_type': 'mentor_advice' if index < 6 else 'notice_summary'},
                }
            )
        try:
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(''.join(json.dumps(record) + '\n' for record in records), encoding='utf-8')
            call_command(
                'split_lora_dataset',
                input=str(source),
                train_output=str(train_output),
                eval_output=str(eval_output),
                eval_ratio=0.2,
                seed=7,
                verbosity=0,
            )
            train_records = [json.loads(line) for line in train_output.read_text(encoding='utf-8').splitlines()]
            eval_records = [json.loads(line) for line in eval_output.read_text(encoding='utf-8').splitlines()]
        finally:
            source.unlink(missing_ok=True)
            train_output.unlink(missing_ok=True)
            eval_output.unlink(missing_ok=True)

        self.assertEqual(len(train_records) + len(eval_records), 10)
        self.assertGreaterEqual(len(eval_records), 2)
        self.assertTrue(all(record['messages'][0]['role'] == 'system' for record in train_records + eval_records))


class DomainIntentRouterTests(SimpleTestCase):
    def setUp(self):
        self.router = DomainIntentRouter()

    def test_score_and_risk_expression_variants_are_structured(self):
        self.assertEqual(self.router.route('내 알고리즘 점수가 얼마야?').intent, DomainIntent.PERSONAL_SCORE)
        self.assertEqual(self.router.route('지금 과락 몇번 했어?').intent, DomainIntent.PERSONAL_SCORE)
        self.assertEqual(self.router.route('내 수료 상태 괜찮아?').intent, DomainIntent.PERSONAL_RISK)

    def test_recommendation_and_important_schedule_variants_are_structured(self):
        self.assertEqual(self.router.route('지금 뭐부터 준비해야 해?').intent, DomainIntent.RECOMMENDED_SCHEDULE)
        self.assertEqual(self.router.route('최근 중요일정 보여줘').intent, DomainIntent.IMPORTANT_SCHEDULE)
        self.assertEqual(self.router.route('급한 할 일 있어?').intent, DomainIntent.IMPORTANT_SCHEDULE)
        self.assertEqual(self.router.route('요즘 긴급하게 볼 스케줄 있어?').intent, DomainIntent.IMPORTANT_SCHEDULE)
        self.assertEqual(self.router.route('먼저 공부할 것을 추천해줘').intent, DomainIntent.RECOMMENDED_SCHEDULE)

    def test_official_pass_criteria_is_not_mistaken_for_personal_score(self):
        self.assertEqual(self.router.route('월말평가 통과 기준 알려줘').intent, DomainIntent.GENERAL)


class PersonalContextChatPipelineTests(TestCase):
    class RecordingLlm:
        def __init__(self, answer='개인 DB 근거로 요약한 답변입니다.'):
            self.answer = answer
            self.messages = []

        def complete(self, messages, **_kwargs):
            self.messages.append(messages)
            return {
                'answer': self.answer,
                'usage': {'prompt_tokens': 120, 'completion_tokens': 30, 'total_tokens': 150},
            }

    def setUp(self):
        User = get_user_model()
        self.user_a = User.objects.create_user(username='context-a', email='context-a@example.com', password='password')
        self.user_b = User.objects.create_user(username='context-b', email='context-b@example.com', password='password')
        self.llm = self.RecordingLlm()
        self.pipeline = ChatPipeline()
        self.pipeline.llm = self.llm

    def _ask(self, user, message):
        return self.pipeline.run(ChatRequest(message=message, user_context=UserContext(user_id=user.id)))

    def test_personal_score_question_sends_only_current_users_scores_to_llm(self):
        EvaluationResult.objects.create(
            user=self.user_a,
            evaluation_type=EvaluationResult.TYPE_SUBJECT,
            round_number=1,
            subject_name='알고리즘',
            score=55,
            status=EvaluationResult.STATUS_FAIL,
        )
        EvaluationResult.objects.create(
            user=self.user_b,
            evaluation_type=EvaluationResult.TYPE_SUBJECT,
            round_number=1,
            subject_name='비밀과목',
            score=99,
            status=EvaluationResult.STATUS_PASS,
        )

        response = self._ask(self.user_a, '내 알고리즘 점수가 얼마야?')
        sent_context = self.llm.messages[-1][1]['content']

        self.assertEqual(response.answer_policy, 'PERSONAL_CONTEXT_LLM')
        self.assertFalse(response.usage['rag_used'])
        self.assertIn('알고리즘', sent_context)
        self.assertIn('55.00', sent_context)
        self.assertNotIn('비밀과목', sent_context)
        self.assertNotIn('99.00', sent_context)

    def test_personal_score_without_records_returns_no_data_without_llm(self):
        response = self._ask(self.user_a, '내 성적 보여줘')

        self.assertEqual(response.answer_policy, 'PERSONAL_CONTEXT_NO_DATA')
        self.assertEqual(response.usage['llm_tokens'], 0)
        self.assertEqual(self.llm.messages, [])

    def test_risk_question_uses_server_calculated_dashboard_context(self):
        response = self._ask(self.user_a, '내 수료 상태 괜찮아?')
        sent_context = self.llm.messages[-1][1]['content']

        self.assertEqual(response.query_type, DomainIntent.PERSONAL_RISK)
        self.assertIn('status_details', sent_context)
        self.assertIn('evaluation_summary', sent_context)

    def test_recent_important_schedule_uses_schedule_db_important_filter(self):
        start_date, _end_date, _exact = self.pipeline.schedule_query_parser.date_extractor.future_range(30)
        starts_at = timezone.make_aware(timezone.datetime.fromisoformat(start_date)) + timedelta(days=1)
        own = ScheduleEvent.objects.create(
            owner=self.user_a,
            title='\uc911\uc694 \uac1c\uc778 \uc77c\uc815',
            start_at=starts_at,
            end_at=starts_at + timedelta(hours=1),
            event_type='personal',
            source_type='manual',
            metadata_json={'is_important': True},
        )
        ScheduleEvent.objects.create(
            owner=self.user_b,
            title='\uc228\uae40 \uc911\uc694 \uc77c\uc815',
            start_at=starts_at,
            end_at=starts_at + timedelta(hours=1),
            event_type='personal',
            source_type='manual',
            metadata_json={'is_important': True},
        )
        ScheduleEvent.objects.create(
            owner=self.user_a,
            title='\uc77c\ubc18 \uac1c\uc778 \uc77c\uc815',
            start_at=starts_at,
            end_at=starts_at + timedelta(hours=1),
            event_type='personal',
            source_type='manual',
            metadata_json={'is_important': False},
        )

        response = self._ask(self.user_a, '\ucd5c\uadfc \uc911\uc694\uc77c\uc815 \ubcf4\uc5ec\uc918')

        self.assertEqual(response.query_type, 'SCHEDULE_RANGE')
        self.assertEqual(response.answer_policy, 'SERVER_VERIFIED_SCHEDULE_DB_DIRECT')
        self.assertIn('important', response.usage['constraint_include_filters'])
        self.assertIn(own.title, response.answer)
        self.assertNotIn('\uc228\uae40 \uc911\uc694 \uc77c\uc815', response.answer)
        self.assertNotIn('\uc77c\ubc18 \uac1c\uc778 \uc77c\uc815', response.answer)
        self.assertEqual(self.llm.messages, [])

    def test_recommendation_question_uses_recommended_schedule_context(self):
        now = timezone.now()
        ScheduleEvent.objects.create(
            title='과목평가',
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, hours=1),
            event_type='exam',
            source_type='notice',
        )

        response = self._ask(self.user_a, '지금 뭐부터 준비해야 해?')
        sent_context = self.llm.messages[-1][1]['content']

        self.assertEqual(response.query_type, DomainIntent.RECOMMENDED_SCHEDULE)
        self.assertIn('recommended_schedules', sent_context)
        self.assertIn('과목평가', sent_context)


class PipelineResultValidatorUsageTests(TestCase):
    def test_schedule_no_match_attaches_result_validator_usage(self):
        pipeline = ChatPipeline()
        request = ChatRequest(message='2026-06-03 \uc77c\uc815 \uc54c\ub824\uc918', user_context=UserContext(user_id=1))

        response = pipeline.run(request)

        self.assertIn('result_validator_status', response.usage)
        self.assertEqual(response.usage['result_validator_status'], 'empty')
        self.assertFalse(response.usage['result_validator_should_retry'])


class AiServerStandaloneImportTests(SimpleTestCase):
    def test_personal_context_module_does_not_import_django_models_at_module_load(self):
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, '-c', "from ai_server.main import app; print(app.title)"],
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
            timeout=20,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('INSSA AI Server', result.stdout)

class UnifiedResponseStyleTests(SimpleTestCase):
    def test_all_llm_prompt_categories_use_same_voice_file(self):
        selector = PromptBuilder().selector
        cases = [
            (QueryType.GENERAL_TECH, 'GENERAL_KNOWLEDGE_FALLBACK', True),
            (QueryType.GENERAL_ADVICE, 'GENERAL_ADVICE_FALLBACK', True),
            (QueryType.SSAFY_OFFICIAL, 'RAG_GROUNDED', False),
            (ScheduleQueryType.SCHEDULE_RANGE, 'RAG_GROUNDED', False),
            (QueryType.UNKNOWN, 'CAUTIOUS_FALLBACK', True),
        ]

        for query_type, policy, insufficient in cases:
            with self.subTest(query_type=query_type):
                selected = selector.select(query_type, policy, insufficient)
                self.assertEqual(selected['styles'], ['styles/inssa_voice.md'])

    def test_direct_response_style_uses_same_calm_opening(self):
        from ai_server.prompts.response_style import ResponseStyle

        style = ResponseStyle()
        self.assertTrue(style.current_date('2026년 6월 4일 목요일').startswith('확인해봤어요.'))
        self.assertTrue(style.schedule_header('가장 가까운 일정', 1).startswith('확인해봤어요.'))
        self.assertTrue(style.schedule_no_data('가장 가까운 일정').startswith('확인해봤지만'))

    def test_personal_context_prompt_contains_unified_voice(self):
        from ai_server.classification.personal_context_service import PersonalContextAnswerService

        service = PersonalContextAnswerService(llm=None)
        messages = service._messages('내 점수 알려줘', DomainIntent.PERSONAL_SCORE, {'records': []})

        self.assertIn('INSSA Unified Voice', messages[0]['content'])
        self.assertIn('친근하고 차분한 존댓말', messages[0]['content'])



class AIServiceRefactorContractTests(SimpleTestCase):
    class MockLLMClient:
        def complete(self, messages, **_kwargs):
            return {'answer': 'mock 모델 답변', 'usage': {'mode': 'mock', 'total_tokens': 10}}

        def stream(self, messages, **_kwargs):
            yield 'mock 모델 답변'

    def test_pipeline_accepts_mock_llm_client_and_preserves_response_contract(self):
        from ai_server.rag.service import RAGService

        class EmptyRetriever:
            def retrieve(self, query, filters=None):
                return []

        pipeline = ChatPipeline(
            rag_service=RAGService(retriever=EmptyRetriever()),
            llm_client=self.MockLLMClient(),
        )
        response = pipeline.run(ChatRequest(message='안녕', user_context=UserContext(user_id=1)))
        payload = response.model_dump()

        self.assertEqual(payload['answer'], 'mock 모델 답변')
        self.assertIn('references', payload)
        self.assertIn('intent', payload)
        self.assertIn('query_type', payload)
        self.assertIn('answer_policy', payload)
        self.assertIn('usage', payload)

    def test_greeting_uses_server_direct_response_without_llm(self):
        pipeline = ChatPipeline(llm_client=self.MockLLMClient())

        response = pipeline.run(ChatRequest(message='안녕', user_context=UserContext(user_id=1)))

        self.assertEqual(response.query_type, 'GREETING')
        self.assertEqual(response.answer_policy, 'SERVER_GREETING_DIRECT')
        self.assertEqual(response.usage['mode'], 'server_greeting_direct')

    def test_llm_route_dispatcher_can_use_direct_llm_route(self):
        pipeline = ChatPipeline(llm_client=self.MockLLMClient())

        class Intent:
            intent = 'general_chat'
            confidence = 0.8

        class Plan:
            route = 'llm'
            data_sources = []

            def as_usage(self):
                return {
                    'verified_route': self.route,
                    'verified_data_sources': self.data_sources,
                    'verified_allowed': True,
                }

        response = pipeline._dispatch_llm_intent_route(
            ChatRequest(message='가볍게 조언해줘', user_context=UserContext(user_id=1)),
            pipeline.schedule_query_parser.parse('가볍게 조언해줘'),
            {},
            Intent(),
            Plan(),
        )

        self.assertTrue(response.answer)
        self.assertEqual(response.answer_policy, 'LLM_INTENT_DIRECT')
        self.assertEqual(response.usage['verified_route'], 'llm')

    def test_llm_route_dispatcher_can_use_clarify_route(self):
        pipeline = ChatPipeline(llm_client=self.MockLLMClient())

        class Intent:
            intent = 'unknown'
            confidence = 0.2

        class Plan:
            route = 'clarify'
            data_sources = []

            def as_usage(self):
                return {
                    'verified_route': self.route,
                    'verified_data_sources': self.data_sources,
                    'verified_allowed': False,
                }

        response = pipeline._dispatch_llm_intent_route(
            ChatRequest(message='그거 알려줘', user_context=UserContext(user_id=1)),
            pipeline.schedule_query_parser.parse('그거 알려줘'),
            {},
            Intent(),
            Plan(),
        )

        self.assertEqual(response.answer_policy, 'LLM_INTENT_CLARIFY')
        self.assertEqual(response.usage['verified_route'], 'clarify')

    def test_schedule_empty_result_retries_with_llm_intent(self):
        from ai_server.rag.service import RAGService

        class JsonLLMClient:
            def complete(self, messages, **_kwargs):
                return {
                    'answer': (
                        '{"intent":"schedule_query","confidence":0.8,"route":"db",'
                        '"data_sources":["schedule"],"date_range":{"start_date":"2026-06-22","end_date":"2026-07-22"},'
                        '"filters":["exam"]}'
                    ),
                    'usage': {'mode': 'mock'},
                }

        class ScheduleRetrieval:
            def __init__(self):
                self.calls = []

            def retrieve(self, parsed_query, filters=None, query=''):
                self.calls.append((parsed_query, filters or {}, query))
                if len(self.calls) == 1:
                    return []
                return [
                    RetrievedChunk(
                        chunk_id='schedule:1',
                        ai_document_id=1,
                        raw_data_id=None,
                        title='Exam Retry',
                        content='Exam Retry',
                        document_type='SCHEDULE',
                        metadata={
                            'event_type': 'exam',
                            'visibility': 'public',
                            'start_at': '2026-06-25 09:00',
                            'end_at': '2026-06-25 10:00',
                        },
                        score=1.0,
                    )
                ]

        schedule_retrieval = ScheduleRetrieval()
        pipeline = ChatPipeline(
            rag_service=RAGService(schedule_retrieval=schedule_retrieval),
            llm_client=JsonLLMClient(),
        )

        response = pipeline.run(
            ChatRequest(message='\uc77c\uc815 \uc54c\ub824\uc918', user_context=UserContext(user_id=1))
        )

        self.assertEqual(len(schedule_retrieval.calls), 2)
        self.assertEqual(response.answer_policy, 'LLM_INTENT_SCHEDULE_DB_DIRECT')
        self.assertTrue(response.usage['result_validator_retry_executed'])
        self.assertEqual(response.usage['result_validator_retry_status'], 'enough')

    def test_rag_insufficient_context_retries_with_llm_intent(self):
        from ai_server.rag.service import RAGService

        chunk = RetrievedChunk(
            chunk_id='doc:retry',
            ai_document_id=9,
            raw_data_id=4,
            title='Retry Notice',
            content='공지 재검색 결과입니다.',
            document_type='NOTICE',
            metadata={'source_type': 'notice'},
            score=1.0,
        )

        class RetryRetriever:
            def __init__(self):
                self.calls = 0

            def retrieve(self, query, filters=None):
                self.calls += 1
                if self.calls == 1:
                    return []
                return [chunk]

        class JsonThenAnswerLLM:
            def __init__(self):
                self.calls = 0

            def complete(self, messages, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return {
                        'answer': (
                            '{"intent":"official_notice_query","confidence":0.82,'
                            '"route":"rag","data_sources":["rag"]}'
                        ),
                        'usage': {'mode': 'mock_intent'},
                    }
                return {'answer': 'grounded retry answer', 'usage': {'mode': 'mock_answer'}}

        retriever = RetryRetriever()
        llm = JsonThenAnswerLLM()
        pipeline = ChatPipeline(
            rag_service=RAGService(retriever=retriever),
            llm_client=llm,
        )

        response = pipeline.run(
            ChatRequest(message='\uae30\uc900 \uc54c\ub824\uc918', user_context=UserContext(user_id=1))
        )

        self.assertEqual(response.answer, 'grounded retry answer')
        self.assertEqual(response.answer_policy, 'LLM_INTENT_RETRY_RAG_GROUNDED')
        self.assertTrue(response.usage['result_validator_retry_executed'])
        self.assertEqual(response.usage['result_validator_retry_status'], 'enough')
        self.assertEqual(response.usage['verified_route'], 'rag')

    def test_rag_service_keeps_references_for_grounded_answer(self):
        from ai_server.rag.service import RAGService

        chunk = RetrievedChunk(
            chunk_id='doc:1',
            ai_document_id=7,
            raw_data_id=3,
            title='SSAFY 공지',
            content='확인된 공지 내용',
            document_type='NOTICE',
            metadata={
                'source_type': 'notice',
                'source_url': 'https://edu.ssafy.com/comm/notice/view.do?noticeId=7',
            },
            score=1.0,
        )

        class StubRetriever:
            def retrieve(self, query, filters=None):
                return [chunk]

        pipeline = ChatPipeline(
            rag_service=RAGService(retriever=StubRetriever()),
            llm_client=self.MockLLMClient(),
        )
        response = pipeline.run(ChatRequest(message='공지 내용 알려줘', user_context=UserContext(user_id=1)))

        self.assertEqual(response.answer, 'mock 모델 답변')
        self.assertEqual(response.references[0].ai_document_id, 7)
        self.assertEqual(response.references[0].source_url, 'https://edu.ssafy.com/comm/notice/view.do?noticeId=7')
        self.assertEqual(response.references[0].external_url, 'https://edu.ssafy.com/comm/notice/view.do?noticeId=7')
        self.assertEqual(response.references[0].title, 'SSAFY 공지')

    def test_schedule_references_keep_source_url(self):
        chunk = RetrievedChunk(
            chunk_id='schedule-event:10',
            ai_document_id=0,
            raw_data_id=10,
            title='SSAFY schedule',
            content='Schedule source',
            document_type='SCHEDULE_EVENT',
            metadata={
                'source_type': 'notice',
                'source_url': 'https://edu.ssafy.com/comm/notice/view.do?noticeId=10',
            },
            score=1.0,
        )
        pipeline = ChatPipeline(llm_client=self.MockLLMClient())

        references = pipeline._schedule_references([chunk])

        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].source_url, 'https://edu.ssafy.com/comm/notice/view.do?noticeId=10')

    def test_schedule_reference_links_do_not_mark_route_as_rag(self):
        from apps.ai.services import AIService

        payload = {
            'answer_policy': 'SCHEDULE_DB_DIRECT',
            'references': [{'source_url': 'https://edu.ssafy.com/comm/notice/view.do?noticeId=10'}],
            'usage': {'mode': 'schedule_db_direct'},
        }

        AIService()._annotate_route_metrics(payload)

        self.assertTrue(payload['usage']['used_db'])
        self.assertFalse(payload['usage']['used_rag'])

    def test_rag_failure_returns_safe_fallback_without_server_crash(self):
        from ai_server.rag.service import RAGService

        class BrokenRetriever:
            def retrieve(self, query, filters=None):
                raise RuntimeError('vector db unavailable')

        pipeline = ChatPipeline(
            rag_service=RAGService(retriever=BrokenRetriever()),
            llm_client=self.MockLLMClient(),
        )
        response = pipeline.run(ChatRequest(message='안녕', user_context=UserContext(user_id=1)))

        self.assertEqual(response.answer, 'mock 모델 답변')
        self.assertEqual(response.references, [])

    def test_unknown_provider_returns_safe_client_instead_of_crashing(self):
        from ai_server.llm.router import LLMClientFactory

        result = LLMClientFactory().create('future-provider').complete([])

        self.assertEqual(result['usage']['mode'], 'unsupported_llm_provider')
        self.assertNotIn('future-provider', result['answer'])

    def test_http_model_client_normalizes_openai_compatible_response(self):
        from ai_server.llm.http_model_client import HttpModelClient

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    'choices': [{'message': {'content': '자체 모델 답변'}}],
                    'usage': {'prompt_tokens': 4, 'completion_tokens': 3, 'total_tokens': 7},
                }

        class Session:
            def post(self, *args, **kwargs):
                return Response()

        result = HttpModelClient(base_url='http://model-server', session=Session()).complete([{'role': 'user', 'content': '질문'}])

        self.assertEqual(result['answer'], '자체 모델 답변')
        self.assertEqual(result['usage']['total_tokens'], 7)
        self.assertEqual(result['usage']['provider'], 'http_model')

    def test_gms_openai_client_uses_openai_compatible_endpoint(self):
        from ai_server.core.config import get_settings
        from ai_server.llm.http_model_client import GmsOpenAiClient

        class Response:
            status_code = 200
            text = ''

            def raise_for_status(self):
                return None

            def json(self):
                return {'choices': [{'message': {'content': 'gms answer'}}]}

        class Session:
            def __init__(self):
                self.calls = []

            def post(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return Response()

        session = Session()
        with patch.dict(os.environ, {'GMS_KEY': 'secret-gms-key', 'GMS_MODEL': 'gpt-5.2'}, clear=False):
            get_settings.cache_clear()
            try:
                result = GmsOpenAiClient(session=session).complete([{'role': 'user', 'content': '안녕'}])
            finally:
                get_settings.cache_clear()

        url = session.calls[0][0][0]
        kwargs = session.calls[0][1]
        self.assertEqual(url, 'https://gms.ssafy.io/gmsapi/api.openai.com/v1/chat/completions')
        self.assertEqual(kwargs['json']['model'], 'gpt-5.2')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer secret-gms-key')
        self.assertEqual(result['usage']['provider'], 'gms_openai')

    def test_http_model_timeout_returns_fallback(self):
        import requests
        from ai_server.llm.http_model_client import HttpModelClient

        class Session:
            def post(self, *args, **kwargs):
                raise requests.Timeout('slow')

        result = HttpModelClient(base_url='http://model-server', session=Session()).complete([])

        self.assertEqual(result['usage']['mode'], 'http_model_timeout')
        self.assertIn('시간이 초과', result['answer'])


class DjangoAIAPICompatibilityTests(TestCase):
    def setUp(self):
        from apps.users.jwt.service import JwtService

        self.user = get_user_model().objects.create_user(username='ai-api-user', email='ai-api@example.com', password='password')
        self.auth = {'HTTP_AUTHORIZATION': f"Bearer {JwtService().issue_token(self.user, token_type='access')}"}

    def test_existing_ai_chat_api_path_and_response_fields_are_preserved(self):
        from django.urls import reverse
        from apps.ai.views import AiChatView

        class StubAIService:
            def answer(self, user, message, session_id=None):
                return {
                    'answer': '호환 응답',
                    'references': [],
                    'intent': 'test',
                    'query_type': 'TEST',
                    'answer_policy': 'TEST_POLICY',
                    'usage': {'mode': 'test'},
                }

        original = AiChatView.service_class
        AiChatView.service_class = StubAIService
        try:
            response = self.client.post(
                reverse('ai-chat'),
                data={'message': '안녕', 'session_id': None},
                content_type='application/json',
                **self.auth,
            )
        finally:
            AiChatView.service_class = original

        self.assertEqual(response.status_code, 200)
        data = response.json()['data']
        self.assertEqual(data['answer'], '호환 응답')
        self.assertEqual(set(data), {'answer', 'references', 'intent', 'query_type', 'answer_policy', 'usage'})

    def test_fastapi_client_timeout_hides_internal_url_and_error(self):
        import requests
        from unittest.mock import patch
        from apps.ai.services import FastAPIAIClient

        with patch('apps.ai.services.requests.post', side_effect=requests.Timeout('secret internal detail')):
            result = FastAPIAIClient(base_url='http://secret-internal-host').chat(self.user, '질문')

        self.assertEqual(result['usage']['mode'], 'ai_server_timeout')
        self.assertNotIn('secret-internal-host', result['answer'])
        self.assertNotIn('secret internal detail', result['answer'])

    def test_fastapi_client_normalizes_reference_source_url(self):
        from apps.ai.services import FastAPIAIClient

        result = FastAPIAIClient(base_url='http://ai-server')._normalize_reference(
            {
                'ai_document_id': 7,
                'title': 'SSAFY notice',
                'source_type': 'notice',
                'score': 0.92,
                'snippet': 'notice body',
                'chunk_id': 'doc:1',
                'raw_data_id': 3,
                'metadata': {
                    'source_url': 'https://edu.ssafy.com/comm/notice/view.do?noticeId=7',
                },
            }
        )

        self.assertEqual(result['source_url'], 'https://edu.ssafy.com/comm/notice/view.do?noticeId=7')
        self.assertEqual(result['external_url'], 'https://edu.ssafy.com/comm/notice/view.do?noticeId=7')
