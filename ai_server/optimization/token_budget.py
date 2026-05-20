from dataclasses import dataclass

from ai_server.core.config import get_settings


@dataclass
class PromptBudget:
    context_chunks: list
    few_shots: list[dict]
    memory_context: str
    style_prompt: str
    estimated_tokens: int


class TokenBudgetManager:
    """Approximate token budget manager independent from any LLM provider."""

    CHARS_PER_TOKEN = 4

    def __init__(self):
        self.settings = get_settings()

    def estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // self.CHARS_PER_TOKEN)

    def trim_context(self, text: str, max_chars: int = 12000) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars]

    def fit(
        self,
        fixed_text: str,
        context_chunks: list,
        few_shots: list[dict],
        memory_context: str,
        style_prompt: str,
    ) -> PromptBudget:
        context_chunks = list(context_chunks[: self.settings.max_context_chunks])
        few_shots = list(few_shots[: self.settings.max_few_shots])
        memory_context = memory_context or ''
        style_prompt = style_prompt or ''

        while self._estimate_total(fixed_text, context_chunks, few_shots, memory_context, style_prompt) > self.settings.max_prompt_tokens:
            if memory_context:
                memory_context = memory_context[: max(0, len(memory_context) // 2)]
                continue
            if len(few_shots) > 1:
                few_shots = few_shots[:-1]
                continue
            if context_chunks:
                longest = max(context_chunks, key=lambda chunk: len(getattr(chunk, 'content', '')))
                content = getattr(longest, 'content', '')
                if len(content) > 300:
                    longest.content = content[: max(300, len(content) // 2)]
                    continue
                context_chunks = context_chunks[:-1]
                continue
            if style_prompt:
                style_prompt = style_prompt[: max(0, len(style_prompt) // 2)]
                continue
            break

        estimated = self._estimate_total(fixed_text, context_chunks, few_shots, memory_context, style_prompt)
        return PromptBudget(context_chunks, few_shots, memory_context, style_prompt, estimated)

    def _estimate_total(self, fixed_text: str, context_chunks: list, few_shots: list[dict], memory_context: str, style_prompt: str) -> int:
        context_text = '\n'.join(getattr(chunk, 'content', '') for chunk in context_chunks)
        few_shot_text = '\n'.join(f"{item.get('user', '')}\n{item.get('assistant', '')}" for item in few_shots)
        return self.estimate_tokens('\n'.join([fixed_text, context_text, few_shot_text, memory_context, style_prompt]))
