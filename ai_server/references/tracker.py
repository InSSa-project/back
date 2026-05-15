from ai_server.schemas.chat import Reference


class ReferenceTracker:
    def from_chunks(self, chunks) -> list[Reference]:
        return [
            Reference(
                ai_document_id=chunk.ai_document_id,
                raw_data_id=chunk.raw_data_id,
                title=chunk.title,
                score=chunk.score,
                chunk_id=chunk.chunk_id,
                metadata=chunk.metadata,
            )
            for chunk in chunks
        ]
