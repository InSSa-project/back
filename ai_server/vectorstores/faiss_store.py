class FaissVectorStore:
    def upsert(self, chunks, vectors) -> None:
        raise NotImplementedError('FAISS persistence will be connected in MVP implementation.')

    def search(self, query: str, top_k: int, filters: dict):
        return []
