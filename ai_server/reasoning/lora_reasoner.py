import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class LoRAReasonerResult:
    used: bool
    answer: str = ""
    reason: str = ""


class LoRAReasonerPolicy:
    OFFICIAL_QUERY_TYPES = {"SSAFY_OFFICIAL", "NOTICE", "SCHEDULE", "CURRENT_DATE"}
    ROUTES_TO_SKIP = {
        "current_date",
        "schedule_db",
        "personal_score",
        "personal_risk",
        "recommended_schedule",
        "important_schedule",
    }
    DOMAIN_KEYWORDS = (
        "싸피",
        "ssafy",
        "과락",
        "월말",
        "평가",
        "프로젝트",
        "팀원",
        "팀플",
        "발표",
        "프로님",
        "멘토",
        "멘토링",
        "컨설턴트",
        "시험",
        "알고리즘",
        "수료",
        "출결",
        "지각",
        "공지",
    )
    ADVICE_KEYWORDS = (
        "어떻게",
        "뭐부터",
        "무엇부터",
        "조언",
        "추천",
        "힘들",
        "불안",
        "멘탈",
        "걱정",
        "괜찮",
        "망했",
        "갈등",
        "분위기",
        "문화",
    )

    def should_use(self, question: str, query_type: str, answer_policy: str, route: str = "") -> tuple[bool, str]:
        text = (question or "").lower()
        if route in self.ROUTES_TO_SKIP:
            return False, f"skip_route:{route}"
        if query_type in self.OFFICIAL_QUERY_TYPES and answer_policy in {"RAG_GROUNDED", "SERVER_DATE_DIRECT"}:
            return False, "official_fact_primary"
        has_domain = any(keyword in text for keyword in self.DOMAIN_KEYWORDS)
        has_advice = any(keyword in text for keyword in self.ADVICE_KEYWORDS)
        if has_domain and has_advice:
            return True, "domain_advice"
        if query_type in {"GENERAL_ADVICE", "UNKNOWN", "GENERAL_CHAT"} and has_advice:
            return True, "advice_query"
        return False, "not_lora_domain"


class LoRAReasonerClient:
    """Lazy local adapter client for SSAFY-style reasoning."""

    SYSTEM_PROMPT = (
        "너는 SSAFY 생활을 잘 아는 선배 AI이다. "
        "제공된 Verified Context가 있으면 그 범위 안에서만 사실을 사용한다. "
        "정확한 규정은 단정하지 않고, 확인된 정보와 조언을 분리해서 답한다. "
        "사용자가 힘들어하면 먼저 짧게 공감하고, 오늘 할 수 있는 행동을 2~3개만 제안한다. "
        "Human:, User:, Assistant: 같은 태그를 출력하지 않는다."
    )

    def __init__(self, settings, policy: LoRAReasonerPolicy | None = None):
        self.settings = settings
        self.policy = policy or LoRAReasonerPolicy()
        self._tokenizer = None
        self._model = None

    def maybe_answer(
        self,
        question: str,
        query_type: str,
        answer_policy: str,
        verified_context,
        route: str = "",
        force: bool = False,
    ) -> LoRAReasonerResult:
        if not self.settings.lora_reasoner_enabled:
            return LoRAReasonerResult(used=False, reason="disabled")

        reason = "forced"
        if not force:
            should_use, reason = self.policy.should_use(question, query_type, answer_policy, route=route)
            if not should_use:
                return LoRAReasonerResult(used=False, reason=reason)

        adapter_path = Path(self.settings.lora_adapter_path)
        if not adapter_path.exists():
            return LoRAReasonerResult(used=False, reason=f"adapter_not_found:{adapter_path}")

        try:
            tokenizer, model = self._load_model()
            from ai_server.finetuning.interact import build_chatml_prompt, strip_prompt
            from ai_server.finetuning.model import generate_response

            user_content = "\n\n".join(
                [
                    verified_context.to_prompt_text() if verified_context else "[Verified Context]\nNO_CONTEXT",
                    "[User Question]",
                    question,
                ]
            )
            prompt = build_chatml_prompt(self.SYSTEM_PROMPT, user_content)
            raw_answer = generate_response(
                model=model,
                tokenizer=tokenizer,
                prompt=prompt,
                max_new_tokens=self.settings.lora_max_new_tokens,
                temperature=self.settings.lora_temperature,
                top_p=self.settings.lora_top_p,
                repetition_penalty=self.settings.lora_repetition_penalty,
                no_repeat_ngram_size=5,
            )
            answer = strip_prompt(raw_answer, prompt)
            if not answer:
                return LoRAReasonerResult(used=False, reason="empty_answer")
            return LoRAReasonerResult(used=True, answer=answer, reason=reason)
        except Exception as exc:
            logger.exception("LoRA reasoner failed: %s", exc)
            return LoRAReasonerResult(used=False, reason=f"exception:{type(exc).__name__}")

    def _load_model(self):
        if self._tokenizer is not None and self._model is not None:
            return self._tokenizer, self._model

        import torch

        from ai_server.finetuning.model import load_lora_model

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        tokenizer, model = load_lora_model(
            base_model_name=self.settings.lora_base_model,
            adapter_path=self.settings.lora_adapter_path,
            device=device,
            dtype=dtype,
        )
        model.eval()
        self._tokenizer = tokenizer
        self._model = model
        return tokenizer, model
