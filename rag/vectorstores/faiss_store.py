from rag.schemas.retrieval import RetrievedChunk, VectorRecord


class FaissVectorStore:
    """FAISS adapter boundary. Implementation can be added without changing services."""

    def upsert(self, records: list[VectorRecord]) -> None:
        raise NotImplementedError('Connect FAISS index persistence.')

    def search(self, query_vector: list[float], top_k: int, filters: dict | None = None) -> list[RetrievedChunk]:
        raise NotImplementedError('Connect FAISS search.')

    def delete_by_document_id(self, document_id: int) -> None:
        raise NotImplementedError('Connect FAISS delete/rebuild strategy.')
