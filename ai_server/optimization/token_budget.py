class TokenBudgetManager:
    def trim_context(self, text: str, max_chars: int = 12000) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars]
