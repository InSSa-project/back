from rag.schemas.documents import RagDocument, RagDocumentChunk


class MetadataService:
    def build_document_filters(self, user=None, document_type: str | None = None) -> dict:
        filters = {}
        if user and getattr(user, 'campus', None):
            filters['campus'] = user.campus
        if user and getattr(user, 'generation', None):
            filters['generation'] = user.generation
        if document_type:
            filters['document_type'] = document_type
        return filters

    def build_chunk_metadata(self, document: RagDocument, chunk: RagDocumentChunk) -> dict:
        return {
            **document.metadata,
            'ai_document_id': document.id,
            'raw_data_id': document.raw_data_id,
            'document_type': document.document_type,
            'chunk_id': chunk.chunk_id,
        }
