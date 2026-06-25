from types import SimpleNamespace

from django.test import SimpleTestCase

from ai_server.context.verified_context_builder import VerifiedContextBuilder


class VerifiedContextBuilderTests(SimpleTestCase):
    def test_builds_compact_facts_and_metadata(self):
        chunk = SimpleNamespace(
            chunk_id="chunk-1",
            title="학사 규정",
            content="과락과 수료 기준은 공식 안내를 확인해야 합니다.",
            document_type="SYNC_ACADEMIC_RULE",
            score=0.91,
            metadata={"source_type": "academic_rule", "source_url": "https://example.com/rule"},
        )
        evaluation = SimpleNamespace(reason="rag_enough_context", insufficient_context=False)

        context = VerifiedContextBuilder().build(
            question="과락 몇 번이면 퇴소야?",
            intent="ssafy_official",
            query_type="SSAFY_OFFICIAL",
            answer_policy="RAG_GROUNDED",
            user_context='{"user_id": 1}',
            memory_context="NO_MEMORY",
            chunks=[chunk],
            references=[],
            retrieval_evaluation=evaluation,
        )

        self.assertEqual(context.metadata["fact_count"], 1)
        self.assertEqual(context.metadata["retrieval_status"], "rag_enough_context")
        self.assertFalse(context.metadata["insufficient_context"])
        self.assertIn("학사 규정", context.to_prompt_text())
        self.assertIn("academic_rule", context.to_prompt_text())

    def test_limits_long_text(self):
        long_content = "가" * 1000
        chunk = SimpleNamespace(
            chunk_id="chunk-1",
            title="긴 문서",
            content=long_content,
            document_type="NOTICE",
            score=0.5,
            metadata={},
        )

        context = VerifiedContextBuilder().build(
            question="질문",
            intent="general_chat",
            query_type="GENERAL",
            answer_policy="LLM",
            user_context="{}",
            memory_context="",
            chunks=[chunk],
        )

        self.assertLessEqual(len(context.facts[0]["snippet"]), 420)
        self.assertTrue(context.facts[0]["snippet"].endswith("..."))
