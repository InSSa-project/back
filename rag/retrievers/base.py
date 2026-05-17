from typing import Protocol

from rag.schemas.retrieval import RetrievalQuery, RetrievalResult


class Retriever(Protocol):
    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Retrieve top-k chunks for a user question."""
