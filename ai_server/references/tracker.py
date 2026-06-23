from ai_server.schemas.chat import Reference


class ReferenceTracker:
    def from_chunks(self, chunks) -> list[Reference]:
        references = []
        for chunk in chunks:
            metadata = chunk.metadata or {}
            source_url = self._source_url(metadata)
            references.append(
                Reference(
                    ai_document_id=chunk.ai_document_id,
                    raw_data_id=chunk.raw_data_id,
                    title=chunk.title,
                    source_type=chunk.document_type,
                    source_url=source_url,
                    external_url=source_url,
                    score=chunk.score,
                    chunk_id=chunk.chunk_id,
                    snippet=chunk.content[:240],
                    metadata=metadata,
                )
            )
        return references

    def _source_url(self, metadata: dict) -> str:
        for key in ('source_url', 'external_url', 'detail_url', 'url'):
            value = metadata.get(key)
            if value:
                return str(value)

        raw_json = metadata.get('raw_json')
        if isinstance(raw_json, dict):
            for key in ('source_url', 'external_url', 'detail_url', 'url'):
                value = raw_json.get(key)
                if value:
                    return str(value)
        return ''
