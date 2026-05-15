class QueryClassifier:
    def classify(self, question: str) -> str:
        rules = {
            'schedule': ['일정', '언제', '마감', '제출', '시험 날짜'],
            'exam': ['문제', '기출', '평가', '월말', '과목평가'],
            'policy': ['규정', '출석', '과락', '퇴소', '공가'],
            'mentoring': ['조언', '멘토', '분위기', '팀원', '어떻게'],
            'action_guide': ['뭐 해야', '우선', '추천', '가이드'],
        }
        for intent, keywords in rules.items():
            if any(keyword in question for keyword in keywords):
                return intent
        return 'general'
