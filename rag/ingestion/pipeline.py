from rag.chunking.base import Chunker
from rag.embeddings.service import EmbeddingService
from rag.metadata.service import MetadataService
from rag.schemas.documents import RagDocument
from rag.schemas.retrieval import VectorRecord
from rag.vectorstores.base import VectorStore


class RagIngestionPipeline:
    def __init__(
        self,
        chunker: Chunker,
        embedding_service: EmbeddingService,
        vector_store: VectorStore,
        metadata_service: MetadataService,
    ):
        self.chunker = chunker
        self.embedding_service = embedding_service
        self.vector_store = vector_store
        self.metadata_service = metadata_service

    def ingest(self, document: RagDocument) -> int:
        chunks = self.chunker.split(document)
        embedded_chunks = self.embedding_service.embed_chunks(chunks)
        records = [
            VectorRecord(
                id=chunk.chunk_id,
                document_id=chunk.document_id,
                content=chunk.content,
                vector=vector,
                metadata=self.metadata_service.build_chunk_metadata(document, chunk),
            )
            for chunk, vector in embedded_chunks
        ]
        self.vector_store.upsert(records)
        return len(records)
