class ContextFormatter:
    def format(self, chunks: list) -> str:
        blocks = []
        for index, chunk in enumerate(chunks, start=1):
            metadata = getattr(chunk, 'metadata', {}) or {}
            date_text = metadata.get('start_date') or ''
            if metadata.get('end_date') and metadata.get('end_date') != date_text:
                date_text = f"{date_text} ~ {metadata.get('end_date')}"
            snippet = getattr(chunk, 'content', '')[:700]
            blocks.append(
                '\n'.join([
                    f'[Context {index}]',
                    f"Title: {getattr(chunk, 'title', '')}",
                    f"Source: {getattr(chunk, 'document_type', '') or metadata.get('source_type', '')}",
                    f"Date: {date_text}",
                    f"Score: {getattr(chunk, 'score', 0)}",
                    f"Snippet: {snippet}",
                ])
            )
        return '\n\n'.join(blocks)
