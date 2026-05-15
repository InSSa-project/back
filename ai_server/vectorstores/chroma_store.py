class ChromaVectorStore:
    def upsert(self, chunks, vectors) -> None:
        raise NotImplementedError('ChromaDB collection upsert will be connected in production.')

    def search(self, query: str, top_k: int, filters: dict):
        return []
