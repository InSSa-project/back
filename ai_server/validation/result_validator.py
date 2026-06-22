from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ResultValidation:
    status: str
    reason: str
    should_retry: bool = False
    retry_route: str = ''

    def as_usage(self) -> dict:
        return {
            'result_validator_status': self.status,
            'result_validator_reason': self.reason,
            'result_validator_should_retry': self.should_retry,
            'result_validator_retry_route': self.retry_route,
        }


class ResultValidator:
    """Decide whether retrieved DB/RAG results are enough to answer."""

    def validate_schedule(self, chunks, parsed_query=None, fallback_chunks=None) -> ResultValidation:
        chunks = list(chunks or [])
        fallback_chunks = list(fallback_chunks or [])
        if chunks:
            return ResultValidation(status='enough', reason='schedule_rows_found')
        if fallback_chunks:
            return ResultValidation(status='partial', reason='fallback_schedule_rows_found')
        if getattr(parsed_query, 'exact_match_required', False):
            return ResultValidation(status='empty', reason='exact_date_no_rows', should_retry=False)
        return ResultValidation(
            status='empty',
            reason='schedule_no_rows',
            should_retry=True,
            retry_route='llm_intent',
        )

    def validate_rag(self, evaluation) -> ResultValidation:
        if not getattr(evaluation, 'insufficient_context', True):
            return ResultValidation(status='enough', reason='rag_enough_context')
        reason = getattr(evaluation, 'reason', '') or 'rag_insufficient_context'
        return ResultValidation(
            status='insufficient',
            reason=reason,
            should_retry=True,
            retry_route='llm_intent',
        )
