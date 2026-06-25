from django.test import SimpleTestCase

from ai_server.classification.query_classifier import QueryClassifier, QueryType
from ai_server.classification.server_verified_router import ServerVerifiedIntentRouter, VerifiedRoute
from ai_server.pipelines.chat_pipeline import ChatPipeline
from ai_server.rag.schemas.documents import RetrievedChunk
from ai_server.rag.service import RAGService
from ai_server.schemas.chat import ChatRequest, UserContext


class OfficialRuleRagRoutingTests(SimpleTestCase):
    def test_server_verified_router_sends_rule_questions_to_rag(self):
        router = ServerVerifiedIntentRouter()

        self.assertEqual(
            router.decide('우리 얼마나 과락하면 퇴소냐', classified=QueryType.SSAFY_OFFICIAL).route,
            VerifiedRoute.RAG,
        )
        self.assertEqual(
            router.decide('과락 몇 번이면 퇴소야?', classified=QueryType.SSAFY_OFFICIAL).route,
            VerifiedRoute.RAG,
        )

    def test_server_verified_router_keeps_recovery_questions_out_of_rag(self):
        router = ServerVerifiedIntentRouter()

        self.assertNotEqual(router.decide('과락인데 어떡하지').route, VerifiedRoute.RAG)
        self.assertNotEqual(router.decide('과락 맞았는데 어떻게 회복하지').route, VerifiedRoute.RAG)

    def test_fail_count_exit_question_is_official_rule_query(self):
        classifier = QueryClassifier()

        self.assertEqual(classifier.classify('과락 몇 번이면 퇴소야?'), QueryType.SSAFY_OFFICIAL)
        self.assertEqual(classifier.classify('과락 몇번이면 퇴소냐'), QueryType.SSAFY_OFFICIAL)
        self.assertEqual(classifier.classify('과락되면 퇴소 기준이 뭐야?'), QueryType.SSAFY_OFFICIAL)

    def test_official_rule_question_uses_academic_rule_rag(self):
        class RecordingRetriever:
            def __init__(self):
                self.filters = []

            def retrieve(self, query, filters=None):
                self.filters.append(filters or {})
                return [
                    RetrievedChunk(
                        chunk_id='academic-rule:1',
                        ai_document_id=1,
                        raw_data_id=1,
                        title='학사규정',
                        content='5-5. 중도퇴소기준: 기준에 해당하는 자는 중도 퇴소 조치를 함.',
                        document_type='SYNC_ACADEMIC_RULE',
                        metadata={'source_type': 'academic_rule'},
                        score=0.02,
                    )
                ]

        class MockLLM:
            def complete(self, messages, **_kwargs):
                return {'answer': '학사규정 근거 답변', 'usage': {'mode': 'mock'}}

        retriever = RecordingRetriever()
        pipeline = ChatPipeline(
            rag_service=RAGService(retriever=retriever),
            llm_client=MockLLM(),
        )

        response = pipeline.run(
            ChatRequest(message='과락 몇번이면 퇴소냐', user_context=UserContext(user_id=1))
        )

        self.assertEqual(retriever.filters[0]['source_type'], 'academic_rule')
        self.assertEqual(response.answer, '학사규정 근거 답변')
        self.assertEqual(response.answer_policy, 'RAG_GROUNDED')
        self.assertFalse(response.usage['retrieval']['insufficient_context'])
