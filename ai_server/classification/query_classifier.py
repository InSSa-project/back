import re


class QueryType:
    SSAFY_OFFICIAL = 'SSAFY_OFFICIAL'
    GENERAL_TECH = 'GENERAL_TECH'
    GENERAL_ADVICE = 'GENERAL_ADVICE'
    UNKNOWN = 'UNKNOWN'


class QueryClassifier:
    """Rule-based classifier that can be replaced by an LLM classifier later."""

    SSAFY_OFFICIAL_KEYWORDS = [
        'ssafy', '싸피', '공지', '일정', '언제', '마감', '제출', '평가', '월말평가',
        '과락', '출결', '결석', '지각', '캠퍼스', '반 공지', '반정보', 'lms',
        'mattermost', '매터모스트', '컨설턴트', '코치', '운영', '규정', '기준',
    ]
    GENERAL_TECH_KEYWORDS = [
        'python', 'django', 'fastapi', 'git', 'github', 'javascript', 'react',
        'sql', 'db', 'database', 'api', 'rest', 'http', '배포', '서버', '오류',
        '에러', '코드', '알고리즘', '자료구조', 'dfs', 'bfs', 'foreignkey',
        '모델', 'serializer', 'viewset', '함수', '클래스',
    ]
    GENERAL_ADVICE_KEYWORDS = [
        '어떻게 하면', '어떻게 해', '어떻게 하지', '준비', '관리', '멘토링',
        '역할 분담', '팀원', '협업', '프로젝트 일정', '시험 대비', '공부',
        '계획', '가이드', '추천', '조언',
    ]

    def classify(self, question: str) -> str:
        normalized = question.lower().strip()
        if self._contains_any(normalized, self.GENERAL_TECH_KEYWORDS):
            return QueryType.GENERAL_TECH
        if self._contains_any(normalized, self.SSAFY_OFFICIAL_KEYWORDS):
            if self._looks_like_general_advice(normalized):
                return QueryType.GENERAL_ADVICE
            return QueryType.SSAFY_OFFICIAL
        if self._contains_any(normalized, self.GENERAL_ADVICE_KEYWORDS):
            return QueryType.GENERAL_ADVICE
        return QueryType.UNKNOWN

    def _contains_any(self, text: str, keywords: list[str]) -> bool:
        return any(keyword.lower() in text for keyword in keywords)

    def _looks_like_general_advice(self, text: str) -> bool:
        return bool(re.search(r'(어떻게|준비|관리|조언|추천|가이드)', text))
