from django.db import transaction
from django.utils import timezone

from schedules.models import ScheduleEvent

from .models import AiDocument
from .rag_ingestion import RagIngestionService


class ScheduleEventRagIngestionService:
    """Create RAG documents for manual/personal schedule events."""

    def __init__(self, ingestion_service=None):
        self.ingestion_service = ingestion_service or RagIngestionService()

    def ingest_event(self, event: ScheduleEvent, ingest_vectors=True):
        document = self.upsert_document(event)
        if not ingest_vectors:
            return {'ok': True, 'chunks': 0}
        return self.ingestion_service.ingest_document(document)

    def ingest_queryset(self, queryset, ingest_vectors=True):
        stats = {'events': 0, 'documents': 0, 'vectors_success': 0, 'vectors_failed': 0, 'chunks': 0}
        for event in queryset.order_by('id'):
            stats['events'] += 1
            result = self.ingest_event(event, ingest_vectors=ingest_vectors)
            stats['documents'] += 1
            stats['chunks'] += result.get('chunks', 0)
            stats['vectors_success' if result.get('ok') else 'vectors_failed'] += 1
        return stats

    @transaction.atomic
    def upsert_document(self, event: ScheduleEvent):
        document, _created = AiDocument.objects.update_or_create(
            schedule_event=event,
            defaults={
                'raw_data': None,
                'sync_raw_data': None,
                'title': event.title,
                'content': self._build_content(event),
                'document_type': self._document_type(event),
                'metadata_json': self._build_metadata(event),
                'embedding_status': AiDocument.EMBEDDING_PENDING,
            },
        )
        return document

    def _build_content(self, event: ScheduleEvent):
        start_at = timezone.localtime(event.start_at) if event.start_at else None
        end_at = timezone.localtime(event.end_at) if event.end_at else None
        return '\n'.join(
            [
                f'Title: {event.title}',
                f'Event type: {event.event_type}',
                f'Source type: {event.source_type}',
                f'ScheduleEvent id: {event.id}',
                f'Start: {start_at.isoformat() if start_at else ""}',
                f'End: {end_at.isoformat() if end_at else ""}',
                f'Description: {event.description or ""}',
            ]
        )

    def _build_metadata(self, event: ScheduleEvent):
        start_at = timezone.localtime(event.start_at) if event.start_at else None
        end_at = timezone.localtime(event.end_at) if event.end_at else None
        metadata = dict(event.metadata_json or {})
        return {
            **metadata,
            'raw_data_model': '',
            'raw_data_id': None,
            'sync_raw_data_id': None,
            'schedule_event_id': event.id,
            'source_type': event.source_type,
            'title': event.title,
            'event_type': event.event_type,
            'start_date': start_at.date().isoformat() if start_at else '',
            'end_date': end_at.date().isoformat() if end_at else '',
            'start_at': start_at.isoformat() if start_at else '',
            'end_at': end_at.isoformat() if end_at else '',
            'category': event.event_type,
            'user_id': metadata.get('user_id', ''),
        }

    def _document_type(self, event: ScheduleEvent):
        event_type = (event.event_type or 'schedule').upper()
        return f'SCHEDULE_{event_type}'
