from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ai_server.classification.query_classifier import QueryClassifier, QueryType
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

from apps.ai.models import AiDocument
from ai_server.core.config import get_settings
from schedules.models import ScheduleEvent
from sync.models import RawSsafyData


class RagFallbackPolicyTests(SimpleTestCase):
    def test_query_classifier_types(self):
        classifier = QueryClassifier()
        self.assertEqual(classifier.classify('월말평가 언제야?'), QueryType.SSAFY_OFFICIAL)
        self.assertEqual(classifier.classify('Django에서 ForeignKey 뭐야?'), QueryType.GENERAL_TECH)
        self.assertEqual(classifier.classify('프로젝트 일정 어떻게 관리하면 좋아?'), QueryType.GENERAL_ADVICE)

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
        self.assertIn('styles/developer_tutor.md', result.metadata['used_prompt_files'])
        self.assertIn('policies/general_tech_policy.md', result.metadata['used_prompt_files'])
        self.assertNotIn('policies/schedule_policy.md', result.metadata['used_prompt_files'])
        self.assertLessEqual(result.metadata['context_count'], 4)



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
    def test_empty_schedule_date_returns_no_confirmed_schedule_message(self):
        response = self._ask(self.user_a, message='2026-06-03 일정 알려줘')

        self.assertEqual(response.answer_policy, 'SCHEDULE_DB_NO_MATCH')
        self.assertIn('확인', response.answer)
        self.assertIn('없', response.answer)

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
