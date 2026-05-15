from ai_server.rag.chunking.semantic_chunker import SemanticChunker


class IngestionPipeline:
    def __init__(self):
        self.chunker = SemanticChunker()

    def ingest_ai_document(self, ai_document):
        chunks = self.chunker.split(ai_document)
        return chunks
