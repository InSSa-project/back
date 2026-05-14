from rag.settings.config import RagSettings
from rag.vectorstores.chroma_store import ChromaVectorStore
from rag.vectorstores.faiss_store import FaissVectorStore


def build_vector_store(settings: RagSettings):
    if settings.vector_store == 'chroma':
        return ChromaVectorStore()
    return FaissVectorStore()
