from dataclasses import dataclass


@dataclass(frozen=True)
class RagSettings:
    vector_store: str = 'faiss'
    top_k: int = 5
    chunk_size: int = 1000
    chunk_overlap: int = 100
    collection_name: str = 'inssa_ai_documents'
