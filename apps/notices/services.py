from django.db import transaction
from django.utils import timezone

from ai_server.retrieval.query_parser import DateExtractor


class NoticeImportService:
    def create_import_log(self, user, import_type, items=None, ingest=True):
        from apps.ai.models import AiDocument
        from apps.ai.rag_ingestion import RagIngestionService
        from .models import RawSsafyData, SsafyDataImportLog

        items = items or []
        with transaction.atomic():
            import_log = SsafyDataImportLog.objects.create(
                user=user,
                import_type=import_type,
                status=SsafyDataImportLog.STATUS_PROCESSING if items else SsafyDataImportLog.STATUS_SUCCESS,
                total_count=len(items),
                started_at=timezone.now(),
            )
            documents = []
            for item in items:
                raw_data = RawSsafyData.objects.create(
                    import_log=import_log,
                    source_type=item.get('source_type') or item.get('document_type') or RawSsafyData.SOURCE_NOTICE,
                    title=item.get('title', 'Untitled SSAFY document'),
                    raw_json=item,
                    raw_text=item.get('raw_text') or item.get('content') or '',
                    parsed_text=self._clean_text(item.get('parsed_text') or item.get('content') or item.get('raw_text') or ''),
                )
                document = AiDocument.objects.create(
                    raw_data=raw_data,
                    title=raw_data.title,
                    content=raw_data.parsed_text or raw_data.raw_text,
                    document_type=raw_data.source_type,
                    metadata_json=self._build_ai_metadata(item, raw_data, import_type),
                )
                documents.append(document)
            import_log.status = SsafyDataImportLog.STATUS_SUCCESS
            import_log.success_count = len(documents)
            import_log.completed_at = timezone.now()
            import_log.save(update_fields=['status', 'success_count', 'completed_at'])

        if ingest and documents:
            ingestion = RagIngestionService()
            for document in documents:
                ingestion.ingest_document(document)
        return import_log

    def _clean_text(self, text):
        return '\n'.join(line.strip() for line in str(text).splitlines() if line.strip())

    def _build_ai_metadata(self, item, raw_data, import_type):
        content_for_date = ' '.join([
            str(item.get('title', '')),
            str(item.get('content') or item.get('parsed_text') or item.get('raw_text') or ''),
        ])
        extracted_start, extracted_end, _ = DateExtractor().extract(content_for_date)
        start_date = item.get('start_date') or item.get('date') or extracted_start
        end_date = item.get('end_date') or start_date or extracted_end
        return {
            'document_id': None,
            'title': raw_data.title,
            'source_type': raw_data.source_type,
            'event_type': item.get('event_type', ''),
            'start_date': start_date,
            'end_date': end_date,
            'campus': item.get('campus', ''),
            'generation': item.get('generation', ''),
            'track': item.get('track', ''),
            'import_type': import_type,
            'raw_data_id': raw_data.id,
        }
