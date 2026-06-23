from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ai_server.classification.query_classifier import QueryClassifier, QueryType
from ai_server.classification.domain_intent_router import DomainIntent, DomainIntentRouter
from ai_server.pipelines.chat_pipeline import ChatPipeline
from ai_server.policies.answer_policy import AnswerPolicy, AnswerPolicyRouter
from ai_server.prompts.builder import PromptBuilder
from ai_server.retrieval.policy_router import RetrievalPolicyRouter
from ai_server.retrieval.query_parser import DateExtractor, ScheduleQueryParser, ScheduleQueryType
from ai_server.retrieval.schedule_retrieval import ScheduleRetrievalService
from ai_server.retrievers.evaluator import RetrievalEvaluator
from ai_server.rag.schemas.documents import RetrievedChunk
from ai_server.schemas.chat import ChatRequest, UserContext
from ai_server.vectorstores.faiss_store import FaissVectorStore

from datetime import date, timedelta
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings

from apps.ai.models import AiDocument, ChatMessage
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
