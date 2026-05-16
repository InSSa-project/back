class IntentRouter:
    """Rule-first intent router. LLM classification can be added behind this class."""

    def route(self, question: str) -> str:
        if any(keyword in question for keyword in ['일정', '시험', '마감', '제출']):
            return 'calendar_rag'
        if any(keyword in question for keyword in ['과락', '출석', '위험']):
            return 'risk_rag'
        if any(keyword in question for keyword in ['요약', '정리']):
            return 'summary_rag'
        return 'general_rag'
