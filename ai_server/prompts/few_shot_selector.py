import re


class FewShotSelector:
    def select(self, question: str, examples: list[dict], max_count: int) -> list[dict]:
        scored = []
        normalized = question.lower()
        question_terms = set(re.findall(r'[\w가-힣]+', normalized))
        for example in examples:
            keywords = {str(item).lower() for item in example.get('keywords', [])}
            keyword_score = len([keyword for keyword in keywords if keyword in normalized])
            text_terms = set(re.findall(r'[\w가-힣]+', (example.get('user', '') + ' ' + example.get('assistant', '')).lower()))
            overlap_score = len(question_terms & text_terms)
            scored.append((keyword_score * 10 + overlap_score, example))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [example for score, example in scored[:max_count] if score > 0] or examples[:max_count]
