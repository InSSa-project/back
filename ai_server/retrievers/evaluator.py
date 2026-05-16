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

    def evaluate(self, chunks) -> RetrievalEvaluation:
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
        return RetrievalEvaluation(
            insufficient_context=False,
            top_score=top_score,
            threshold=self.threshold,
            reason='ENOUGH_CONTEXT',
        )
