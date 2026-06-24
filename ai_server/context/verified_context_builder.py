import json
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass
class VerifiedContext:
    question: str
    intent: str
    query_type: str
    answer_policy: str
    retrieval_status: str
    insufficient_context: bool
    user_context: str
    memory_context: str
    facts: list[dict] = field(default_factory=list)
    references: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_prompt_text(self) -> str:
        facts_text = self._format_facts()
        references_text = self._format_references()
        return "\n".join(
            [
                "[Verified Context]",
                f"Intent: {self.intent}",
                f"Query Type: {self.query_type}",
                f"Answer Policy: {self.answer_policy}",
                f"Retrieval Status: {self.retrieval_status}",
                f"Insufficient Context: {self.insufficient_context}",
                f"User Context: {self.user_context or 'NO_USER_CONTEXT'}",
                f"Memory Context: {self.memory_context or 'NO_MEMORY'}",
                "[Verified Facts]",
                facts_text or "NO_VERIFIED_FACTS",
                "[References]",
                references_text or "NO_REFERENCES",
            ]
        )

    def _format_facts(self) -> str:
        lines = []
        for index, fact in enumerate(self.facts[:5], start=1):
            title = fact.get("title") or "Untitled"
            source_type = fact.get("source_type") or ""
            score = fact.get("score")
            snippet = fact.get("snippet") or ""
            score_text = f", score={score}" if score is not None else ""
            lines.append(f"{index}. {title} ({source_type}{score_text}) - {snippet}")
        return "\n".join(lines)

    def _format_references(self) -> str:
        lines = []
        for index, ref in enumerate(self.references[:5], start=1):
            title = ref.get("title") or "Untitled"
            source_url = ref.get("source_url") or ref.get("detail_url") or ""
            lines.append(f"{index}. {title} {source_url}".strip())
        return "\n".join(lines)


class VerifiedContextBuilder:
    """Build a single verified context object from DB/RAG/memory/runtime state."""

    def build(
        self,
        question: str,
        intent: str,
        query_type: str,
        answer_policy: str,
        user_context: str,
        memory_context: str,
        chunks: list | None = None,
        references: list | None = None,
        retrieval_evaluation=None,
        parsed_query=None,
    ) -> VerifiedContext:
        facts = [self._chunk_to_fact(chunk) for chunk in list(chunks or [])[:5]]
        refs = [self._reference_to_dict(reference) for reference in list(references or [])[:5]]
        retrieval_status = getattr(retrieval_evaluation, "reason", "") or ""
        insufficient_context = bool(getattr(retrieval_evaluation, "insufficient_context", False))
        current_date = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
        metadata = {
            "current_date": current_date,
            "fact_count": len(facts),
            "reference_count": len(refs),
            "retrieval_status": retrieval_status,
            "insufficient_context": insufficient_context,
            "parsed_start_date": str(getattr(parsed_query, "start_date", "") or ""),
            "parsed_end_date": str(getattr(parsed_query, "end_date", "") or ""),
        }
        return VerifiedContext(
            question=question,
            intent=intent,
            query_type=query_type,
            answer_policy=answer_policy,
            retrieval_status=retrieval_status,
            insufficient_context=insufficient_context,
            user_context=self._safe_text(user_context, 1000),
            memory_context=self._safe_text(memory_context, 1200),
            facts=facts,
            references=refs,
            metadata=metadata,
        )

    def _chunk_to_fact(self, chunk) -> dict:
        metadata = dict(getattr(chunk, "metadata", {}) or {})
        return {
            "chunk_id": self._safe_text(getattr(chunk, "chunk_id", "") or metadata.get("chunk_id", ""), 120),
            "title": self._safe_text(getattr(chunk, "title", "") or metadata.get("title", ""), 160),
            "source_type": self._safe_text(metadata.get("source_type", "") or getattr(chunk, "document_type", ""), 80),
            "source_url": self._safe_text(metadata.get("source_url", "") or metadata.get("detail_url", ""), 240),
            "score": self._safe_score(getattr(chunk, "score", None)),
            "snippet": self._safe_text(getattr(chunk, "content", "") or "", 420),
        }

    def _reference_to_dict(self, reference) -> dict:
        if hasattr(reference, "model_dump"):
            payload = reference.model_dump()
        elif isinstance(reference, dict):
            payload = dict(reference)
        else:
            payload = {
                "title": getattr(reference, "title", ""),
                "source_type": getattr(reference, "source_type", ""),
                "source_url": getattr(reference, "source_url", ""),
                "detail_url": getattr(reference, "detail_url", ""),
            }
        return {
            "title": self._safe_text(payload.get("title", ""), 160),
            "source_type": self._safe_text(payload.get("source_type", ""), 80),
            "source_url": self._safe_text(payload.get("source_url", ""), 240),
            "detail_url": self._safe_text(payload.get("detail_url", ""), 240),
        }

    def _safe_text(self, value, limit: int) -> str:
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."

    def _safe_score(self, value):
        if value is None:
            return None
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            return None
