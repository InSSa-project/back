from ai_server.classification.query_classifier import QueryType
from ai_server.retrieval.query_parser import ScheduleQueryType


class PromptSelector:
    def select(self, query_type: str, answer_policy: str, insufficient_context: bool) -> dict:
        files = {
            'base': ['base/system.md', 'base/safety.md', 'base/response_format.md'],
            'styles': ['styles/inssa_voice.md'],
            'policies': [],
            'template': 'templates/general_chat_template.md',
            'few_shots': 'few_shots/mentoring_examples.json',
        }

        if insufficient_context and query_type == QueryType.SSAFY_OFFICIAL:
            files['styles'] = ['styles/inssa_voice.md']
            files['policies'] = ['policies/fallback_policy.md']
            files['template'] = 'templates/no_context_template.md'
            files['few_shots'] = 'few_shots/exam_examples.json'
            return files

        if query_type in [QueryType.SSAFY_OFFICIAL, ScheduleQueryType.SCHEDULE_EXACT_DATE, ScheduleQueryType.SCHEDULE_MONTH, ScheduleQueryType.SCHEDULE_RANGE]:
            files['styles'] = ['styles/inssa_voice.md']
            files['policies'] = ['policies/rag_answer_policy.md', 'policies/schedule_policy.md']
            files['template'] = 'templates/rag_chat_template.md'
            files['few_shots'] = 'few_shots/schedule_examples.json'
            return files

        if query_type == QueryType.GENERAL_TECH:
            files['styles'] = ['styles/inssa_voice.md']
            files['policies'] = ['policies/general_tech_policy.md']
            files['template'] = 'templates/general_chat_template.md'
            files['few_shots'] = 'few_shots/tech_examples.json'
            return files

        if query_type == QueryType.GENERAL_ADVICE:
            files['styles'] = ['styles/inssa_voice.md']
            files['policies'] = ['policies/fallback_policy.md']
            files['template'] = 'templates/general_chat_template.md'
            files['few_shots'] = 'few_shots/mentoring_examples.json'
            return files

        files['styles'] = ['styles/inssa_voice.md']
        files['policies'] = ['policies/fallback_policy.md']
        files['template'] = 'templates/no_context_template.md' if insufficient_context else 'templates/general_chat_template.md'
        return files

