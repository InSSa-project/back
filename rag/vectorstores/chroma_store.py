from rag.schemas.retrieval import RetrievedChunk, VectorRecord


class ChromaVectorStore:
    """ChromaDB adapter boundary. Matches VectorStore protocol."""

    def upsert(self, records: list[VectorRecord]) -> None:
        raise NotImplementedError('Connect ChromaDB collection upsert.')

    def search(self, query_vector: list[float], top_k: int, filters: dict | None = None) -> list[RetrievedChunk]:
        raise NotImplementedError('Connect ChromaDB query.')

    def delete_by_document_id(self, document_id: int) -> None:
        raise NotImplementedError('Connect ChromaDB delete.')
