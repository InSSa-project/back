class ChromaVectorStore:
    def upsert(self, chunks, vectors) -> None:
        raise NotImplementedError('ChromaDB collection upsert will be connected in production.')

    def search(self, query_vector: list[float], top_k: int, filters: dict):
        return []

    def search_by_metadata(self, start_date: str, end_date: str, exact: bool = False, filters: dict | None = None):
        return []
