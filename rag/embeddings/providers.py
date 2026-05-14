class LocalEmbeddingProvider:
    """Placeholder provider for BGE/E5/Instructor or LangChain embeddings."""

    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError('Connect a real embedding model provider.')

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError('Connect a real embedding model provider.')
