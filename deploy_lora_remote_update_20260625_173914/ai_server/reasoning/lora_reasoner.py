import logging
import re
import json
import urllib.error
import urllib.request
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
        "인싸",
        "ssafy",
        "싸피",
        "과락",
        "월말",
        "평가",
        "프로젝트",
        "공부",
        "발표",
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
        "힘들어",
        "불안",
        "멘탈",
        "걱정",
        "괜찮",
        "망했",
        "갈등",
        "분위기",
        "문화",
        "막막",
        "지쳤",
        "포기",
    )

    def should_use(self, question: str, query_type: str, answer_policy: str, route: str = "") -> tuple[bool, str]:
        text = (question or "").lower()
        if route in self.ROUTES_TO_SKIP:
            return False, f"skip_route:{route}"
        if query_type == "GENERAL_TECH":
            return False, "tech_query_uses_llm"
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
        "너는 SSAFY 생활을 함께 정리해주는 차분한 AI 선배다. "
        "공식 규정, 공지, 일정은 Verified Context 안에서 확인된 내용만 사용한다. "
        "감정적으로 힘들다는 질문에는 규정 설명을 하지 말고, 먼저 한 문장으로 공감한다. "
        "그 다음 오늘 바로 할 수 있는 작은 행동 2~3개를 짧게 제안한다. "
        "답변은 5문장 이내로 끝내고, 마지막은 부담을 낮춰주는 말로 마무리한다. "
        "Human:, User:, Assistant: 같은 태그는 절대 출력하지 않는다."
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
        allow_remote: bool = True,
    ) -> LoRAReasonerResult:
        if not self.settings.lora_reasoner_enabled:
            return LoRAReasonerResult(used=False, reason="disabled")

        reason = "forced"
        if not force:
            should_use, reason = self.policy.should_use(question, query_type, answer_policy, route=route)
            if not should_use:
                return LoRAReasonerResult(used=False, reason=reason)

        if allow_remote:
            remote_result = self._try_remote_answer(
                question=question,
                query_type=query_type,
                answer_policy=answer_policy,
                verified_context=verified_context,
                route=route,
                policy_reason=reason,
            )
            if remote_result is not None:
                return remote_result

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
            answer = self._clean_answer(strip_prompt(raw_answer, prompt))
            if (
                self._is_low_quality_answer(answer)
                or self._is_incomplete_answer(answer)
                or self._is_bad_exam_recovery_answer(question, answer)
            ):
                answer = self._fallback_advice_answer(question)
            if not answer:
                return LoRAReasonerResult(used=False, reason="empty_answer")
            return LoRAReasonerResult(used=True, answer=answer, reason=reason)
        except Exception as exc:
            logger.exception("LoRA reasoner failed: %s", exc)
            return LoRAReasonerResult(used=False, reason=f"exception:{type(exc).__name__}")

    def _try_remote_answer(
        self,
        question: str,
        query_type: str,
        answer_policy: str,
        verified_context,
        route: str,
        policy_reason: str,
    ) -> LoRAReasonerResult | None:
        remote_url = getattr(self.settings, "lora_remote_url", "") or ""
        if not remote_url:
            return None

        context_text = verified_context.to_prompt_text() if verified_context else "[Verified Context]\nNO_CONTEXT"
        payload = {
            "question": question,
            "query_type": query_type,
            "answer_policy": answer_policy,
            "route": route,
            "verified_context": context_text,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        api_key = getattr(self.settings, "lora_remote_api_key", "") or ""
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["X-LoRA-API-Key"] = api_key

        request = urllib.request.Request(remote_url, data=body, headers=headers, method="POST")
        timeout = getattr(self.settings, "lora_remote_timeout", 120)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            logger.warning("Remote LoRA request failed: %s", exc)
            return LoRAReasonerResult(used=False, reason=f"remote_exception:{type(exc).__name__}")

        answer = self._clean_answer(data.get("answer", ""))
        if (
            self._is_low_quality_answer(answer)
            or self._is_incomplete_answer(answer)
            or self._is_bad_exam_recovery_answer(question, answer)
        ):
            answer = self._fallback_advice_answer(question)
        if not answer:
            return LoRAReasonerResult(used=False, reason="remote_empty_answer")
        remote_reason = data.get("reason") or policy_reason
        return LoRAReasonerResult(used=True, answer=answer, reason=f"remote:{remote_reason}")

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

    def preload(self) -> str:
        if not self.settings.lora_reasoner_enabled:
            return "disabled"
        adapter_path = Path(self.settings.lora_adapter_path)
        if not adapter_path.exists():
            return f"adapter_not_found:{adapter_path}"
        self._load_model()
        return "loaded"

    def _clean_answer(self, answer: str) -> str:
        text = (answer or "").strip()
        for marker in ("Human:", "User:", "Assistant:", "<|im_end|>", "<|im_start|>"):
            text = text.replace(marker, "")
        lines = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line in {"선생님", "선생님과"}:
                continue
            lines.append(line)
        text = "\n".join(lines).strip()
        text = re.sub(r"\s{2,}", " ", text)
        return self._limit_sentences(text, max_sentences=5)

    def _limit_sentences(self, text: str, max_sentences: int) -> str:
        if not text:
            return ""
        count = 0
        for index, char in enumerate(text):
            if char in ".!?\n":
                count += 1
                if count >= max_sentences:
                    return text[: index + 1].strip()
        return text.strip()

    def _is_low_quality_answer(self, answer: str) -> bool:
        text = (answer or "").strip()
        if len(text) < 80:
            return True
        low_value_markers = (
            "definitely",
            "AI is here",
            "already exists in context",
            "context",
            "구체적으로 물어",
            "스케줄이나 업데이트",
            "질문하면 바로",
            "어떤 걸 시작하거나 중단",
        )
        if any(marker in text for marker in low_value_markers):
            return True
        if re.search(r"[A-Za-z]{4,}", text):
            return True
        emotional_markers = ("힘들", "버티", "괜찮", "쉬", "혼자", "오늘")
        return not any(marker in text for marker in emotional_markers)

    def _is_incomplete_answer(self, answer: str) -> bool:
        text = (answer or "").strip()
        if not text:
            return True
        if text.endswith((".", "!", "?", "요", "다", "죠", "요.", "다.", "죠.")):
            return False
        return len(text) >= max(80, self.settings.lora_max_new_tokens)

    def _is_bad_exam_recovery_answer(self, question: str, answer: str) -> bool:
        question_text = question or ""
        if not self._is_exam_recovery_question(question_text):
            return False
        answer_text = answer or ""
        required_terms = ("시험", "평가", "점수", "문제", "실수", "복습", "회복")
        if not any(term in answer_text for term in required_terms):
            return True
        bad_terms = (
            "선형계획",
            "과중한 스케줄",
            "막힌 문제 해결",
            "알고리즘 개념",
            "일정은",
            "확인된 일정",
        )
        return any(term in answer_text for term in bad_terms)

    def _is_exam_recovery_question(self, question: str) -> bool:
        return any(keyword in question for keyword in ("시험", "평가", "월말평가", "과목평가")) and any(
            keyword in question for keyword in ("망했", "망친", "못봤", "회복", "떨어졌")
        )

    def _fallback_advice_answer(self, question: str) -> str:
        text = question or ""
        if self._is_exam_recovery_question(text):
            return (
                "시험을 망친 직후에는 먼저 점수보다 회복 루틴을 잡는 게 중요해요. "
                "오늘은 틀린 문제를 전부 보려고 하지 말고, 기억나는 실수 3개만 적어서 원인을 나눠보세요. "
                "개념 부족인지, 시간 관리인지, 구현 실수인지 하나만 고르면 다음 공부 방향이 훨씬 선명해집니다. "
                "내일은 그 원인 하나에 맞춰 30분 복습하고 비슷한 문제 1개만 다시 풀어보세요. "
                "한 번 망친 시험이 끝이 아니라, 다음 회차에서 같은 실수를 줄이는 쪽으로 회복하면 됩니다."
            )
        if any(keyword in text for keyword in ("힘들", "지쳤", "멘탈", "불안", "포기")):
            return (
                "지금 많이 버티고 있는 상태처럼 보여요. "
                "일단 오늘은 문제를 전부 해결하려고 하기보다, 물 한 잔 마시고 10분만 쉬면서 몸을 먼저 안정시켜요. "
                "그다음 해야 할 일을 하나만 적고, 20분짜리 작은 단위로 시작해보면 좋겠습니다. "
                "혼자 감당하기 어렵다면 같은 반 친구나 코치님께 지금 상태를 짧게 공유해도 괜찮아요. "
                "오늘은 완벽하게 버티는 날이 아니라, 무너지지 않게 조금 낮춰 잡는 날로 가도 됩니다."
            )
        return (
            "지금은 한 번에 정답을 찾기보다 상황을 작게 나눠보는 게 좋아요. "
            "오늘 꼭 해야 하는 일 하나, 미뤄도 되는 일 하나, 도움을 요청할 사람 하나만 정해보세요. "
            "그 정도만 해도 지금 상태에서는 충분히 앞으로 가는 겁니다."
        )
