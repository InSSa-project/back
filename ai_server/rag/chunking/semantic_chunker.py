import re

from ai_server.core.config import get_settings
from ai_server.rag.schemas.documents import AiDocument, DocumentChunk


class SemanticChunker:
    def __init__(self):
        settings = get_settings()
        self.chunk_size = settings.chunk_size
        self.chunk_overlap = settings.chunk_overlap

    def split(self, document: AiDocument) -> list[DocumentChunk]:
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', document.content) if p.strip()]
        chunks: list[DocumentChunk] = []
        buffer: list[str] = []
        current_size = 0

        for paragraph in paragraphs or [document.content]:
            if buffer and current_size + len(paragraph) > self.chunk_size:
                chunks.append(self._build_chunk(document, chunks, buffer))
                overlap_text = self._overlap_text('\n\n'.join(buffer))
                buffer = [overlap_text] if overlap_text else []
                current_size = len(overlap_text)
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
                'document_id': document.id,
                'ai_document_id': document.id,
                'raw_data_id': document.raw_data_id,
                'document_type': document.document_type,
                'source_type': document.document_type,
                'event_type': document.metadata_json.get('event_type', ''),
                'start_date': document.metadata_json.get('start_date', ''),
                'end_date': document.metadata_json.get('end_date', document.metadata_json.get('start_date', '')),
                'campus': document.metadata_json.get('campus', ''),
                'generation': document.metadata_json.get('generation', ''),
                'track': document.metadata_json.get('track', ''),
                'title': document.title,
                'chunk_index': len(chunks),
            },
        )

    def _overlap_text(self, text: str) -> str:
        if self.chunk_overlap <= 0:
            return ''
        return text[-self.chunk_overlap:]
