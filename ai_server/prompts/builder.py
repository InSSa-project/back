from dataclasses import dataclass

from ai_server.core.config import get_settings
from ai_server.optimization.token_budget import TokenBudgetManager
from ai_server.prompts.context_formatter import ContextFormatter
from ai_server.prompts.few_shot_selector import FewShotSelector
from ai_server.prompts.loader import PromptLoader
from ai_server.prompts.selector import PromptSelector


@dataclass
class PromptBuildResult:
    messages: list[dict]
    metadata: dict


class PromptBuilder:
    def __init__(
        self,
        loader: PromptLoader | None = None,
        selector: PromptSelector | None = None,
        few_shot_selector: FewShotSelector | None = None,
        context_formatter: ContextFormatter | None = None,
        token_budget: TokenBudgetManager | None = None,
    ):
        self.settings = get_settings()
        self.loader = loader or PromptLoader()
        self.selector = selector or PromptSelector()
        self.few_shot_selector = few_shot_selector or FewShotSelector()
        self.context_formatter = context_formatter or ContextFormatter()
        self.token_budget = token_budget or TokenBudgetManager()

    def build_messages(
        self,
        question: str,
        intent: str,
        query_type: str,
        answer_policy: str,
        insufficient_context: bool,
        extracted_date: str,
        retrieval_status: str,
        exact_match: bool,
        chunks: list,
        user_context: str,
        memory_context: str,
        fallback_prefix: str = '',
    ) -> PromptBuildResult:
        selection = self.selector.select(query_type, answer_policy, insufficient_context)
        base_files = selection['base']
        style_files = selection['styles']
        policy_files = selection['policies']
        template_file = selection['template']
        few_shot_file = selection['few_shots']

        base_prompts = [self.loader.load_text(path) for path in base_files]
        style_prompt = '\n\n'.join(self.loader.load_text(path) for path in style_files)
        policy_prompt = '\n\n'.join(self.loader.load_text(path) for path in policy_files)
        template = self.loader.load_text(template_file)
        examples = self.loader.load_json(few_shot_file)
        few_shots = self.few_shot_selector.select(question, examples, self.settings.max_few_shots)

        fixed_text = '\n\n'.join([
            *base_prompts,
            policy_prompt,
            question,
            f'Query Type: {query_type}',
            f'Answer Policy: {answer_policy}',
            f'Extracted Date: {extracted_date}',
            f'Retrieval Status: {retrieval_status}',
            f'Exact Match: {exact_match}',
        ])
        budget = self.token_budget.fit(
            fixed_text=fixed_text,
            context_chunks=chunks,
            few_shots=few_shots,
            memory_context=memory_context,
            style_prompt=style_prompt,
        )
        retrieved_context = self.context_formatter.format(budget.context_chunks)
        few_shot_text = self._format_few_shots(budget.few_shots)

        system_content = '\n\n'.join([
            *base_prompts,
            policy_prompt,
            budget.style_prompt,
            '[Runtime Metadata]',
            f'Intent: {intent}',
            f'Query Type: {query_type}',
            f'Answer Policy: {answer_policy}',
            f'Extracted Date: {extracted_date}',
            f'Retrieval Status: {retrieval_status}',
            f'Exact Match: {exact_match}',
        ])
        user_content = '\n\n'.join([
            template.format(
                question=question,
                retrieved_context=retrieved_context or 'NO_RETRIEVED_CONTEXT',
                fallback_prefix=fallback_prefix,
            ),
            f'[Few-shot Examples]\n{few_shot_text or "NO_FEW_SHOTS"}',
            f'[User Context]\n{user_context}',
            f'[Conversation Memory]\n{budget.memory_context or "NO_MEMORY"}',
        ])
        used_prompt_files = [*base_files, *style_files, *policy_files, template_file]
        return PromptBuildResult(
            messages=[
                {'role': 'system', 'content': system_content},
                {'role': 'user', 'content': user_content},
            ],
            metadata={
                'query_type': query_type,
                'answer_policy': answer_policy,
                'used_prompt_files': used_prompt_files,
                'used_few_shots': [item.get('id', '') for item in budget.few_shots],
                'context_count': len(budget.context_chunks),
                'estimated_tokens': budget.estimated_tokens,
            },
        )

    def _format_few_shots(self, few_shots: list[dict]) -> str:
        blocks = []
        for item in few_shots:
            blocks.append('\n'.join([
                f"Example ID: {item.get('id', '')}",
                f"User: {item.get('user', '')}",
                f"Assistant: {item.get('assistant', '')}",
            ]))
        return '\n\n'.join(blocks)
