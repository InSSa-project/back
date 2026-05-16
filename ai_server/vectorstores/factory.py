from ai_server.core.config import get_settings
from ai_server.vectorstores.chroma_store import ChromaVectorStore
from ai_server.vectorstores.faiss_store import FaissVectorStore


class VectorStoreFactory:
    def create(self):
        provider = get_settings().vectorstore_provider
        if provider == 'chroma':
            return ChromaVectorStore()
        return FaissVectorStore()
