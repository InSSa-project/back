from django.test import SimpleTestCase

from ai_server.classification.query_classifier import QueryClassifier, QueryType
from ai_server.policies.answer_policy import AnswerPolicy, AnswerPolicyRouter
from ai_server.retrieval.policy_router import RetrievalPolicyRouter
from ai_server.retrieval.query_parser import DateExtractor, ScheduleQueryParser, ScheduleQueryType
from ai_server.retrievers.evaluator import RetrievalEvaluator
from ai_server.vectorstores.faiss_store import FaissVectorStore

from datetime import date
from pathlib import Path
from uuid import uuid4

from django.conf import settings


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
