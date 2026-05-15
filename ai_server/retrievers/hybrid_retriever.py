from ai_server.core.config import get_settings
from ai_server.rag.schemas.documents import RetrievedChunk
from ai_server.vectorstores.factory import VectorStoreFactory


class HybridRetriever:
    def __init__(self):
        self.settings = get_settings()
        self.vectorstore = VectorStoreFactory().create()

    def retrieve(self, query: str, filters: dict | None = None) -> list[RetrievedChunk]:
        return self.vectorstore.search(query=query, top_k=self.settings.top_k, filters=filters or {})
