from types import SimpleNamespace

from django.test import SimpleTestCase

from ai_server.pipelines.chat_pipeline import ChatPipeline
from ai_server.reasoning.lora_reasoner import LoRAReasonerPolicy, LoRAReasonerResult
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
