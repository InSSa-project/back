import re
from dataclasses import dataclass, field

from ai_server.schemas.chat import ChatResponse


@dataclass
class FinalAnswerValidationResult:
    answer: str
    removed_artifacts: list[str] = field(default_factory=list)
    removed_repetition: bool = False
    softened_unsupported_rule_claim: bool = False

    def as_usage(self) -> dict:
        return {
            "final_answer_validator": {
                "removed_artifacts": self.removed_artifacts,
                "removed_repetition": self.removed_repetition,
                "softened_unsupported_rule_claim": self.softened_unsupported_rule_claim,
            }
        }


class FinalAnswerValidator:
    """Last-mile guard for generated answers before they reach the user."""

    ARTIFACT_PATTERNS = [
        ("human_tag", re.compile(r"\s*Human\s*:.*$", re.IGNORECASE | re.DOTALL)),
        ("user_tag", re.compile(r"\s*User\s*:.*$", re.IGNORECASE | re.DOTALL)),
        ("assistant_tag", re.compile(r"\s*Assistant\s*:.*$", re.IGNORECASE | re.DOTALL)),
        ("instruction_tag", re.compile(r"\s*###\s*Instruction\s*:.*$", re.IGNORECASE | re.DOTALL)),
        ("response_tag", re.compile(r"\s*###\s*Response\s*:.*$", re.IGNORECASE | re.DOTALL)),
        ("chatml_user", re.compile(r"\s*<\|user\|>.*$", re.IGNORECASE | re.DOTALL)),
        ("chatml_assistant", re.compile(r"\s*<\|assistant\|>.*$", re.IGNORECASE | re.DOTALL)),
    ]
    RULE_KEYWORDS = ("과락", "퇴소", "제적", "출결", "지각", "결석", "수료", "재시험", "공결")
    ASSERTIVE_RULE_PATTERNS = [
        re.compile(r"(?:반드시|무조건|바로|즉시)\s*(?:퇴소|제적|탈락|수료\s*불가)"),
        re.compile(r"\d+\s*(?:번|회|개)\s*(?:이면|부터|이상).*?(?:퇴소|제적|탈락|수료\s*불가)"),
    ]

    def validate(self, response: ChatResponse) -> FinalAnswerValidationResult:
        answer = response.answer or ""
        artifacts = []

        for name, pattern in self.ARTIFACT_PATTERNS:
            next_answer = pattern.sub("", answer).strip()
            if next_answer != answer.strip():
                artifacts.append(name)
                answer = next_answer

        answer, removed_repetition = self._remove_repeated_sentences(answer)
        answer, softened = self._soften_unsupported_rule_claim(answer, response)

        return FinalAnswerValidationResult(
            answer=answer.strip(),
            removed_artifacts=artifacts,
            removed_repetition=removed_repetition,
            softened_unsupported_rule_claim=softened,
        )

    def apply(self, response: ChatResponse) -> ChatResponse:
        result = self.validate(response)
        response.answer = result.answer
        response.usage.update(result.as_usage())
        return response

    def _remove_repeated_sentences(self, answer: str) -> tuple[str, bool]:
        paragraphs = re.split(r"(\n+)", answer)
        changed = False
        cleaned_parts = []
        recent = []

        for part in paragraphs:
            if part.startswith("\n"):
                cleaned_parts.append(part)
                continue

            sentences = re.split(r"(?<=[.!?。！？요다까죠])\s+", part)
            cleaned_sentences = []
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                normalized = re.sub(r"\s+", " ", sentence)
                if normalized in recent:
                    changed = True
                    continue
                cleaned_sentences.append(sentence)
                recent.append(normalized)
                recent = recent[-6:]
            cleaned_parts.append(" ".join(cleaned_sentences))

        cleaned = "".join(cleaned_parts)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned, changed

    def _soften_unsupported_rule_claim(self, answer: str, response: ChatResponse) -> tuple[str, bool]:
        if response.references:
            return answer, False
        if not any(keyword in answer for keyword in self.RULE_KEYWORDS):
            return answer, False
        if not any(pattern.search(answer) for pattern in self.ASSERTIVE_RULE_PATTERNS):
            return answer, False

        prefix = (
            "확인된 근거 없이 규정 기준을 단정하기는 어렵습니다. "
            "정확한 기준은 공식 공지나 담당 프로님 안내를 확인해야 합니다.\n\n"
        )
        return prefix + answer, True
