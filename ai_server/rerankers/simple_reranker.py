from ai_server.core.config import get_settings
from ai_server.rag.schemas.documents import RetrievedChunk


class SimpleReranker:
    def rerank(self, query: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        top_k = get_settings().rerank_top_k
        return sorted(chunks, key=lambda chunk: chunk.score, reverse=True)[:top_k]
