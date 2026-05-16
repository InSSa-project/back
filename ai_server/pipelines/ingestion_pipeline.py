from ai_server.rag.chunking.semantic_chunker import SemanticChunker
from ai_server.embeddings.factory import EmbeddingProviderFactory
from ai_server.vectorstores.factory import VectorStoreFactory


class IngestionPipeline:
    def __init__(self):
        self.chunker = SemanticChunker()
        self.embedding_provider = EmbeddingProviderFactory().create()
        self.vectorstore = VectorStoreFactory().create()

    def ingest_ai_document(self, ai_document):
        chunks = self.chunker.split(ai_document)
        vectors = self.embedding_provider.embed_documents([chunk.content for chunk in chunks])
        records = [
            {
                'chunk_id': chunk.chunk_id,
                'ai_document_id': chunk.ai_document_id,
                'raw_data_id': chunk.raw_data_id,
                'title': chunk.title,
                'content': chunk.content,
                'document_type': chunk.document_type,
                'metadata': chunk.metadata,
                'vector': vector,
            }
            for chunk, vector in zip(chunks, vectors)
        ]
        self.vectorstore.upsert(records)
        return chunks
