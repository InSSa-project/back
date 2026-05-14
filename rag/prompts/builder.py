from rag.schemas.prompts import PromptContext, PromptPayload


class RagPromptBuilder:
    def build(self, context: PromptContext) -> PromptPayload:
        retrieved_context = '\n\n'.join(
            f'[source:{chunk.document_id} score:{chunk.score}]\n{chunk.content}'
            for chunk in context.retrieved_chunks
        )
        prompt = (
            f'{context.system_prompt}\n\n'
            f'[Retrieved Documents]\n{retrieved_context}\n\n'
            f'[User Context]\n{context.user_context}\n\n'
            f'[User Question]\n{context.question}'
        )
        return PromptPayload(prompt=prompt, references=context.retrieved_chunks)
