from ai_server.core.config import get_settings
from ai_server.rag.schemas.documents import AiDocument, DocumentChunk


class SemanticChunker:
    def __init__(self):
        settings = get_settings()
        self.chunk_size = settings.chunk_size
        self.chunk_overlap = settings.chunk_overlap

    def split(self, document: AiDocument) -> list[DocumentChunk]:
        paragraphs = [p.strip() for p in document.content.split('\n\n') if p.strip()]
        chunks: list[DocumentChunk] = []
        buffer: list[str] = []
        current_size = 0

        for paragraph in paragraphs or [document.content]:
            if buffer and current_size + len(paragraph) > self.chunk_size:
                chunks.append(self._build_chunk(document, chunks, buffer))
                buffer = buffer[-1:] if self.chunk_overlap else []
                current_size = sum(len(item) for item in buffer)
            buffer.append(paragraph)
            current_size += len(paragraph)

        if buffer:
            chunks.append(self._build_chunk(document, chunks, buffer))
        return chunks

    def _build_chunk(self, document: AiDocument, chunks: list[DocumentChunk], parts: list[str]) -> DocumentChunk:
        return DocumentChunk(
            chunk_id=f'{document.id}:{len(chunks)}',
            ai_document_id=document.id,
            raw_data_id=document.raw_data_id,
            title=document.title,
            content='\n\n'.join(parts),
            document_type=document.document_type,
            metadata={
                **document.metadata_json,
                'ai_document_id': document.id,
                'raw_data_id': document.raw_data_id,
                'document_type': document.document_type,
                'chunk_index': len(chunks),
            },
        )
