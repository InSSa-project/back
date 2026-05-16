from typing import Protocol

from rag.schemas.documents import RagDocument, RagDocumentChunk


class Chunker(Protocol):
    def split(self, document: RagDocument) -> list[RagDocumentChunk]:
        """Split an AI document into retrieval-friendly chunks."""
