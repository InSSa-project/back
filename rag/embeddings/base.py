from typing import Protocol


class EmbeddingProvider(Protocol):
    def embed_query(self, text: str) -> list[float]:
        """Create one embedding for a query."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Create embeddings for document chunks."""
