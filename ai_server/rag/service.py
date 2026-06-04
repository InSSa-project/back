import logging
from dataclasses import dataclass

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
            evaluation = self.evaluator.evaluate(chunks)
            references = self.reference_tracker.from_chunks(chunks)
            return RAGSearchResult(chunks=chunks, evaluation=evaluation, references=references)
        except Exception as exc:
            evaluation = self.evaluator.evaluate([])
            return RAGSearchResult(
                chunks=[],
                evaluation=evaluation,
                references=[],
            )

    def search_schedules(self, parsed_query, filters: dict | None = None, question: str = '') -> list:
        # Schedule DB access enforces public + current user's private data policy.
        return self.schedule_retrieval.retrieve(parsed_query, filters=filters or {}, query=question)

