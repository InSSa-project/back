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
        query_vector = self.embedding_provider.embed_query(query)
        return self.vectorstore.search(query_vector=query_vector, top_k=self.settings.top_k, filters=filters or {})
