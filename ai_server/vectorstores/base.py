from typing import Protocol

from ai_server.rag.schemas.documents import DocumentChunk, RetrievedChunk


class VectorStore(Protocol):
    def upsert(self, chunks: list[DocumentChunk], vectors: list[list[float]]) -> None:
        ...

    def search(self, query: str, top_k: int, filters: dict) -> list[RetrievedChunk]:
        ...
