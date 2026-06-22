from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ServerVerifiedIntentPlan:
    intent: str = 'unknown'
    route: str = 'none'
    data_sources: list[str] = field(default_factory=list)
    confidence: float = 0.0
    allowed: bool = False
    reason: str = ''

    def as_usage(self) -> dict[str, Any]:
        return {
            'verified_intent': self.intent,
            'verified_route': self.route,
            'verified_data_sources': self.data_sources,
            'verified_confidence': self.confidence,
            'verified_allowed': self.allowed,
            'verified_reason': self.reason,
        }


class ServerVerifiedParser:
    """Validate LLM intent JSON before DB/RAG lookup decisions use it."""

    ROUTES = {'db', 'rag', 'llm', 'hybrid', 'clarify', 'none'}
    INTENT_DEFAULTS = {
        'schedule_query': ('db', ['schedule']),
        'schedule_recommendation': ('hybrid', ['schedule', 'risk']),
        'important_schedule': ('db', ['schedule']),
        'official_notice_query': ('rag', ['notice', 'rag']),
        'official_rule_query': ('rag', ['official_docs', 'rag']),
        'personal_score_query': ('db', ['score', 'user_profile']),
        'personal_risk_query': ('hybrid', ['risk', 'score', 'schedule']),
        'general_development': ('llm', []),
        'general_chat': ('llm', []),
        'unknown': ('clarify', []),
    }
    INTENT_ALLOWED_SOURCES = {
        'schedule_query': {'schedule', 'calendar', 'rag'},
        'schedule_recommendation': {'schedule', 'calendar', 'risk', 'score', 'memory', 'user_profile'},
        'important_schedule': {'schedule', 'calendar', 'risk', 'score'},
        'official_notice_query': {'notice', 'official_docs', 'rag'},
        'official_rule_query': {'official_docs', 'notice', 'rag'},
        'personal_score_query': {'score', 'user_profile'},
        'personal_risk_query': {'risk', 'score', 'schedule', 'user_profile'},
        'general_development': {'rag', 'chat_history'},
        'general_chat': {'chat_history', 'memory'},
        'unknown': set(),
    }
    ROUTE_ALLOWED_BY_INTENT = {
        'schedule_query': {'db', 'hybrid'},
        'schedule_recommendation': {'db', 'hybrid'},
        'important_schedule': {'db', 'hybrid'},
        'official_notice_query': {'rag', 'hybrid'},
        'official_rule_query': {'rag', 'hybrid'},
        'personal_score_query': {'db', 'hybrid'},
        'personal_risk_query': {'db', 'hybrid'},
        'general_development': {'llm', 'rag', 'hybrid'},
        'general_chat': {'llm'},
        'unknown': {'clarify', 'none', 'llm'},
    }

    def verify(self, intent_result) -> ServerVerifiedIntentPlan:
        intent = str(getattr(intent_result, 'intent', '') or 'unknown')
        confidence = float(getattr(intent_result, 'confidence', 0.0) or 0.0)
        default_route, default_sources = self.INTENT_DEFAULTS.get(intent, self.INTENT_DEFAULTS['unknown'])
        route = str(getattr(intent_result, 'route', '') or default_route).lower()
        if route not in self.ROUTES:
            route = default_route
        if route == 'none':
            route = default_route

        allowed_routes = self.ROUTE_ALLOWED_BY_INTENT.get(intent, {'clarify'})
        reason = 'accepted'
        if route not in allowed_routes:
            route = default_route
            reason = 'route_corrected'

        sources = self._verified_sources(intent, getattr(intent_result, 'data_sources', []), default_sources)
        allowed = confidence >= 0.55 and intent != 'unknown'
        if route == 'clarify':
            allowed = False
            reason = 'clarify_required'
        elif not allowed:
            route = 'clarify'
            sources = []
            reason = 'low_confidence'

        return ServerVerifiedIntentPlan(
            intent=intent,
            route=route,
            data_sources=sources,
            confidence=confidence,
            allowed=allowed,
            reason=reason,
        )

    def _verified_sources(self, intent: str, proposed_sources, default_sources: list[str]) -> list[str]:
        allowed_sources = self.INTENT_ALLOWED_SOURCES.get(intent, set())
        result = []
        if isinstance(proposed_sources, list):
            for source in proposed_sources:
                normalized = str(source or '').strip().lower()
                if normalized in allowed_sources and normalized not in result:
                    result.append(normalized)
        return result or list(default_sources)
