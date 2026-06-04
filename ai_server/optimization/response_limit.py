from ai_server.core.config import get_settings
from ai_server.optimization.token_budget import TokenBudgetManager


def limit_answer(answer: str) -> tuple[str, dict]:
    settings = get_settings()
    original = (answer or '').strip()
    limited = _truncate(original, settings.max_answer_chars)
    estimator = TokenBudgetManager()
    return limited, {
        'answer_chars': len(limited),
        'estimated_output_tokens': estimator.estimate_tokens(limited),
        'answer_truncated': limited != original,
        'answer_char_limit': settings.max_answer_chars,
    }


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return text[:max_chars]

    candidate = text[: max_chars - 1].rstrip()
    boundary = max(candidate.rfind('\n'), candidate.rfind('. '), candidate.rfind('다.'))
    if boundary >= max_chars // 2:
        candidate = candidate[: boundary + (2 if candidate[boundary:boundary + 2] == '다.' else 1)].rstrip()
    return f'{candidate}…'
