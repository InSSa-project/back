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
                'source_title': raw_data.title if raw_data else metadata.get('source_title', ''),
                'source_url': raw_data.source_url if raw_data else metadata.get('source_url', ''),
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
        normalized = re.sub(r'\s+', ' ', (query or '').lower()).strip()
        tokens = re.findall(r'[\w\uac00-\ud7a3]+', normalized)
        ignored = {
            '\uc77c\uc815', '\uc2a4\ucf00\uc904', '\uc54c\ub824\uc918', '\uc54c\ub824', '\uac1c\uc778', '\ub0b4',
            '\uc624\ub298', '\ub0b4\uc77c', '\uc5b4\uc81c', '\uc774\ubc88', '\uc774\ubc88\uc8fc', '\uc774\ubc88\ub2ec', '\uc774\ubc88\uc6d4',
            '\ub2e4\uc74c', '\ub2e4\uc74c\uc8fc', '\ub2e4\uc74c\ub2ec', '\uc800\ubc88', '\uc800\ubc88\uc8fc', '\uc800\ubc88\ub2ec',
            '\uc9c0\ub09c', '\uc9c0\ub09c\uc8fc', '\uc9c0\ub09c\ub2ec', '\uc800\uc800\ubc88\ub2ec', '\uac00\uae4c\uc6b4',
            '\ub2e4\uac00\uc624\ub294', '\uc608\uc815\ub41c', '\uc608\uc815\uc778', '\uc55e\uc73c\ub85c', '\uace7', '\ub2e4\uc74c\uc73c\ub85c',
            '\uac00\uc7a5', '\uc81c\uc77c', '\ubb50', '\ubb50\uc57c', '\ubb50\ub2c8', '\ubb50\uc788\uc5b4', '\ubb50\uc788\ub2c8',
            '\uc788\ub294\uc9c0', '\uc788\uc5b4', '\uc788\ub2c8', '\uc788\ub098\uc694', '\uc788\uc744\uae4c', '\ud655\uc778', '\ud655\uc778\ud574\uc918',
            '\ucc3e\uc544\uc918', '\ubcf4\uc5ec\uc918', '\ubcf4\uc5ec', '\uc870\ud68c', '\uc870\ud68c\ud574\uc918', '\uc694\uc57d', '\uc694\uc57d\ud574\uc918',
            '\uc815\ub9ac', '\uc815\ub9ac\ud574\uc918', '\ub9d0\ud574\uc918', '\uc124\uba85\ud574\uc918', '\uc54c\uace0\uc2f6\uc5b4', '\uad81\uae08\ud574', 'ssafy',
        }
        suffixes = ('\ud574\uc918', '\ud574', '\uc918', '\uc694')
        filtered = []
        for token in tokens:
            if len(token) < 2:
                continue
            if token in ignored:
                continue
            stripped = self._strip_query_suffix(token, suffixes)
            if not stripped or stripped in ignored or len(stripped) < 2:
                continue
            if re.fullmatch(r'\d+(?:\ub144|\uc6d4|\uc77c|\ud68c|\ucc28)?', stripped):
                continue
            filtered.append(stripped)
        synonyms = {
            '\uc2dc\ud5d8': ['\uc2dc\ud5d8', '\ud3c9\uac00', '\uacfc\ubaa9\ud3c9\uac00', '\uc6d4\ub9d0\ud3c9\uac00', 'exam'],
            '\ud3c9\uac00': ['\ud3c9\uac00', '\uacfc\ubaa9\ud3c9\uac00', '\uc6d4\ub9d0\ud3c9\uac00', '\uc2dc\ud5d8', 'exam'],
            '\uacfc\ubaa9\ud3c9\uac00': ['\uacfc\ubaa9\ud3c9\uac00', '\ud3c9\uac00', '\uc2dc\ud5d8', 'exam'],
            '\uc6d4\ub9d0\ud3c9\uac00': ['\uc6d4\ub9d0\ud3c9\uac00', '\ud3c9\uac00', '\uc2dc\ud5d8', 'exam'],
        }
        expanded = []
        for token in filtered:
            expanded.extend(synonyms.get(token, [token]))
        return list(dict.fromkeys(expanded))

    def _strip_query_suffix(self, token: str, suffixes: tuple[str, ...]) -> str:
        current = token
        changed = True
        while changed:
            changed = False
            for suffix in suffixes:
                if current.endswith(suffix) and len(current) > len(suffix) + 1:
                    current = current[: -len(suffix)]
                    changed = True
                    break
        return current
