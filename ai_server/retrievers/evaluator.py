from dataclasses import dataclass

from ai_server.core.config import get_settings


@dataclass
class RetrievalEvaluation:
    insufficient_context: bool
    top_score: float
    threshold: float
    reason: str


class RetrievalEvaluator:
    def __init__(self, threshold: float | None = None):
        settings = get_settings()
        self.threshold = threshold if threshold is not None else settings.retrieval_score_threshold

    def evaluate(self, chunks, query: str = '') -> RetrievalEvaluation:
        if not chunks:
            return RetrievalEvaluation(
                insufficient_context=True,
                top_score=0.0,
                threshold=self.threshold,
                reason='NO_RETRIEVED_CHUNKS',
            )
        top_score = max(float(getattr(chunk, 'score', 0.0)) for chunk in chunks)
        if top_score < self.threshold:
            return RetrievalEvaluation(
                insufficient_context=True,
                top_score=top_score,
                threshold=self.threshold,
                reason='LOW_TOP_SCORE',
            )
        if not self._has_required_query_terms(query, chunks):
            return RetrievalEvaluation(
                insufficient_context=True,
                top_score=top_score,
                threshold=self.threshold,
                reason='NO_REQUIRED_TERM_MATCH',
            )
        return RetrievalEvaluation(
            insufficient_context=False,
            top_score=top_score,
            threshold=self.threshold,
            reason='ENOUGH_CONTEXT',
        )

    def _has_required_query_terms(self, query: str, chunks) -> bool:
        required_terms = self._required_terms(query)
        if not required_terms:
            return True
        haystack = '\n'.join(f'{getattr(chunk, "title", "")}\n{getattr(chunk, "content", "")}' for chunk in chunks).lower()
        return any(term in haystack for term in required_terms)

    def _required_terms(self, query: str) -> list[str]:
        text = (query or '').lower()
        terms = []
        for term in (
            '\uacfc\ub77d', '\ud1f4\uc18c', '\uc218\ub8cc', '\ucd9c\uacb0', '\uacb0\uc11d', '\uc9c0\uac01',
            '\uc6d4\ub9d0\ud3c9\uac00', '\uacfc\ubaa9\ud3c9\uac00', '\uc7ac\uc2dc\ud5d8', '\ud1b5\uacfc\uae30\uc900',
        ):
            if term in text:
                terms.append(term)
        return terms
