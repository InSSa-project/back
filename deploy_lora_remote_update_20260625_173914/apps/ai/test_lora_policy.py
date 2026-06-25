from django.test import SimpleTestCase

from ai_server.reasoning.lora_reasoner import LoRAReasonerPolicy


class LoRAReasonerPolicyRegressionTests(SimpleTestCase):
    def test_policy_skips_lora_for_general_tech_questions(self):
        use_lora, reason = LoRAReasonerPolicy().should_use(
            "REST API keeps failing. How should I study it?",
            "GENERAL_TECH",
            "GENERAL_KNOWLEDGE_FALLBACK",
        )

        self.assertFalse(use_lora)
        self.assertEqual(reason, "tech_query_uses_llm")
