from ai_server.prompts.templates import SAFETY_PROMPT, STYLE_PROMPT, SYSTEM_PROMPT


class PromptBuilder:
    def build_messages(
        self,
        question: str,
        intent: str,
        retrieved_context: str,
        user_context: str,
        memory_context: str,
        few_shot_examples: str,
    ) -> list[dict]:
        system_content = '\n\n'.join([
            SYSTEM_PROMPT,
            STYLE_PROMPT,
            SAFETY_PROMPT,
            f'[Intent]\n{intent}',
        ])
        user_content = '\n\n'.join([
            f'[Few-shot Examples]\n{few_shot_examples}',
            f'[Retrieved Context]\n{retrieved_context}',
            f'[User Context]\n{user_context}',
            f'[Conversation Memory]\n{memory_context}',
            f'[Question]\n{question}',
        ])
        return [
            {'role': 'system', 'content': system_content},
            {'role': 'user', 'content': user_content},
        ]
