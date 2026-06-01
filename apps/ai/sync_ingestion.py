from django.db import transaction

from sync.models import RawSsafyData

from .models import AiDocument
from .rag_ingestion import RagIngestionService


class SyncRawDataRagIngestionService:
    """Create RAG documents from the canonical crawling raw-data model."""

    DOCUMENT_TYPE_PREFIX = 'SYNC_'

    def __init__(self, ingestion_service=None):
        self.ingestion_service = ingestion_service or RagIngestionService()

    def ingest_raw_data_ids(self, raw_data_ids, ingest_vectors=True):
        queryset = RawSsafyData.objects.filter(id__in=list(raw_data_ids or [])).order_by('id')
        return self.ingest_queryset(queryset, ingest_vectors=ingest_vectors)

    def ingest_queryset(self, queryset, ingest_vectors=True):
        stats = {'raw_total': 0, 'documents': 0, 'vectors_success': 0, 'vectors_failed': 0, 'chunks': 0}
        for raw_data in queryset:
            stats['raw_total'] += 1
            document = self.upsert_document(raw_data)
            stats['documents'] += 1
            if not ingest_vectors:
                continue
            result = self.ingestion_service.ingest_document(document)
            stats['chunks'] += result.get('chunks', 0)
            stats['vectors_success' if result.get('ok') else 'vectors_failed'] += 1
        return stats

    @transaction.atomic
    def upsert_document(self, raw_data):
        schedule_events = list(raw_data.schedule_events.order_by('start_at', 'id'))
        content = self._build_content(raw_data, schedule_events)
        metadata = self._build_metadata(raw_data, schedule_events)
        document_type = self._document_type(raw_data)

        document, _created = AiDocument.objects.update_or_create(
            sync_raw_data=raw_data,
            defaults={
                'raw_data': None,
                'title': raw_data.title or 'Untitled SSAFY document',
                'content': content,
                'document_type': document_type,
                'metadata_json': metadata,
                'embedding_status': AiDocument.EMBEDDING_PENDING,
            },
        )
        return document

    def _build_content(self, raw_data, schedule_events):
        parts = [
            f'Title: {raw_data.title}',
            f'Source type: {raw_data.source_type}',
            f'Source URL: {raw_data.source_url}',
            f'Raw data id: {raw_data.id}',
            f'OCR text included: {self._has_ocr_text(raw_data)}',
            '',
            'Raw text:',
            raw_data.raw_text or '',
        ]

        if schedule_events:
            parts.extend(['', 'Parsed schedule events:'])
            for event in schedule_events:
                parts.append(
                    '\n'.join(
                        [
                            f'- ScheduleEvent id: {event.id}',
                            f'  Event title: {event.title}',
                            f'  Event type: {event.event_type}',
                            f'  Start: {event.start_at.isoformat()}',
                            f'  End: {event.end_at.isoformat()}',
                            f'  Description: {event.description}',
                        ]
                    )
                )
        else:
            parts.extend(['', 'Parsed schedule events: none'])

        return '\n'.join(part for part in parts if part is not None)

    def _build_metadata(self, raw_data, schedule_events):
        raw_metadata = dict(raw_data.metadata_json or {})
        event_payloads = [self._event_metadata(event) for event in schedule_events]
        first_event = schedule_events[0] if schedule_events else None
        return {
            **raw_metadata,
            'raw_data_model': 'sync.RawSsafyData',
            'raw_data_id': raw_data.id,
            'sync_raw_data_id': raw_data.id,
            'source_type': raw_data.source_type,
            'source_url': raw_data.source_url,
            'title': raw_data.title,
            'ocr_text_included': self._has_ocr_text(raw_data),
            'schedule_event_ids': [event.id for event in schedule_events],
            'schedule_events': event_payloads,
            'schedule_event_count': len(schedule_events),
            'has_schedule_events': bool(schedule_events),
            'event_type': first_event.event_type if first_event else raw_metadata.get('category', ''),
            'start_date': first_event.start_at.date().isoformat() if first_event else raw_metadata.get('start_date', ''),
            'end_date': first_event.end_at.date().isoformat() if first_event else raw_metadata.get('end_date', ''),
            'category': raw_metadata.get('category', first_event.event_type if first_event else ''),
        }

    def _event_metadata(self, event):
        return {
            'schedule_event_id': event.id,
            'event_title': event.title,
            'event_type': event.event_type,
            'event_start': event.start_at.isoformat(),
            'event_end': event.end_at.isoformat(),
            'is_all_day': event.is_all_day,
            'source_type': event.source_type,
            'source_id': event.source_id,
            'metadata_json': event.metadata_json or {},
        }

    def _document_type(self, raw_data):
        source_type = (raw_data.source_type or 'notice').upper()
        return f'{self.DOCUMENT_TYPE_PREFIX}{source_type}'

    def _has_ocr_text(self, raw_data):
        return '[OCR_TEXT]' in (raw_data.raw_text or '')
