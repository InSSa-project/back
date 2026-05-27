import logging

from django.db import transaction

from ai_server.pipelines.ingestion_pipeline import IngestionPipeline
from ai_server.rag.schemas.documents import AiDocument as RagAiDocument

from .models import AiDocument


logger = logging.getLogger(__name__)


class RagIngestionService:
    def __init__(self, pipeline=None):
        self.pipeline = pipeline or IngestionPipeline()

    def ingest_pending(self, limit: int | None = None) -> dict:
        queryset = AiDocument.objects.exclude(embedding_status=AiDocument.EMBEDDING_SUCCESS).order_by('id')
        if limit:
            queryset = queryset[:limit]
        stats = {'total': 0, 'success': 0, 'failed': 0, 'chunks': 0}
        for document in queryset:
            result = self.ingest_document(document)
            stats['total'] += 1
            stats['chunks'] += result.get('chunks', 0)
            stats['success' if result['ok'] else 'failed'] += 1
        return stats

    def ingest_all(self, limit: int | None = None) -> dict:
        queryset = AiDocument.objects.all().order_by('id')
        if limit:
            queryset = queryset[:limit]
        stats = {'total': 0, 'success': 0, 'failed': 0, 'chunks': 0}
        for document in queryset:
            result = self.ingest_document(document)
            stats['total'] += 1
            stats['chunks'] += result.get('chunks', 0)
            stats['success' if result['ok'] else 'failed'] += 1
        return stats

    def ingest_document(self, document: AiDocument) -> dict:
        try:
            with transaction.atomic():
                document.embedding_status = AiDocument.EMBEDDING_PENDING
                document.save(update_fields=['embedding_status', 'updated_at'])
                rag_document = self._to_rag_document(document)
                chunks = self.pipeline.ingest_ai_document(rag_document)
                document.embedding_status = AiDocument.EMBEDDING_SUCCESS
                document.save(update_fields=['embedding_status', 'updated_at'])
            logger.info('RAG ingestion succeeded document_id=%s chunks=%s', document.id, len(chunks))
            return {'ok': True, 'chunks': len(chunks)}
        except Exception as exc:
            document.embedding_status = AiDocument.EMBEDDING_FAILED
            document.save(update_fields=['embedding_status', 'updated_at'])
            logger.exception('RAG ingestion failed document_id=%s error=%s', document.id, exc)
            return {'ok': False, 'chunks': 0, 'error': str(exc)}

    def _to_rag_document(self, document: AiDocument) -> RagAiDocument:
        return RagAiDocument(
            id=document.id,
            raw_data_id=document.canonical_raw_data_id,
            title=document.title,
            content=document.content,
            document_type=document.document_type,
            metadata_json={
                **(document.metadata_json or {}),
                'raw_data_id': document.canonical_raw_data_id,
                'sync_raw_data_id': document.sync_raw_data_id,
                'notices_raw_data_id': document.raw_data_id,
                'created_at': document.created_at.isoformat() if document.created_at else '',
            },
        )
