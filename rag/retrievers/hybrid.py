from rag.embeddings.base import EmbeddingProvider
from rag.schemas.retrieval import RetrievalQuery, RetrievalResult
from rag.vectorstores.base import VectorStore


class HybridRetriever:
    """Vector-first retriever with a keyword-search extension point."""

    def __init__(self, embedding_provider: EmbeddingProvider, vector_store: VectorStore):
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        query_vector = self.embedding_provider.embed_query(query.text)
        chunks = self.vector_store.search(
            query_vector=query_vector,
            top_k=query.top_k,
            filters=query.filters,
        )
        return RetrievalResult(query=query, chunks=chunks)
