import json
import math
from pathlib import Path

from ai_server.core.config import get_settings
from ai_server.rag.schemas.documents import RetrievedChunk


class FaissVectorStore:
    """Small FAISS-style persistent vector store for the MVP.

    It stores vectors in JSON so the project can run without native FAISS wheels.
    The class boundary matches a future FAISS adapter: upsert records, search by vector.
    """

    def __init__(self, path: str | None = None):
        settings = get_settings()
        self.path = Path(path or settings.vectorstore_path)
        if not self.path.is_absolute():
            self.path = Path.cwd() / self.path

    def upsert(self, records: list[dict]) -> None:
        payload = self._load()
        by_id = {record['chunk_id']: record for record in payload.get('records', [])}
        for record in records:
            by_id[record['chunk_id']] = record
        self._save({'records': list(by_id.values())})

    def search(self, query_vector: list[float], top_k: int, filters: dict | None = None):
        filters = filters or {}
        records = self._load().get('records', [])
        scored = []
        for record in records:
            if not self._matches_filters(record.get('metadata', {}), filters):
                continue
            score = self._cosine(query_vector, record.get('vector', []))
            scored.append((score, record))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            RetrievedChunk(
                chunk_id=record['chunk_id'],
                ai_document_id=record['ai_document_id'],
                raw_data_id=record.get('raw_data_id'),
                title=record.get('title', ''),
                content=record.get('content', ''),
                document_type=record.get('document_type', ''),
                metadata=record.get('metadata', {}),
                score=round(float(score), 4),
            )
            for score, record in scored[:top_k]
            if score > 0
        ]

    def search_by_metadata(
        self,
        start_date: str,
        end_date: str,
        exact: bool = False,
        filters: dict | None = None,
    ):
        filters = filters or {}
        records = self._load().get('records', [])
        matched = []
        for record in records:
            metadata = record.get('metadata', {})
            if not self._matches_filters(metadata, filters):
                continue
            if self._matches_date(metadata, start_date, end_date, exact):
                matched.append(record)
        return [
            RetrievedChunk(
                chunk_id=record['chunk_id'],
                ai_document_id=record['ai_document_id'],
                raw_data_id=record.get('raw_data_id'),
                title=record.get('title', ''),
                content=record.get('content', ''),
                document_type=record.get('document_type', ''),
                metadata=record.get('metadata', {}),
                score=1.0,
            )
            for record in matched
        ]

    def _load(self) -> dict:
        if not self.path.exists():
            return {'records': []}
        return json.loads(self.path.read_text(encoding='utf-8'))

    def _save(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')

    def _matches_filters(self, metadata: dict, filters: dict) -> bool:
        for key, value in filters.items():
            if value in (None, ''):
                continue
            if key == 'intent':
                continue
            if str(metadata.get(key, '')) != str(value):
                return False
        return True

    def _matches_date(self, metadata: dict, start_date: str, end_date: str, exact: bool) -> bool:
        record_start = metadata.get('start_date') or metadata.get('date') or ''
        record_end = metadata.get('end_date') or record_start
        if not record_start:
            return False
        if exact:
            return record_start <= start_date <= (record_end or record_start)
        return record_start <= end_date and (record_end or record_start) >= start_date

    def _cosine(self, left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left)) or 1.0
        right_norm = math.sqrt(sum(b * b for b in right)) or 1.0
        return dot / (left_norm * right_norm)
