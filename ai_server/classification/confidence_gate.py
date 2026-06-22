from dataclasses import dataclass


class ConfidenceGateDecision:
    DIRECT_RETRIEVAL = 'direct_retrieval'
    VALIDATE_THEN_RETRIEVE = 'validate_then_retrieve'
    LLM_INTENT_CLASSIFIER = 'llm_intent_classifier'


@dataclass
class ConfidenceGateResult:
    decision: str
    band: str
    threshold_high: float
    threshold_low: float
    reason: str = ''


class ConfidenceGate:
    def __init__(self, high_threshold: float = 0.75, low_threshold: float = 0.45):
        self.high_threshold = high_threshold
        self.low_threshold = low_threshold

    def decide(self, rule_result) -> ConfidenceGateResult:
        confidence = float(getattr(rule_result, 'confidence', 0.0) or 0.0)
        if confidence >= self.high_threshold:
            return ConfidenceGateResult(
                decision=ConfidenceGateDecision.DIRECT_RETRIEVAL,
                band='high',
                threshold_high=self.high_threshold,
                threshold_low=self.low_threshold,
                reason='rule_confidence_high',
            )
        if confidence < self.low_threshold:
            return ConfidenceGateResult(
                decision=ConfidenceGateDecision.LLM_INTENT_CLASSIFIER,
                band='low',
                threshold_high=self.high_threshold,
                threshold_low=self.low_threshold,
                reason='rule_confidence_low',
            )
        return ConfidenceGateResult(
            decision=ConfidenceGateDecision.VALIDATE_THEN_RETRIEVE,
            band='medium',
            threshold_high=self.high_threshold,
            threshold_low=self.low_threshold,
            reason='rule_confidence_medium',
        )
