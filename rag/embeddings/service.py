from rag.embeddings.base import EmbeddingProvider
from rag.schemas.documents import RagDocumentChunk


class EmbeddingService:
    def __init__(self, provider: EmbeddingProvider):
        self.provider = provider

    def embed_chunks(self, chunks: list[RagDocumentChunk]) -> list[tuple[RagDocumentChunk, list[float]]]:
        vectors = self.provider.embed_documents([chunk.content for chunk in chunks])
        return list(zip(chunks, vectors))
