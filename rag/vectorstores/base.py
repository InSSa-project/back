from typing import Protocol

from rag.schemas.retrieval import RetrievedChunk, VectorRecord


class VectorStore(Protocol):
    def upsert(self, records: list[VectorRecord]) -> None:
        """Insert or update embedded chunks."""

    def search(self, query_vector: list[float], top_k: int, filters: dict | None = None) -> list[RetrievedChunk]:
        """Return top-k vector search results."""

    def delete_by_document_id(self, document_id: int) -> None:
        """Delete indexed chunks only. PostgreSQL raw data is never deleted."""
