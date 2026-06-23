from dataclasses import dataclass, field

from ai_server.classification.query_classifier import QueryType
from ai_server.classification.server_verified_router import VerifiedRoute
from ai_server.retrieval.query_parser import ScheduleQueryType


@dataclass
class RuleParserResult:
    intent: str
    query_type: str = ''
    start_date: str = ''
    end_date: str = ''
    exact_match_required: bool = False
    filters: list[str] = field(default_factory=list)
    exclude_filters: list[str] = field(default_factory=list)
    rank: int = 0
    confidence: float = 0.0
    reason: str = ''
    route: str = ''
    source: str = 'rule_parser'


class RuleParser:
    def parse(self, question: str, parsed_query, classified: str, verified_decision) -> RuleParserResult:
        route = getattr(verified_decision, 'route', '') or VerifiedRoute.LLM
        if route == VerifiedRoute.CURRENT_DATE:
            return self._result('current_date', parsed_query, verified_decision, 0.98)
        if route in {VerifiedRoute.PERSONAL_SCORE, VerifiedRoute.PERSONAL_RISK, VerifiedRoute.RECOMMENDED_SCHEDULE}:
            return self._result(route, parsed_query, verified_decision, 0.9)
        if route == VerifiedRoute.IMPORTANT_SCHEDULE:
            return self._result('schedule_query', parsed_query, verified_decision, 0.88)
        if route == VerifiedRoute.SCHEDULE_DB:
            return self._result('schedule_query', parsed_query, verified_decision, self._schedule_confidence(parsed_query))
        if route == VerifiedRoute.RAG:
            return self._result('official_rag_query', parsed_query, verified_decision, 0.82)
        if route == VerifiedRoute.LLM_INTENT:
            return self._result('schedule_query', parsed_query, verified_decision, 0.62)
        if classified == QueryType.GENERAL_TECH:
            return self._result('general_tech', parsed_query, verified_decision, 0.58)
        if classified == QueryType.GENERAL_ADVICE:
            return self._result('general_advice', parsed_query, verified_decision, 0.55)
        return self._result('unknown', parsed_query, verified_decision, 0.35)

    def _result(self, intent: str, parsed_query, verified_decision, confidence: float) -> RuleParserResult:
        return RuleParserResult(
            intent=intent,
            query_type=getattr(parsed_query, 'query_type', '') or '',
            start_date=getattr(parsed_query, 'start_date', '') or '',
            end_date=getattr(parsed_query, 'end_date', '') or '',
            exact_match_required=bool(getattr(parsed_query, 'exact_match_required', False)),
            filters=list(getattr(verified_decision, 'filters', []) or []),
            exclude_filters=list(getattr(verified_decision, 'exclude_filters', []) or []),
            rank=int(getattr(verified_decision, 'rank', 0) or 0),
            confidence=round(float(confidence), 2),
            reason=getattr(verified_decision, 'reason', '') or '',
            route=getattr(verified_decision, 'route', '') or '',
        )

    def _schedule_confidence(self, parsed_query) -> float:
        query_type = getattr(parsed_query, 'query_type', '')
        if query_type == ScheduleQueryType.SCHEDULE_EXACT_DATE:
            return 0.92
        if query_type in {ScheduleQueryType.SCHEDULE_RANGE, ScheduleQueryType.SCHEDULE_MONTH}:
            return 0.84
        return 0.74
