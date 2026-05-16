from rag.prompts.builder import RagPromptBuilder
from rag.prompts.templates import DEFAULT_SYSTEM_PROMPT
from rag.retrievers.base import Retriever
from rag.schemas.prompts import PromptContext
from rag.schemas.retrieval import RetrievalQuery


class RagQaPipeline:
    def __init__(self, retriever: Retriever, prompt_builder: RagPromptBuilder):
        self.retriever = retriever
        self.prompt_builder = prompt_builder

    def build_prompt(self, question: str, user_context: str = '', filters: dict | None = None):
        retrieval = self.retriever.retrieve(RetrievalQuery(text=question, filters=filters or {}))
        return self.prompt_builder.build(
            PromptContext(
                system_prompt=DEFAULT_SYSTEM_PROMPT,
                question=question,
                user_context=user_context,
                retrieved_chunks=retrieval.chunks,
            )
        )
