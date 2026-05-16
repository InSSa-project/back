from rag.schemas.documents import RagDocument, RagDocumentChunk


class SemanticChunker:
    """Paragraph-first chunker. Replace internals with LangChain/LlamaIndex if needed."""

    def __init__(self, chunk_size: int = 1000, overlap: int = 100):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split(self, document: RagDocument) -> list[RagDocumentChunk]:
        paragraphs = [part.strip() for part in document.content.split('\n\n') if part.strip()]
        chunks: list[RagDocumentChunk] = []
        buffer: list[str] = []
        current_size = 0

        for paragraph in paragraphs or [document.content]:
            paragraph_size = len(paragraph)
            if buffer and current_size + paragraph_size > self.chunk_size:
                chunks.append(self._build_chunk(document, chunks, buffer))
                buffer = buffer[-1:] if self.overlap else []
                current_size = sum(len(item) for item in buffer)
            buffer.append(paragraph)
            current_size += paragraph_size

        if buffer:
            chunks.append(self._build_chunk(document, chunks, buffer))
        return chunks

    def _build_chunk(self, document: RagDocument, chunks: list[RagDocumentChunk], parts: list[str]) -> RagDocumentChunk:
        return RagDocumentChunk(
            document_id=document.id,
            chunk_id=f'{document.id}:{len(chunks)}',
            content='\n\n'.join(parts),
            metadata=document.metadata,
        )
