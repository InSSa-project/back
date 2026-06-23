from ai_server.schemas.chat import Reference


class ReferenceTracker:
    def from_chunks(self, chunks) -> list[Reference]:
        return [
            Reference(
                ai_document_id=chunk.ai_document_id,
                raw_data_id=chunk.raw_data_id,
                title=chunk.title,
                source_type=chunk.metadata.get('source_type', chunk.document_type),
                source_url=self._source_url(chunk.metadata),
                detail_url=self._detail_url(chunk.metadata),
                score=chunk.score,
                chunk_id=chunk.chunk_id,
                snippet=chunk.content[:240],
                metadata=chunk.metadata,
            )
            for chunk in chunks
        ]

    def _source_url(self, metadata: dict) -> str:
        return str(
            metadata.get('source_url')
            or metadata.get('original_url')
            or metadata.get('url')
            or ''
        ).strip()

    def _detail_url(self, metadata: dict) -> str:
        return str(
            metadata.get('detail_url')
            or metadata.get('source_url')
            or metadata.get('original_url')
            or metadata.get('url')
            or ''
        ).strip()
