from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ai_server.retrieval.query_parser import ParsedQuery, ScheduleQueryType


@dataclass
class ScheduleQueryConstraints:
    query_type: str = ScheduleQueryType.SCHEDULE_RANGE
    start_date: str = ''
    end_date: str = ''
    exact_match_required: bool = False
    display_label: str = ''
    include_filters: list[str] = field(default_factory=list)
    exclude_filters: list[str] = field(default_factory=list)
    rank: int = 0
    scope: str = ''
    source: str = 'rule'

    def to_parsed_query(self) -> ParsedQuery:
        return ParsedQuery(
            query_type=self.query_type,
            start_date=self.start_date,
            end_date=self.end_date,
            exact_match_required=self.exact_match_required,
            display_label=self.display_label,
            result_limit=0,
        )

    def as_usage(self) -> dict[str, Any]:
        return {
            'constraint_start_date': self.start_date,
            'constraint_end_date': self.end_date,
            'constraint_include_filters': self.include_filters,
            'constraint_exclude_filters': self.exclude_filters,
            'constraint_rank': self.rank,
            'constraint_scope': self.scope,
            'constraint_source': self.source,
        }


class ScheduleConstraintMerger:
    """Merge date parsing and intent/filter parsing into one DB constraint."""

    def __init__(self, date_extractor):
        self.date_extractor = date_extractor

    def from_decision(self, parsed_query, decision) -> ScheduleQueryConstraints:
        start_date = getattr(parsed_query, 'start_date', '')
        end_date = getattr(parsed_query, 'end_date', '')
        if not start_date or not end_date:
            start_date, end_date, _exact = self.date_extractor.future_range(30)
        exclude_filters = self._unique(getattr(decision, 'exclude_filters', []) or [])
        include_filters = [item for item in self._unique(getattr(decision, 'filters', []) or []) if item not in exclude_filters]
        return ScheduleQueryConstraints(
            query_type=ScheduleQueryType.SCHEDULE_RANGE,
            start_date=start_date,
            end_date=end_date,
            exact_match_required=False,
            display_label=getattr(parsed_query, 'display_label', '') or self._display_label(include_filters, getattr(decision, 'rank', 0)),
            include_filters=include_filters,
            exclude_filters=exclude_filters,
            rank=getattr(decision, 'rank', 0) or 0,
            scope=self._scope(include_filters, exclude_filters),
            source=getattr(decision, 'reason', '') or 'server_verified',
        )

    def from_llm_intent(self, parsed_query, intent_result) -> ScheduleQueryConstraints:
        start_date = intent_result.start_date or getattr(parsed_query, 'start_date', '')
        end_date = intent_result.end_date or getattr(parsed_query, 'end_date', '')
        if not start_date or not end_date:
            start_date, end_date, _exact = self.date_extractor.future_range(30)
        exclude_filters = self._unique(intent_result.exclude_filters or [])
        include_filters = [item for item in self._unique(intent_result.filters or []) if item not in exclude_filters]
        return ScheduleQueryConstraints(
            query_type=ScheduleQueryType.SCHEDULE_RANGE,
            start_date=start_date,
            end_date=end_date,
            exact_match_required=False,
            display_label=getattr(parsed_query, 'display_label', '') or self._display_label(include_filters, intent_result.rank),
            include_filters=include_filters,
            exclude_filters=exclude_filters,
            rank=intent_result.rank or 0,
            scope=self._scope(include_filters, exclude_filters),
            source='llm_intent',
        )

    def _display_label(self, filters: list[str], rank: int) -> str:
        if rank:
            return f'{rank}번째로 가까운 일정'
        if 'important' in filters:
            return '중요 일정'
        if 'exam' in filters:
            return '평가 일정'
        if 'deadline' in filters or 'assignment' in filters:
            return '마감 일정'
        return '요청한 일정'

    def _scope(self, include_filters: list[str], exclude_filters: list[str]) -> str:
        if 'personal' in include_filters:
            return 'personal'
        if 'public' in include_filters or 'personal' in exclude_filters:
            return 'public'
        return ''

    def _unique(self, values) -> list[str]:
        return list(dict.fromkeys(str(value) for value in values if value))
