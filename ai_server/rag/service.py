import logging
from dataclasses import dataclass
from datetime import datetime

from ai_server.rag.schemas.documents import RetrievedChunk
from ai_server.references.tracker import ReferenceTracker
from ai_server.rerankers.simple_reranker import SimpleReranker
from ai_server.retrieval.schedule_retrieval import ScheduleRetrievalService
from ai_server.retrievers.evaluator import RetrievalEvaluator
from ai_server.retrievers.hybrid_retriever import HybridRetriever

logger = logging.getLogger(__name__)


@dataclass
class RAGSearchResult:
    chunks: list
    evaluation: object
    references: list


class RAGService:
    """Own public RAG retrieval, reranking, evaluation, and reference formatting."""

    def __init__(
        self,
        retriever=None,
        schedule_retrieval=None,
        reranker=None,
        evaluator=None,
        reference_tracker=None,
    ):
        self.retriever = retriever or HybridRetriever()
        self.schedule_retrieval = schedule_retrieval or ScheduleRetrievalService()
        self.reranker = reranker or SimpleReranker()
        self.evaluator = evaluator or RetrievalEvaluator()
        self.reference_tracker = reference_tracker or ReferenceTracker()

    def search_public(self, question: str, filters: dict | None = None) -> RAGSearchResult:
        try:
            retrieved = self.retriever.retrieve(question, filters=filters or {})
            chunks = self.reranker.rerank(question, retrieved)
            evaluation = self.evaluator.evaluate(chunks, query=question)
            references = self.reference_tracker.from_chunks(chunks)
            return RAGSearchResult(chunks=chunks, evaluation=evaluation, references=references)
        except Exception as exc:
            logger.exception('Public RAG search failed: %s', exc)
            evaluation = self.evaluator.evaluate([], query=question)
            return RAGSearchResult(
                chunks=[],
                evaluation=evaluation,
                references=[],
            )

    def search_notices(self, question: str, parsed_query=None, limit: int = 4) -> RAGSearchResult:
        try:
            from apps.ai.models import AiDocument

            queryset = AiDocument.objects.filter(document_type='SYNC_NOTICE').order_by('-sync_raw_data_id', '-id')
            start_date = getattr(parsed_query, 'start_date', '') or ''
            end_date = getattr(parsed_query, 'end_date', '') or ''
            rows = []
            for document in queryset[:500]:
                published_date = self._published_date(document.metadata_json or {})
                if start_date and end_date:
                    if not published_date or not (start_date <= published_date <= end_date):
                        continue
                if self._is_routine_notice(question, document):
                    continue
                rows.append((self._notice_score(question, document), document, published_date))

            if not rows:
                evaluation = self.evaluator.evaluate([], query=question)
                return RAGSearchResult(chunks=[], evaluation=evaluation, references=[])

            if start_date and end_date:
                rows.sort(key=lambda item: (item[2] or '', item[1].id), reverse=True)
            else:
                rows.sort(key=lambda item: (item[0], item[2] or '', item[1].id), reverse=True)
            chunks = [self._notice_chunk(document, score=score) for score, document, _date in rows[:limit]]
            evaluation = self.evaluator.evaluate(chunks, query=question)
            references = self.reference_tracker.from_chunks(chunks)
            return RAGSearchResult(chunks=chunks, evaluation=evaluation, references=references)
        except Exception as exc:
            logger.exception('Notice RAG search failed; falling back to public RAG: %s', exc)
            return self.search_public(question)

    def search_schedules(self, parsed_query, filters: dict | None = None, question: str = '') -> list:
        # Schedule DB access enforces public + current user's private data policy.
        return self.schedule_retrieval.retrieve(parsed_query, filters=filters or {}, query=question)


    def _notice_chunk(self, document, score: float) -> RetrievedChunk:
        metadata = dict(document.metadata_json or {})
        content = document.content or ''
        return RetrievedChunk(
            chunk_id=f'notice-db:{document.id}',
            ai_document_id=document.id,
            raw_data_id=document.canonical_raw_data_id,
            title=document.title,
            content=content[:1600],
            document_type=document.document_type,
            metadata={
                **metadata,
                'document_id': document.id,
                'ai_document_id': document.id,
                'raw_data_id': document.canonical_raw_data_id,
                'document_type': document.document_type,
                'source_type': metadata.get('source_type', 'notice'),
                'retrieval_source': 'notice_db',
            },
            score=round(float(score), 4),
        )

    def _notice_score(self, question: str, document) -> float:
        text = f'{document.title}\n{document.content}'.lower()
        tokens = self._query_tokens(question)
        if not tokens:
            return 0.2
        matched = sum(1 for token in tokens if token in text)
        return 0.12 + (matched / max(len(tokens), 1)) * 0.88

    def _query_tokens(self, question: str) -> list[str]:
        normalized = (question or '').lower()
        stopwords = {
            '\uc774\ubc88\uc8fc', '\uc774\ubc88', '\uc624\ub298', '\ub0b4\uc77c', '\uacf5\uc9c0', '\uacf5\uc9c0\uc0ac\ud56d',
            '\uc694\uc57d', '\uc815\ub9ac', '\uc54c\ub824\uc918', '\ubcf4\uc5ec\uc918', '\ud655\uc778', '\ucd5c\uadfc',
            '\uc911\uc694', '\ubb50\uc57c', '\ubb34\uc5c7',
        }
        tokens = []
        for raw in normalized.replace('?', ' ').replace('.', ' ').split():
            token = raw.strip()
            if len(token) < 2 or token in stopwords:
                continue
            tokens.append(token)
        return list(dict.fromkeys(tokens))

    def _published_date(self, metadata: dict) -> str:
        value = str(
            metadata.get('published_at')
            or metadata.get('posted_at')
            or metadata.get('source_published_at')
            or metadata.get('date')
            or ''
        ).strip()
        for fmt in ('%Y.%m.%d %H:%M', '%Y.%m.%d', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                return datetime.strptime(value, fmt).date().isoformat()
            except ValueError:
                continue
        return ''


    def _is_routine_notice(self, question: str, document) -> bool:
        question_text = (question or '').lower()
        allow_terms = ('\uc2dc\uac04\ud45c', '\ud559\uc2b5', '\uc628\ub77c\uc778', '\uc704\ud06c')
        if any(term in question_text for term in allow_terms):
            return False
        title = (document.title or '').lower()
        routine_terms = ('\uc2dc\uac04\ud45c', '\uc628\ub77c\uc778 \uc704\ud06c', '\uc628\ub77c\uc778\uc704\ud06c')
        return any(term in title for term in routine_terms)
