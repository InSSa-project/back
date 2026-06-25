from types import SimpleNamespace

from django.test import SimpleTestCase

from ai_server.pipelines.chat_pipeline import ChatPipeline
from ai_server.reasoning.lora_reasoner import LoRAReasonerClient, LoRAReasonerPolicy, LoRAReasonerResult
from ai_server.schemas.chat import ChatRequest, UserContext


class FakeLlm:
    def complete(self, messages):
        return {"answer": "기존 LLM 답변입니다.", "usage": {"provider": "fake"}}


class FakeRagService:
    def __init__(self):
        self.schedule_retrieval = SimpleNamespace()
        self.retriever = SimpleNamespace()
        self.evaluator = SimpleNamespace()
        self.reranker = SimpleNamespace()

    def search_public(self, question, filters=None):
        return SimpleNamespace(
            chunks=[],
            references=[],
            evaluation=SimpleNamespace(
                insufficient_context=True,
                reason="test_insufficient",
                exact_match=False,
                top_score=0,
            ),
        )


class FakeLoraReasoner:
    def __init__(self, used):
        self.used = used
        self.last_context = None

    def maybe_answer(self, question, query_type, answer_policy, verified_context, route=""):
        self.last_context = verified_context
        if self.used:
            return LoRAReasonerResult(used=True, answer="LoRA 선배 답변입니다.", reason="test")
        return LoRAReasonerResult(used=False, reason="disabled")


class LoRAReasonerPipelineTests(SimpleTestCase):
    def request(self, message="싸피 생활이 너무 힘들어. 어떻게 버티면 좋을까?"):
        return ChatRequest(message=message, user_context=UserContext(user_id=1))

    def test_policy_uses_lora_for_domain_advice(self):
        use_lora, reason = LoRAReasonerPolicy().should_use(
            "싸피 프로젝트 때문에 너무 힘들어. 뭐부터 해야 해?",
            "GENERAL_ADVICE",
            "LLM",
        )

        self.assertTrue(use_lora)
        self.assertEqual(reason, "domain_advice")

    def test_policy_uses_lora_for_short_distress_advice(self):
        use_lora, reason = LoRAReasonerPolicy().should_use(
            "나 너무 힘들어",
            "GENERAL_ADVICE",
            "GENERAL_ADVICE_FALLBACK",
        )

        self.assertTrue(use_lora)
        self.assertEqual(reason, "advice_query")

    def test_failed_exam_recovery_question_is_not_schedule_query(self):
        pipeline = ChatPipeline(rag_service=FakeRagService(), llm_client=FakeLlm())
        pipeline.lora_reasoner = FakeLoraReasoner(used=True)

        response = pipeline.run(self.request("시험 망했는데 어떻게 회복하지"))

        self.assertEqual(response.answer_policy, "LORA_REASONER")
        self.assertEqual(response.usage["mode"], "lora_reasoner")
        self.assertNotEqual(response.query_type, "SCHEDULE_RANGE")

    def test_bad_exam_recovery_answer_is_replaced_with_exam_fallback(self):
        settings = SimpleNamespace(lora_reasoner_enabled=True)
        client = LoRAReasonerClient(settings)

        self.assertTrue(
            client._is_bad_exam_recovery_answer(
                "시험 망했는데 어떻게 회복하지",
                '결론: 지금은 "시험 망했다"가 아니라 과중한 스케줄로 인한 "막힘" 신호라서 "막힌 문제 해결"과 "선형계획"이 중요합니다.',
            )
        )

        answer = client._fallback_advice_answer("시험 망했는데 어떻게 회복하지")
        self.assertIn("시험을 망친 직후", answer)
        self.assertIn("실수", answer)

    def test_pipeline_falls_back_when_lora_not_used(self):
        pipeline = ChatPipeline(rag_service=FakeRagService(), llm_client=FakeLlm())
        pipeline.lora_reasoner = FakeLoraReasoner(used=False)

        response = pipeline.run(self.request())

        self.assertEqual(response.answer, "기존 LLM 답변입니다.")
        self.assertNotEqual(response.answer_policy, "LORA_REASONER")

    def test_pipeline_can_return_lora_answer(self):
        pipeline = ChatPipeline(rag_service=FakeRagService(), llm_client=FakeLlm())
        pipeline.lora_reasoner = FakeLoraReasoner(used=True)

        response = pipeline.run(self.request())

        self.assertEqual(response.answer, "LoRA 선배 답변입니다.")
        self.assertEqual(response.answer_policy, "LORA_REASONER")
        self.assertEqual(response.usage["mode"], "lora_reasoner")
        self.assertIsNotNone(pipeline.lora_reasoner.last_context)
