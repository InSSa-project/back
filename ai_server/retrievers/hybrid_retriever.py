from ai_server.core.config import get_settings
from ai_server.embeddings.factory import EmbeddingProviderFactory
from ai_server.rag.schemas.documents import RetrievedChunk
from ai_server.vectorstores.factory import VectorStoreFactory


class HybridRetriever:
    def __init__(self):
        self.settings = get_settings()
        self.embedding_provider = EmbeddingProviderFactory().create()
        self.vectorstore = VectorStoreFactory().create()

    def retrieve(self, query: str, filters: dict | None = None) -> list[RetrievedChunk]:
        filters = filters or {}
        query_vector = self.embedding_provider.embed_query(query)
        chunks = self.vectorstore.search(query_vector=query_vector, top_k=self.settings.top_k, filters=filters)
        if filters.get('source_type') == 'academic_rule' and hasattr(self.vectorstore, 'search_by_terms'):
            chunks = self._merge_chunks(
                chunks,
                self.vectorstore.search_by_terms(query, top_k=self.settings.top_k, filters=filters),
            )
        return chunks

    def _merge_chunks(self, primary: list[RetrievedChunk], supplemental: list[RetrievedChunk]) -> list[RetrievedChunk]:
        by_id = {chunk.chunk_id: chunk for chunk in primary}
        for chunk in supplemental:
            existing = by_id.get(chunk.chunk_id)
            if existing is None or chunk.score > existing.score:
                by_id[chunk.chunk_id] = chunk
        return sorted(by_id.values(), key=lambda chunk: chunk.score, reverse=True)
