import re
from dataclasses import dataclass


class DomainIntent:
    CURRENT_DATE = 'CURRENT_DATE'
    PERSONAL_SCORE = 'PERSONAL_SCORE'
    PERSONAL_RISK = 'PERSONAL_RISK'
    RECOMMENDED_SCHEDULE = 'RECOMMENDED_SCHEDULE'
    IMPORTANT_SCHEDULE = 'IMPORTANT_SCHEDULE'
    GENERAL = 'GENERAL'


@dataclass
class DomainIntentDecision:
    intent: str
    confidence: str = 'HIGH'


class DomainIntentRouter:
    """Route private dashboard questions before general schedule/RAG handling."""

    OFFICIAL_MARKERS = ('기준', '조건', '규정', '몇 점 이상', '몇점 이상', '통과 기준', '수료 기준')
    PERSONAL_MARKERS = ('내 ', '내가', '나는', '나의', '현재', '지금', '입력한', '받은')

    def route(self, question: str) -> DomainIntentDecision:
        normalized = self._normalize(question)
        compact = normalized.replace(' ', '')

        if any(pattern in compact for pattern in ('오늘날짜', '현재날짜', '오늘며칠', '오늘이몇일', '오늘이무슨날')):
            return DomainIntentDecision(DomainIntent.CURRENT_DATE)

        if self._is_recommendation_question(normalized):
            return DomainIntentDecision(DomainIntent.RECOMMENDED_SCHEDULE)
        if self._is_important_schedule_question(normalized):
            return DomainIntentDecision(DomainIntent.IMPORTANT_SCHEDULE)
        if self._is_personal_risk_question(normalized):
            return DomainIntentDecision(DomainIntent.PERSONAL_RISK)
        if self._is_personal_score_question(normalized):
            return DomainIntentDecision(DomainIntent.PERSONAL_SCORE)
        return DomainIntentDecision(DomainIntent.GENERAL)

    def _is_personal_score_question(self, text: str) -> bool:
        if self._contains_any(text, self.OFFICIAL_MARKERS):
            return False
        score_terms = ('성적', '점수', '몇 점', '몇점', '평균', '합격 횟수', '불합격 횟수', '과락 횟수', '몇 번 과락', '몇번 과락', '과락 몇 번', '과락 몇번')
        return self._contains_any(text, score_terms) and (
            self._contains_any(text, self.PERSONAL_MARKERS)
            or self._contains_any(text, ('알려', '보여', '확인', '어때', '얼마'))
        )

    def _is_personal_risk_question(self, text: str) -> bool:
        if self._contains_any(text, self.OFFICIAL_MARKERS) and not self._contains_any(text, self.PERSONAL_MARKERS):
            return False
        risk_terms = ('위험도', '위험 단계', '현재 상태', '내 상태', '수료 상태', '수료 가능', '퇴소 위험', '한 번 더 과락', '한번 더 과락', '과락 여유')
        return self._contains_any(text, risk_terms)

    def _is_recommendation_question(self, text: str) -> bool:
        compact = text.replace(' ', '')
        direct_phrases = ('뭐부터', '무엇부터', '뭘준비', '무엇을준비', '뭐준비')
        recommendation_terms = ('추천', '우선할', '먼저')
        action_terms = ('일정', '스케줄', '준비', '공부', '해야', '할일', '할 일')
        return self._contains_any(compact, direct_phrases) or (
            self._contains_any(text, recommendation_terms) and self._contains_any(text, action_terms)
        )

    def _is_important_schedule_question(self, text: str) -> bool:
        importance_terms = ('중요', '급한', '긴급', '우선순위')
        schedule_terms = ('일정', '스케줄', '할 일', '할일', '마감', '시험', '평가')
        return self._contains_any(text, importance_terms) and self._contains_any(text, schedule_terms)

    def _normalize(self, question: str) -> str:
        return re.sub(r'\s+', ' ', (question or '').lower()).strip()

    def _contains_any(self, text: str, values) -> bool:
        return any(value in text for value in values)



