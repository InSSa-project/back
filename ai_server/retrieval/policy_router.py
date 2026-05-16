from dataclasses import dataclass

from ai_server.retrieval.query_parser import ScheduleQueryType


@dataclass
class RetrievalPolicy:
    name: str
    use_schedule_metadata: bool = False
    allow_semantic_fallback: bool = True
    exact_match_required: bool = False


class RetrievalPolicyRouter:
    def decide(self, parsed_query) -> RetrievalPolicy:
        if parsed_query.query_type == ScheduleQueryType.SCHEDULE_EXACT_DATE:
            return RetrievalPolicy(
                name='SCHEDULE_METADATA_EXACT',
                use_schedule_metadata=True,
                allow_semantic_fallback=False,
                exact_match_required=True,
            )
        if parsed_query.query_type in [ScheduleQueryType.SCHEDULE_MONTH, ScheduleQueryType.SCHEDULE_RANGE]:
            return RetrievalPolicy(
                name='SCHEDULE_METADATA_RANGE',
                use_schedule_metadata=True,
                allow_semantic_fallback=False,
                exact_match_required=False,
            )
        return RetrievalPolicy(name='SEMANTIC_RETRIEVAL')
