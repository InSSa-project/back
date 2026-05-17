from rag.schemas.documents import RagDocument


class AiDocumentLoader:
    """Adapter from Django ai_documents rows to RAG documents."""

    def from_model(self, ai_document) -> RagDocument:
        return RagDocument(
            id=ai_document.id,
            raw_data_id=ai_document.raw_data_id,
            title=ai_document.title,
            content=ai_document.content,
            document_type=ai_document.document_type,
            metadata=ai_document.metadata_json or {},
        )
