from dataclasses import dataclass

from ai_server.classification.query_classifier import QueryType


class AnswerPolicy:
    RAG_GROUNDED = 'RAG_GROUNDED'
    OFFICIAL_NO_CONTEXT = 'OFFICIAL_NO_CONTEXT'
    GENERAL_KNOWLEDGE_FALLBACK = 'GENERAL_KNOWLEDGE_FALLBACK'
    GENERAL_ADVICE_FALLBACK = 'GENERAL_ADVICE_FALLBACK'
    CAUTIOUS_FALLBACK = 'CAUTIOUS_FALLBACK'


@dataclass
class AnswerPolicyDecision:
    answer_policy: str
    use_llm: bool
    use_references: bool
    fallback_prefix: str = ''


class AnswerPolicyRouter:
    def decide(self, query_type: str, retrieval_evaluation) -> AnswerPolicyDecision:
        if not retrieval_evaluation.insufficient_context:
            return AnswerPolicyDecision(
                answer_policy=AnswerPolicy.RAG_GROUNDED,
                use_llm=True,
                use_references=True,
            )

        if query_type == QueryType.SSAFY_OFFICIAL:
            return AnswerPolicyDecision(
                answer_policy=AnswerPolicy.OFFICIAL_NO_CONTEXT,
                use_llm=False,
                use_references=False,
            )

        if query_type == QueryType.GENERAL_TECH:
            return AnswerPolicyDecision(
                answer_policy=AnswerPolicy.GENERAL_KNOWLEDGE_FALLBACK,
                use_llm=True,
                use_references=False,
                fallback_prefix='SSAFY 공식 자료 기준은 아니지만, 일반적인 개발 기준으로 설명하면',
            )

        if query_type == QueryType.GENERAL_ADVICE:
            return AnswerPolicyDecision(
                answer_policy=AnswerPolicy.GENERAL_ADVICE_FALLBACK,
                use_llm=True,
                use_references=False,
                fallback_prefix='SSAFY 공식 안내는 아니지만, 일반적으로는',
            )

        return AnswerPolicyDecision(
            answer_policy=AnswerPolicy.CAUTIOUS_FALLBACK,
            use_llm=True,
            use_references=False,
            fallback_prefix='현재 등록된 SSAFY 자료에서는 직접 확인되지 않습니다. 일반적인 관점에서 조심스럽게 말하면',
        )


def official_no_context_answer() -> str:
    return (
        '현재 등록된 SSAFY 자료에서는 확인되지 않습니다.\n'
        '정확한 내용은 최신 공지사항, LMS, Mattermost 반 공지방, '
        '담당 컨설턴트/코치를 통해 확인하는 것이 안전합니다.'
    )
