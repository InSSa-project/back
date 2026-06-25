from pydantic import BaseModel
from fastapi import APIRouter, Header, HTTPException

from ai_server.core.config import get_settings
from ai_server.reasoning.lora_reasoner import LoRAReasonerClient


router = APIRouter()
_lora_reasoner: LoRAReasonerClient | None = None


class LoraGenerateRequest(BaseModel):
    question: str
    query_type: str = "GENERAL_ADVICE"
    answer_policy: str = "LORA_REMOTE"
    route: str = ""
    verified_context: str = ""


class LoraGenerateResponse(BaseModel):
    answer: str
    reason: str


class _PlainVerifiedContext:
    def __init__(self, text: str):
        self.text = text or "[Verified Context]\nNO_CONTEXT"

    def to_prompt_text(self) -> str:
        return self.text


def get_lora_reasoner() -> LoRAReasonerClient:
    global _lora_reasoner
    if _lora_reasoner is None:
        _lora_reasoner = LoRAReasonerClient(get_settings())
    return _lora_reasoner


def _check_api_key(authorization: str | None, x_lora_api_key: str | None) -> None:
    expected = get_settings().lora_remote_api_key
    if not expected:
        return
    bearer = f"Bearer {expected}"
    if authorization == bearer or x_lora_api_key == expected:
        return
    raise HTTPException(status_code=401, detail="Invalid LoRA API key")


@router.post('/generate', response_model=LoraGenerateResponse)
def generate_lora_answer(
    request: LoraGenerateRequest,
    authorization: str | None = Header(default=None),
    x_lora_api_key: str | None = Header(default=None),
):
    _check_api_key(authorization, x_lora_api_key)
    context = _PlainVerifiedContext(request.verified_context)
    result = get_lora_reasoner().maybe_answer(
        question=request.question,
        query_type=request.query_type,
        answer_policy=request.answer_policy,
        verified_context=context,
        route=request.route,
        force=True,
        allow_remote=False,
    )
    if not result.used:
        raise HTTPException(status_code=503, detail=result.reason or "LoRA answer unavailable")
    return LoraGenerateResponse(answer=result.answer, reason=result.reason)
