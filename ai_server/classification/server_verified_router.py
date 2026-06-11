from __future__ import annotations

import codecs
import re
from dataclasses import dataclass, field


class VerifiedRoute:
    CURRENT_DATE = 'current_date'
    PERSONAL_SCORE = 'personal_score'
    PERSONAL_RISK = 'personal_risk'
    RECOMMENDED_SCHEDULE = 'recommended_schedule'
    IMPORTANT_SCHEDULE = 'important_schedule'
    SCHEDULE_DB = 'schedule_db'
    RAG = 'rag'
    LLM_INTENT = 'llm_intent'
    LLM = 'llm'


@dataclass
class IntentDecision:
    route: str
    confidence: float = 1.0
    reason: str = ''
    filters: list[str] = field(default_factory=list)
    exclude_filters: list[str] = field(default_factory=list)
    rank: int = 0
    requires_personal_context: bool = False


class ServerVerifiedIntentRouter:
    """High-confidence Korean route rules before loose LLM handling."""

    def decide(self, question: str, parsed_query=None, classified: str = '') -> IntentDecision:
        text = self._normalize(question)
        compact = text.replace(' ', '')

        if any(pattern in compact for pattern in self._words('\\uc624\\ub298\\ub0a0\\uc9dc', '\\ud604\\uc7ac\\ub0a0\\uc9dc', '\\uc624\\ub298\\uba70\\uce60', '\\uc624\\ub298\\uc774\\uba87\\uc77c', '\\uc624\\ub298\\uc774\\ubb34\\uc2a8\\ub0a0')):
            return IntentDecision(VerifiedRoute.CURRENT_DATE, reason='current_date_rule')
        if self._is_notice_question(text):
            return IntentDecision(VerifiedRoute.RAG, reason='notice_question')
        if self._is_urgent_recommendation_question(text):
            return IntentDecision(VerifiedRoute.RECOMMENDED_SCHEDULE, reason='urgent_recommendation_rule', requires_personal_context=True)
        if self._is_recommendation_question(text):
            return IntentDecision(VerifiedRoute.RECOMMENDED_SCHEDULE, reason='recommendation_rule', requires_personal_context=True)
        if self._is_weak_subject_question(text):
            return IntentDecision(VerifiedRoute.PERSONAL_SCORE, reason='weak_subject_rule', requires_personal_context=True)
        if self._is_personal_risk_question(text):
            return IntentDecision(VerifiedRoute.PERSONAL_RISK, reason='personal_risk_rule', requires_personal_context=True)
        if self._is_bare_risk_question(text):
            return IntentDecision(VerifiedRoute.PERSONAL_RISK, reason='bare_risk_rule', requires_personal_context=True)
        if self._is_personal_score_question(text):
            return IntentDecision(VerifiedRoute.PERSONAL_SCORE, reason='personal_score_rule', requires_personal_context=True)
        if self._is_important_context_question(text):
            return IntentDecision(VerifiedRoute.IMPORTANT_SCHEDULE, reason='important_context_rule', requires_personal_context=True)
        if self._is_exam_upcoming_question(text):
            return IntentDecision(
                VerifiedRoute.LLM_INTENT,
                reason='exam_upcoming_rule',
                filters=['exam'],
                exclude_filters=self._exclude_filters(text),
                rank=self._rank(text),
            )
        if self._is_complex_schedule_question(text):
            return IntentDecision(
                VerifiedRoute.LLM_INTENT,
                reason='complex_schedule_rule',
                filters=self._include_filters(text),
                exclude_filters=self._exclude_filters(text),
                rank=self._rank(text),
            )
        if getattr(parsed_query, 'query_type', '') and getattr(parsed_query, 'query_type', '') != 'GENERAL_CHAT':
            return IntentDecision(VerifiedRoute.SCHEDULE_DB, reason='parsed_schedule_rule')
        if classified == 'SSAFY_OFFICIAL':
            return IntentDecision(VerifiedRoute.RAG, reason='official_classifier')
        return IntentDecision(VerifiedRoute.LLM, confidence=0.5, reason='default')


    def _is_urgent_recommendation_question(self, text: str) -> bool:
        compact = text.replace(' ', '')
        urgent_words = self._words(
            '\uc228\ub9c9\ud788', '\ud070\uc77c', '\uc704\ud5d8\ud55c', '\uc704\ud5d8\ud558\ub2e4',
            '\uc704\ud5d8\ud55c\uac70', '\uc704\ud5d8\ud55c\uac83', '\ub9dd\ud558\ub294', '\ub9dd\ud560',
            '\ub2f9\uc7a5\uc900\ube44', '\uc81c\uc77c\uc704\ud5d8', '\uc704\ud5d8\ub3c4',
        )
        has_urgent = self._contains_any(compact, urgent_words)
        has_time = self._contains_any(compact, self._words('\uc774\ubc88\uc8fc', '\uace7', '\ub2e4\uac00\uc624\ub294', '\ub2f9\uc7a5', '\uc81c\uc77c'))
        return has_urgent and (has_time or self._contains_any(compact, self._words('\ub098', '\ub0b4')))

    def _is_exam_upcoming_question(self, text: str) -> bool:
        compact = text.replace(' ', '')
        has_exam = self._contains_any(text, self._words('\uc2dc\ud5d8', '\ud3c9\uac00', '\uacfc\ubaa9\ud3c9\uac00', '\uc6d4\ub9d0\ud3c9\uac00'))
        has_upcoming = self._contains_any(compact, self._words('\uac00\uae4c\uc6b4', '\ub2e4\uac00\uc624\ub294', '\uace7', '\uc788\uc74c', '\uc788\ub0d0', '\uc788\uc5b4'))
        return has_exam and has_upcoming
    def _is_notice_question(self, text: str) -> bool:
        return self._contains_any(text, self._words('\\uacf5\\uc9c0', '\\uacf5\\uc9c0\\uc0ac\\ud56d', '\\uc548\\ub0b4', '\\uc54c\\ub9bc')) and self._contains_any(
            text,
            self._words('\\uc694\\uc57d', '\\uc815\\ub9ac', '\\uc54c\\ub824', '\\ubcf4\\uc5ec', '\\ud655\\uc778', '\\uc911\\uc694', '\\ucd5c\\uadfc'),
        )


    def _is_weak_subject_question(self, text: str) -> bool:
        compact = text.replace(' ', '')
        return self._contains_any(compact, self._words('\uacfc\ub77d\uac00\ub2a5\uc131', '\uc57d\ud55c\uacfc\ubaa9', '\ub0ae\uc740\uacfc\ubaa9')) and self._contains_any(
            compact,
            self._words('\uacfc\ubaa9', '\uc131\uc801', '\uc810\uc218', '\ud3c9\uac00'),
        )

    def _is_bare_risk_question(self, text: str) -> bool:
        compact = text.replace(' ', '')
        return self._contains_any(compact, self._words('\uc704\ud5d8\ub3c4\ud3c9\uac00', '\uc704\ud5d8\ub3c4', '\uc218\ub8cc\uac00\ub2a5')) and self._contains_any(
            compact,
            self._words('\uc774\ubc88\ub2ec', '\ud604\uc7ac', '\uc9c0\uae08', '\uc131\uc801', '\ud3c9\uac00'),
        )
    def _is_personal_score_question(self, text: str) -> bool:
        if self._contains_any(text, self._words('\\uae30\\uc900', '\\uc870\\uac74', '\\uaddc\\uc815', '\\ud1b5\\uacfc \\uae30\\uc900', '\\uc218\\ub8cc \\uae30\\uc900')):
            return False
        personal = self._contains_any(text, self._words('\\ub0b4 ', '\\ub0b4\\uac00', '\\ub098\\ub294', '\\ub098\\uc758', '\\ud604\\uc7ac', '\\uc9c0\\uae08', '\\uc785\\ub825\\ud55c', '\\ubc1b\\uc740'))
        score = self._contains_any(text, self._words('\\uc131\\uc801', '\\uc810\\uc218', '\\ud3c9\\uade0', '\\ud569\\uaca9 \\ud69f\\uc218', '\\ubd88\\ud569\\uaca9 \\ud69f\\uc218', '\\uacfc\\ub77d \\ud69f\\uc218', '\\uba87 \\ubc88 \\uacfc\\ub77d', '\\uba87\\ubc88 \\uacfc\\ub77d'))
        return personal and score

    def _is_personal_risk_question(self, text: str) -> bool:
        personal = self._contains_any(text, self._words('\\ub0b4 ', '\\ub0b4\\uac00', '\\ub098\\ub294', '\\ub098\\uc758', '\\ud604\\uc7ac', '\\uc9c0\\uae08', '\\uc131\\uc801 \\uae30\\uc900'))
        risk = self._contains_any(text, self._words('\\uc704\\ud5d8', '\\uc704\\ud5d8\\ud574', '\\uad1c\\ucc2e', '\\uc218\\ub8cc \\uac00\\ub2a5', '\\uc218\\ub8cc \\uc0c1\\ud0dc', '\\ud1f4\\uc18c', '\\uacfc\\ub77d \\uc5ec\\uc720', '\\ub0b4 \\uc0c1\\ud0dc'))
        score_context = self._contains_any(text, self._words('\\uc131\\uc801', '\\uc810\\uc218', '\\uacfc\\ub77d', '\\ud3c9\\uac00', '\\uc218\\ub8cc'))
        return personal and risk and (score_context or self._word('\\ub0b4 \\uc0c1\\ud0dc') in text)

    def _is_recommendation_question(self, text: str) -> bool:
        compact = text.replace(' ', '')
        direct = self._words('\\ubb50\\ubd80\\ud130', '\\ubb34\\uc5c7\\ubd80\\ud130', '\\ubbf8\\uc900\\ube44', '\\ubb50\\uc900\\ube44', '\\uba3c\\uc800\\ubb50', '\\uc2e0\\uacbd\\uc368\\uc57c', '\\uc2e0\\uacbd\\uc368\\uc57c\\ud560', '\\ube61\\uc13c\\uac70', '\\uc870\\uc2ec\\ud574\\uc57c')
        return self._contains_any(compact, direct) or (
            self._contains_any(text, self._words('\\ucd94\\ucc9c', '\\uba3c\\uc800', '\\uc900\\ube44', '\\uc2e0\\uacbd', '\\ube61\\uc13c', '\\uc870\\uc2ec'))
            and self._contains_any(text, self._words('\\uc77c\\uc815', '\\uc2a4\\ucf00\\uc904', '\\ud560 \\uc77c', '\\ud574\\uc57c', '\\ud3c9\\uac00', '\\ub9c8\\uac10'))
        )

    def _is_important_context_question(self, text: str) -> bool:
        compact = text.replace(' ', '')
        return self._contains_any(compact, self._words('\\ucd5c\\uadfc\\uc911\\uc694\\uc77c\\uc815', '\\uc911\\uc694\\uc77c\\uc815\\ubcf4\\uc5ec', '\\uc911\\uc694\\uc77c\\uc815\\ud655\\uc778'))

    def _is_complex_schedule_question(self, text: str) -> bool:
        if not self._contains_any(text, self._words('\\uc77c\\uc815', '\\uc2a4\\ucf00\\uc904', '\\ud3c9\\uac00', '\\uc2dc\\ud5d8', '\\ub9c8\\uac10', '\\uc81c\\ucd9c', '\\uc911\\uc694', '\\ud504\\ub85c\\uc81d\\ud2b8', '\\uacf5\\ud734\\uc77c')):
            return False
        if self._exclude_filters(text) or self._rank(text):
            return True
        if self._contains_any(text, self._words('\\uc911\\uc694', '\\uc6b0\\uc120\\uc21c\\uc704', '\\uacf5\\uc6a9', '\\uacf5\\uc2dd', '\\uac1c\\uc778', '\\ub0b4 \\uc77c\\uc815')):
            return True
        compact = text.replace(' ', '')
        return self._contains_any(compact, self._words('\\ud3c9\\uac00\\ub9cc', '\\uc2dc\\ud5d8\\ub9cc', '\\ub9c8\\uac10\\ub9cc', '\\uc81c\\ucd9c\\ub9cc', '\\ud504\\ub85c\\uc81d\\ud2b8\\ub9cc', '\\ud504\\ub85c\\uc81d\\ud2b8\\uc77c\\uc815\\ub9cc', '\\uacf5\\ud734\\uc77c\\ub9cc'))

    def _include_filters(self, text: str) -> list[str]:
        filters = []
        if self._contains_any(text, self._words('\\ub9c8\\uac10\\ub9cc', '\\uc81c\\ucd9c\\ub9cc', '\\ub9c8\\uac10 \\uc54c\\ub824', '\\ub9c8\\uac10 \\uc77c\\uc815', '\\ub9c8\\uac10 \\uc911')):
            filters.extend(['deadline', 'assignment'])
        if self._contains_any(text, self._words('\\ud3c9\\uac00\\ub9cc', '\\uc2dc\\ud5d8\\ub9cc', '\\ud3c9\\uac00 \\uc77c\\uc815', '\\uc2dc\\ud5d8 \\ub0a0\\uc9dc', '\\uc2dc\\ud5d8 \\uc77c\\uc815', '\\ud3c9\\uac00 \\uc54c\\ub824', '\\uc2dc\\ud5d8 \\uc54c\\ub824')):
            filters.append('exam')
        if self._contains_any(text, self._words('\\ud504\\ub85c\\uc81d\\ud2b8\\ub9cc', '\\ud504\\ub85c\\uc81d\\ud2b8 \\uc77c\\uc815', '\\ud504\\ub85c\\uc81d\\ud2b8 \\uc77c\\uc815\\ub9cc', '\\ud504\\ub85c\\uc81d\\ud2b8 \\uc54c\\ub824')):
            filters.append('project')
        if self._contains_any(text, self._words('\\uacf5\\ud734\\uc77c\\ub9cc', '\\ud734\\uc77c\\ub9cc')):
            filters.append('holiday')
        if self._contains_any(text, self._words('\\uc911\\uc694', '\\uc6b0\\uc120\\uc21c\\uc704')):
            filters.append('important')
        if self._contains_any(text, self._words('\\uac1c\\uc778', '\\ub0b4 \\uc77c\\uc815')):
            filters.append('personal')
        if self._contains_any(text, self._words('\\uacf5\\uc6a9', '\\uacf5\\uc2dd')):
            filters.append('public')
        return list(dict.fromkeys(filters))

    def _exclude_filters(self, text: str) -> list[str]:
        compact = text.replace(' ', '')
        excludes = []
        exclude_words = self._words('\\ub9d0\\uace0', '\\ube7c\\uace0', '\\uc81c\\uc678', '\\uc81c\\uc678\\ud558\\uace0')
        if self._has_exclusion(compact, self._words('\\ud3c9\\uac00', '\\uc2dc\\ud5d8'), exclude_words):
            excludes.append('exam')
        if self._has_exclusion(compact, self._words('\\ub9c8\\uac10', '\\uc81c\\ucd9c', '\\uacfc\\uc81c'), exclude_words):
            excludes.extend(['deadline', 'assignment'])
        if self._has_exclusion(compact, self._words('\\ud504\\ub85c\\uc81d\\ud2b8'), exclude_words):
            excludes.append('project')
        if self._has_exclusion(compact, self._words('\\uacf5\\ud734\\uc77c', '\\ud734\\uc77c'), exclude_words):
            excludes.append('holiday')
        if self._has_exclusion(compact, self._words('\\uac1c\\uc778', '\\uac1c\\uc778\\uc77c\\uc815', '\\ub0b4\\uc77c\\uc815'), exclude_words):
            excludes.append('personal')
        return list(dict.fromkeys(excludes))

    def _has_exclusion(self, compact: str, targets, exclude_words) -> bool:
        return any(target + exclude_word in compact for target in targets for exclude_word in exclude_words)

    def _rank(self, text: str) -> int:
        compact = text.replace(' ', '')
        if any(word in compact for word in self._words('\\ub450\\ubc88\\uc9f8', '\\ub450\\ubc88\\uc9f8\\ub85c', '2\\ubc88\\uc9f8', '\\ub458\\uc9f8')):
            return 2
        if any(word in compact for word in self._words('\\uc138\\ubc88\\uc9f8', '\\uc138\\ubc88\\uc9f8\\ub85c', '3\\ubc88\\uc9f8', '\\uc14b\\uc9f8')):
            return 3
        if any(word in compact for word in self._words('\\uccab\\ubc88\\uc9f8', '\\uccab\\ubc88\\uc9f8\\ub85c', '1\\ubc88\\uc9f8', '\\uc81c\\uc77c')):
            return 1
        return 0

    def _normalize(self, question: str) -> str:
        return re.sub(r'\s+', ' ', (question or '').lower()).strip()

    def _contains_any(self, text: str, values) -> bool:
        return any(value in text for value in values)

    def _words(self, *values: str) -> tuple[str, ...]:
        return tuple(self._word(value) for value in values)

    def _word(self, value: str) -> str:
        if '\\u' in value or '\\U' in value or '\\x' in value:
            return codecs.decode(value, 'unicode_escape')
        return value
