from ai_server.core.config import get_settings
from ai_server.rag.schemas.documents import RetrievedChunk


class SimpleReranker:
    def rerank(self, query: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        top_k = get_settings().rerank_top_k
        boosted = [self._with_keyword_boost(query, chunk) for chunk in chunks]
        return sorted(boosted, key=lambda chunk: chunk.score, reverse=True)[:top_k]

    def _with_keyword_boost(self, query: str, chunk: RetrievedChunk) -> RetrievedChunk:
        terms = self._query_terms(query)
        if not terms:
            return chunk
        haystack = f'{chunk.title}\n{chunk.content}'.lower()
        matches = sum(1 for term in terms if term in haystack)
        if not matches:
            return chunk
        boosted_score = min(1.0, float(chunk.score) + (0.08 * matches))
        return RetrievedChunk(
            chunk_id=chunk.chunk_id,
            ai_document_id=chunk.ai_document_id,
            raw_data_id=chunk.raw_data_id,
            title=chunk.title,
            content=chunk.content,
            document_type=chunk.document_type,
            metadata=chunk.metadata,
            score=round(boosted_score, 4),
        )

    def _query_terms(self, query: str) -> list[str]:
        text = (query or '').lower()
        terms = []
        for term in (
            '과락', '퇴소', '중도퇴소', '수료', '재시험', '월말평가', '과목평가',
            '출결', '결석', '지각', '통과', '불합격', '평가',
        ):
            if term in text:
                terms.append(term)
        return terms
