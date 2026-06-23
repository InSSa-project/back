from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class LLMIntentResult:
    intent: str = 'unknown'
    confidence: float = 0.0
    route: str = 'none'
    data_sources: list[str] = field(default_factory=list)
    date_range_type: str = ''
    start_date: str = ''
    end_date: str = ''
    filters: list[str] = field(default_factory=list)
    exclude_filters: list[str] = field(default_factory=list)
    rank: int = 0
    requires_personal_context: bool = False
    answer_mode: str = 'direct'
    reason: str = ''
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_schedule_intent(self) -> bool:
        return self.intent in {'schedule_query', 'schedule_recommendation', 'important_schedule'}


class LLMIntentClassifier:
    """Low-frequency fallback classifier for ambiguous queries.

    RuleParser remains the first path. This class is only used when a rule-based
    route is ambiguous or returns no schedule rows. The server still validates
    dates, filters, permissions, and DB queries after this classifier responds.
    """

    ALLOWED_INTENTS = {
        'schedule_query',
        'schedule_recommendation',
        'important_schedule',
        'official_notice_query',
        'official_rule_query',
        'personal_score_query',
        'personal_risk_query',
        'general_development',
        'general_chat',
        'unknown',
    }
    ALLOWED_FILTERS = {
        'exam', 'subject_exam', 'monthly_exam', 'deadline', 'assignment',
        'project', 'personal', 'public', 'holiday', 'notice', 'important',
        'study', 'online_week',
    }
    ALLOWED_DATE_RANGE_TYPES = {
        '', 'today', 'tomorrow', 'this_week', 'next_week', 'this_month',
        'next_month', 'explicit', 'upcoming', 'future',
    }
    ALLOWED_ROUTES = {'db', 'rag', 'llm', 'hybrid', 'clarify', 'none'}
    ALLOWED_DATA_SOURCES = {
        'schedule',
        'notice',
        'official_docs',
        'risk',
        'score',
        'memory',
        'chat_history',
        'rag',
        'calendar',
        'user_profile',
    }

    def __init__(self, llm_client, today: date | None = None):
        self.llm_client = llm_client
        self.today = today or date.today()

    def classify(self, question: str, rule_summary: dict | None = None) -> LLMIntentResult:
        if not self.llm_client:
            return LLMIntentResult(reason='no_llm_client')
        messages = self._messages(question, rule_summary or {})
        try:
            response = self.llm_client.complete(messages)
        except Exception as exc:  # defensive: classifier must not break chat
            return LLMIntentResult(reason=f'llm_intent_exception:{type(exc).__name__}')
        answer = str((response or {}).get('answer') or '')
        payload = self._parse_json(answer)
        result = self._coerce(payload)
        result.raw = payload
        return result

    def _messages(self, question: str, rule_summary: dict) -> list[dict]:
        system = (
            'You classify Korean SSAFY assistant questions. Return JSON only. '
            'Do not answer the user. The server will verify every result. '
            'Allowed intents: schedule_query, schedule_recommendation, important_schedule, '
            'official_notice_query, official_rule_query, personal_score_query, personal_risk_query, '
            'general_development, general_chat, unknown. '
            'Allowed filters: exam, subject_exam, monthly_exam, deadline, assignment, project, '
            'personal, public, holiday, notice, important, study, online_week. '
            'Use ISO date strings when dates are obvious from the question or rule summary. '
            'If a user asks for the Nth closest item, set rank to N. '
            'If the question asks to exclude something, put it in exclude_filters. '
            'Set route to where the server should retrieve or answer from: db, rag, llm, hybrid, clarify, none. '
            'Set data_sources to the required verified sources. '
            'Schema: {"intent":"...","confidence":0.0,'
            '"route":"db","data_sources":["schedule"],'
            '"date_range":{"type":"this_week","start_date":"YYYY-MM-DD","end_date":"YYYY-MM-DD"},'
            '"filters":[],"exclude_filters":[],"rank":0,'
            '"requires_personal_context":false,"answer_mode":"direct","reason":"..."}'
        )
        user = json.dumps(
            {
                'today': self.today.isoformat(),
                'question': question,
                'rule_summary': rule_summary,
            },
            ensure_ascii=False,
            default=str,
        )
        return [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]

    def _parse_json(self, text: str) -> dict:
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
        match = re.search(r'\{.*\}', text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _coerce(self, payload: dict) -> LLMIntentResult:
        intent = str(payload.get('intent') or 'unknown').strip()
        if intent not in self.ALLOWED_INTENTS:
            intent = 'unknown'
        confidence = self._float_between(payload.get('confidence'), 0.0, 1.0)
        route = str(payload.get('route') or 'none').strip().lower()
        if route not in self.ALLOWED_ROUTES:
            route = 'none'
        data_sources = self._coerce_data_sources(payload.get('data_sources'))
        rank = self._int_between(payload.get('rank'), 0, 20)
        filters = self._coerce_filter_list(payload.get('filters'))
        exclude_filters = self._coerce_filter_list(payload.get('exclude_filters'))
        date_range = payload.get('date_range') if isinstance(payload.get('date_range'), dict) else {}
        date_range_type = str(date_range.get('type') or payload.get('date_range_type') or '').strip()
        if date_range_type not in self.ALLOWED_DATE_RANGE_TYPES:
            date_range_type = ''
        start_date = self._date_string(date_range.get('start_date') or payload.get('start_date'))
        end_date = self._date_string(date_range.get('end_date') or payload.get('end_date'))
        return LLMIntentResult(
            intent=intent,
            confidence=confidence,
            route=route,
            data_sources=data_sources,
            date_range_type=date_range_type,
            start_date=start_date,
            end_date=end_date,
            filters=filters,
            exclude_filters=exclude_filters,
            rank=rank,
            requires_personal_context=bool(payload.get('requires_personal_context') or False),
            answer_mode=str(payload.get('answer_mode') or 'direct')[:40],
            reason=str(payload.get('reason') or '')[:200],
        )

    def _coerce_data_sources(self, value) -> list[str]:
        if not isinstance(value, list):
            return []
        result = []
        for item in value:
            normalized = str(item or '').strip().lower()
            if normalized in self.ALLOWED_DATA_SOURCES and normalized not in result:
                result.append(normalized)
        return result

    def _coerce_filter_list(self, value) -> list[str]:
        if not isinstance(value, list):
            return []
        result = []
        for item in value:
            normalized = str(item or '').strip().lower()
            if normalized in self.ALLOWED_FILTERS and normalized not in result:
                result.append(normalized)
        return result

    def _date_string(self, value) -> str:
        text = str(value or '').strip()
        return text if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text) else ''

    def _float_between(self, value, minimum: float, maximum: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return minimum
        return max(minimum, min(maximum, number))

    def _int_between(self, value, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return minimum
        return max(minimum, min(maximum, number))
