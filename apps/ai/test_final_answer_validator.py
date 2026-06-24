from django.test import SimpleTestCase

from ai_server.schemas.chat import ChatResponse
from ai_server.validation.final_answer_validator import FinalAnswerValidator


class FinalAnswerValidatorTests(SimpleTestCase):
    def setUp(self):
        self.validator = FinalAnswerValidator()

    def response(self, answer, references=None):
        return ChatResponse(
            answer=answer,
            intent="general_chat",
            query_type="GENERAL",
            answer_policy="LLM",
            references=references or [],
            usage={},
        )

    def test_removes_human_tail(self):
        response = self.validator.apply(
            self.response("시험기간에는 우선순위를 나누는 게 좋습니다.Human: 다음 질문입니다.")
        )

        self.assertEqual(response.answer, "시험기간에는 우선순위를 나누는 게 좋습니다.")
        self.assertIn("human_tag", response.usage["final_answer_validator"]["removed_artifacts"])

    def test_removes_repeated_sentence(self):
        response = self.validator.apply(
            self.response("오늘 할 일을 세 개로 나누세요. 오늘 할 일을 세 개로 나누세요. 먼저 하나만 시작하세요.")
        )

        self.assertEqual(response.answer, "오늘 할 일을 세 개로 나누세요. 먼저 하나만 시작하세요.")
        self.assertTrue(response.usage["final_answer_validator"]["removed_repetition"])

    def test_softens_unsupported_rule_claim_without_references(self):
        response = self.validator.apply(self.response("과락 3번이면 바로 퇴소입니다."))

        self.assertTrue(response.answer.startswith("확인된 근거 없이 규정 기준을 단정하기는 어렵습니다."))
        self.assertTrue(response.usage["final_answer_validator"]["softened_unsupported_rule_claim"])
