import re


class QueryType:
    SSAFY_OFFICIAL = 'SSAFY_OFFICIAL'
    GENERAL_TECH = 'GENERAL_TECH'
    GENERAL_ADVICE = 'GENERAL_ADVICE'
    UNKNOWN = 'UNKNOWN'


class QueryClassifier:
    """Rule-based intent classifier for SSAFY official facts vs general support."""

    GENERAL_TECH_KEYWORDS = [
        'python', 'django', 'fastapi', 'git', 'github', 'javascript', 'vue', 'react',
        'sql', 'db', 'database', 'api', 'rest', 'http', '배포', '서버', '오류', '에러',
        '코드', '알고리즘', '자료구조', 'dfs', 'bfs', 'foreignkey', 'serializer', 'viewset',
        '컴포넌트', '함수', '클래스', '모델', '마이그레이션', '충돌', 'merge', 'commit',
    ]
    EMOTIONAL_SUPPORT_KEYWORDS = [
        '힘들', '불안', '걱정', '스트레스', '우울', '멘탈', '버틸', '포기', '지쳤',
        '따라가기 힘', '못 따라가', '팀플이 힘', '팀원이', '갈등', '괴롭', '막막',
        '맞았는데', '떨어졌는데', '망했', '괜찮을까', '잘할 수 있을까',
    ]
    GENERAL_ADVICE_KEYWORDS = [
        '어떻게 하면', '어떻게 해야', '어떻게 하지', '준비', '관리', '멘토링',
        '공부 방향', '공부법', '계획', '가이드', '추천', '조언', '시간 관리',
        '집중력', '운동 루틴', '배달음식', '취업이 걱정', '프로젝트 때문에',
    ]
    SSAFY_DOMAIN_KEYWORDS = [
        'ssafy', '싸피', '과락', '수료', '재시험', '월말평가', '과목평가', '평가',
        '출결', '결석', '지각', '공지', '공지사항', 'lms', 'mattermost', '매터모스트',
        '컨설턴트', '코치', '멘토링', '프로젝트 제출', '제출일', '마감', '기준', '규정',
        '학사규정', '학사 규정', '생활수칙', '생활 수칙', '출결관리', '출결 관리',
        '퇴소', '중도퇴소', '공가', '사유결석', '교육지원금',
    ]
    OFFICIAL_FACT_PATTERNS = [
        r'기준(이|은|을|가)?\s*(뭐|무엇|알려|설명)',
        r'조건(이|은|을|가)?\s*(뭐|무엇|알려|설명)',
        r'(수료|과락|재시험|평가|출결|결석|지각).*(기준|조건|규정|점수|횟수|처리)',
        r'(출결|결석|지각|조퇴|공가|사유결석).*(하면|되면|어떻게|어케|처리|불이익)',
        r'(학사\s*규정|생활\s*수칙|출결\s*관리).*(알려|설명|뭐|무엇|내용|정리)',
        r'(학사\s*규정|생활\s*수칙|출결\s*관리)$',
        r'(과락|불합격).*(퇴소|수료|횟수|몇\s*번|몇번)',
        r'(퇴소|중도퇴소).*(기준|조건|사유|횟수|몇\s*번|몇번)',
        r'(몇\s*번|몇번).*(과락|불합격|퇴소)',
        r'(일정|제출일|마감|공지|공지사항|내용).*(알려|언제|뭐|무엇|확인|보여)',
        r'(언제|몇\s*시|몇\s*일).*(평가|시험|멘토링|마감|제출|프로젝트)',
        r'(통과|합격|불합격).*(기준|조건|점수|횟수)',
    ]

    def classify(self, question: str) -> str:
        normalized = question.lower().strip()
        if not normalized:
            return QueryType.UNKNOWN

        if self._contains_any(normalized, self.GENERAL_TECH_KEYWORDS):
            return QueryType.GENERAL_TECH

        if self._is_support_or_advice(normalized):
            return QueryType.GENERAL_ADVICE

        if self._is_official_fact_question(normalized):
            return QueryType.SSAFY_OFFICIAL

        if self._contains_any(normalized, self.GENERAL_ADVICE_KEYWORDS):
            return QueryType.GENERAL_ADVICE

        return QueryType.UNKNOWN

    def _contains_any(self, text: str, keywords: list[str]) -> bool:
        return any(keyword.lower() in text for keyword in keywords)

    def _is_support_or_advice(self, text: str) -> bool:
        return self._contains_any(text, self.EMOTIONAL_SUPPORT_KEYWORDS) or self._contains_any(text, self.GENERAL_ADVICE_KEYWORDS)

    def _is_official_fact_question(self, text: str) -> bool:
        if not self._contains_any(text, self.SSAFY_DOMAIN_KEYWORDS):
            return False
        return any(re.search(pattern, text) for pattern in self.OFFICIAL_FACT_PATTERNS)
