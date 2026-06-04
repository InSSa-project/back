import os
import re
from datetime import time

from django.db.models import Q
from django.utils import timezone

from ai_server.rag.schemas.documents import RetrievedChunk


class ScheduleRetrievalService:
    def __init__(self, vectorstore=None):
        self.vectorstore = vectorstore

    def retrieve(self, parsed_query, filters: dict | None = None, query: str = ''):
        filters = filters or {}
        if self.vectorstore is not None:
            return self._retrieve_from_vectorstore(parsed_query, filters=filters, query=query)
        return self._retrieve_from_db(parsed_query, filters=filters, query=query)

    def _retrieve_from_vectorstore(self, parsed_query, filters: dict, query: str = ''):
        records = self.vectorstore.search_by_metadata(
            start_date=parsed_query.start_date,
            end_date=parsed_query.end_date,
            exact=parsed_query.exact_match_required,
            filters=filters,
        )
        tokens = self._query_tokens(query)
        if tokens:
            records = [record for record in records if self._match_count(record, tokens) > 0]
        records = sorted(records, key=lambda chunk: self._sort_key(chunk, tokens))
        return records[: parsed_query.result_limit] if parsed_query.result_limit else records

    def _retrieve_from_db(self, parsed_query, filters: dict, query: str = ''):
        self._ensure_django_ready()
        from schedules.models import ScheduleEvent

        queryset = ScheduleEvent.objects.select_related('raw_data', 'owner')
        user_id = filters.get('user_id')
        legacy_unowned_personal = Q(owner__isnull=True, raw_data__isnull=True, source_type='manual', event_type='personal')
        if user_id:
            queryset = queryset.filter(Q(owner__isnull=True) | Q(owner_id=user_id)).exclude(legacy_unowned_personal)
        else:
            queryset = queryset.filter(owner__isnull=True).exclude(legacy_unowned_personal)

        if parsed_query.start_date and parsed_query.end_date:
            start_at = self._date_boundary(parsed_query.start_date, is_end=False)
            end_at = self._date_boundary(parsed_query.end_date, is_end=True)
            queryset = queryset.filter(end_at__gt=start_at, start_at__lte=end_at)

        event_type = filters.get('event_type')
        if event_type:
            queryset = queryset.filter(event_type=event_type)

        events = list(queryset.order_by('start_at', 'id')[:100])
        tokens = self._query_tokens(query)
        if tokens:
            events = [event for event in events if self._event_match_count(event, tokens) > 0]
        if parsed_query.result_limit:
            events = events[: parsed_query.result_limit]
        return [self._chunk_from_event(event) for event in events]

    def _chunk_from_event(self, event):
        start_at = timezone.localtime(event.start_at)
        end_at = timezone.localtime(event.end_at)
        metadata = dict(event.metadata_json or {})
        is_personal = bool(event.owner_id)
        raw_data = event.raw_data if event.raw_data_id else None
        source_type = 'personal' if is_personal else (event.source_type or 'public')
        visibility = 'personal' if is_personal else 'public'
        content = '\n'.join(
            [
                f'Title: {event.title}',
                f'Description: {event.description or ""}',
                f'Start: {start_at.isoformat()}',
                f'End: {end_at.isoformat()}',
                f'Event type: {event.event_type}',
                f'Visibility: {visibility}',
                f'Source type: {source_type}',
                f'Source title: {raw_data.title if raw_data else metadata.get("source_title", "")}',
                f'Source URL: {raw_data.source_url if raw_data else metadata.get("source_url", "")}',
            ]
        )
        metadata.update(
            {
                'schedule_event_id': event.id,
                'start_date': start_at.date().isoformat(),
                'end_date': end_at.date().isoformat(),
                'start_at': start_at.isoformat(),
                'end_at': end_at.isoformat(),
                'event_type': event.event_type,
                'source_type': source_type,
                'visibility': visibility,
                'owner_id': event.owner_id,
                'is_personal': is_personal,
                'is_public': not is_personal,
            }
        )
        return RetrievedChunk(
            chunk_id=f'schedule-event:{event.id}',
            ai_document_id=0,
            raw_data_id=event.raw_data_id,
            title=event.title,
            content=content,
            document_type='SCHEDULE_EVENT',
            metadata=metadata,
            score=1.0,
        )

    def _date_boundary(self, value: str, is_end: bool):
        parsed_date = timezone.datetime.fromisoformat(value).date()
        boundary = timezone.datetime.combine(parsed_date, time.max if is_end else time.min)
        return timezone.make_aware(boundary, timezone.get_current_timezone())

    def _ensure_django_ready(self):
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crawler_service.settings')
        import django
        from django.apps import apps

        if not apps.ready:
            django.setup()

    def _sort_key(self, chunk, tokens: list[str]):
        match_count = self._match_count(chunk, tokens)
        return (-match_count, chunk.metadata.get('start_date') or '', chunk.title)

    def _match_count(self, chunk, tokens: list[str]) -> int:
        text = f'{chunk.title} {chunk.content}'.lower()
        return sum(1 for token in tokens if token in text)

    def _event_match_count(self, event, tokens: list[str]) -> int:
        metadata = event.metadata_json or {}
        raw_data = event.raw_data if event.raw_data_id else None
        text = ' '.join(
            [
                str(event.title or ''),
                str(event.description or ''),
                str(event.event_type or ''),
                str(event.source_type or ''),
                str(metadata.get('source_title') or ''),
                str(raw_data.title if raw_data else ''),
            ]
        ).lower()
        return sum(1 for token in tokens if token in text)

    def _query_tokens(self, query: str) -> list[str]:
        tokens = re.findall(r'[\w가-힣]+', query.lower())
        ignored = {'일정', '알려줘', '개인', '내', '오늘', '내일', '어제', '이번', '다음', '가까운', '다가오는', '예정된', '예정인', '앞으로', '곧', '가장', '제일', 'ssafy'}
        filtered = [
            token
            for token in tokens
            if len(token) >= 2
            and token not in ignored
            and not re.fullmatch(r'\d+(?:년|월|일)?', token)
        ]
        synonyms = {
            '시험': ['시험', '평가', '과목평가', '월말평가', 'exam'],
            '평가': ['평가', '과목평가', '월말평가', '시험', 'exam'],
        }
        expanded = []
        for token in filtered:
            expanded.extend(synonyms.get(token, [token]))
        return list(dict.fromkeys(expanded))



