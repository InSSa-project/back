from ai_server.prompts.templates import SAFETY_PROMPT, STYLE_PROMPT, SYSTEM_PROMPT


class PromptBuilder:
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
        retrieved_context: str,
        user_context: str,
        memory_context: str,
        few_shot_examples: str,
        fallback_prefix: str = '',
    ) -> list[dict]:
        system_content = '\n\n'.join([
            SYSTEM_PROMPT,
            STYLE_PROMPT,
            SAFETY_PROMPT,
            f'[Intent]\n{intent}',
            f'[Query Type]\n{query_type}',
            f'[Answer Policy]\n{answer_policy}',
            f'[Extracted Date]\n{extracted_date}',
            f'[Retrieval Status]\n{retrieval_status}',
            f'[Exact Match]\n{exact_match}',
        ])
        user_content = '\n\n'.join([
            f'[Few-shot Examples]\n{few_shot_examples}',
            f'[Retrieved Context]\n{retrieved_context or "NO_RETRIEVED_CONTEXT"}',
            f'[Insufficient Context]\n{insufficient_context}',
            f'[Fallback Prefix]\n{fallback_prefix}',
            f'[User Context]\n{user_context}',
            f'[Conversation Memory]\n{memory_context}',
            f'[Question]\n{question}',
            '[Answer Rules]\n'
            '1. For RAG_GROUNDED, use retrieved context as evidence and include no unsupported claims.\n'
            '1-1. For schedule queries, answer only for the extracted date or range.\n'
            '1-2. If Exact Match is false, do not answer with similar schedules from other dates.\n'
            '2. For GENERAL_KNOWLEDGE_FALLBACK, answer from general development knowledge and start by distinguishing it from SSAFY official data.\n'
            '3. For GENERAL_ADVICE_FALLBACK, provide practical general advice and state that it is not SSAFY official guidance.\n'
            '4. For CAUTIOUS_FALLBACK, be conservative and avoid SSAFY-specific claims.\n'
            '5. Do not invent SSAFY dates, scores, penalties, or official requirements.',
        ])
        return [
            {'role': 'system', 'content': system_content},
            {'role': 'user', 'content': user_content},
        ]
