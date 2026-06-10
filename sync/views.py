import html
import json
import re
from datetime import date, datetime

from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.html import strip_tags
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST
from rest_framework.exceptions import AuthenticationFailed

from apps.users.authentication import JwtAuthentication

from schedules.models import ScheduleEvent
from sync.models import CrawlJobLog, RawSsafyData
from sync.services.notice_normalizer import (
    CATEGORIES,
    TRACKS,
    infer_notice_category,
    infer_notice_track,
    normalize_notice_category,
    notice_matches_search,
)
from sync.services.notice_policy import (
    is_user_visible_notice_source_type,
    notice_source_url,
    notice_title,
    user_visible_notice_queryset,
)
from sync.services.tracks import COMMON_TRACK_KEY, canonical_track_display, canonical_track_keys, normalize_track_key
from sync.services.import_service import run_notice_import
from sync.services.manual_ocr_service import apply_manual_ocr_text


SUMMARY_MAX_LENGTH = 200
DEBUG_SUMMARY_PATTERNS = (
    'Title:',
    'Source type:',
    'Source URL:',
    'Raw data id:',
    'OCR text included:',
    'Raw text:',
)
URL_PATTERN = re.compile(r'https?://\S+', re.IGNORECASE)
INTERNAL_KEY_VALUE_PATTERN = re.compile(
    r'\b(source_type|source url|source_url|raw_data_id|raw data id|ocr_text_included|ocr text included)\b\s*[:=]',
    re.IGNORECASE,
)
NOTICE_TRACK_QUERY_ALIASES = {
    'java_advanced': 'java_major',
    'java_basic': 'java_non_major',
    'common': COMMON_TRACK_KEY,
}
NOTICE_PUBLICATION_DATE_PATHS = (
    ('list_notice_date',),
    ('notice_list_date',),
    ('original_notice_date',),
    ('board_date',),
    ('raw_json', 'notice', 'date'),
    ('raw_json', 'notice', 'reg_date'),
    ('raw_json', 'notice', 'createdDate'),
    ('raw_json', 'date'),
    ('raw_json', 'reg_date'),
    ('raw_json', 'posted_at'),
    ('raw_json', 'published_at'),
    ('raw_json', 'source_date'),
    ('notice_date',),
    ('published_at',),
    ('posted_at',),
    ('source_date',),
)


@require_GET
def raw_data_list(request):
    permission_error = _staff_permission_error(request)
    if permission_error:
        return permission_error
    queryset = RawSsafyData.objects.all().order_by('-collected_at')
    source_type = request.GET.get('source_type')
    category = request.GET.get('category')
    if source_type:
        queryset = queryset.filter(source_type=source_type)
    if category:
        queryset = queryset.filter(metadata_json__category=category)
    return JsonResponse(
        [
            {
                'id': raw_data.id,
                'source_type': raw_data.source_type,
                'source_url': raw_data.source_url,
                'title': raw_data.title,
                'category': (raw_data.metadata_json or {}).get('category'),
                'document_type': (raw_data.metadata_json or {}).get('document_type'),
                'ocr_status': (raw_data.metadata_json or {}).get('ocr_status'),
                'ocr_text_length': (raw_data.metadata_json or {}).get('ocr_text_length', 0),
            }
            for raw_data in queryset[:100]
        ],
        safe=False,
    )


def notice_list(request):
    queryset = user_visible_notice_queryset(RawSsafyData.objects.all()).prefetch_related(
        'ai_documents',
        'schedule_events',
    )
    source_type = request.GET.get('source_type')
    category = request.GET.get('category')
    track_filter = request.GET.get('track_key') or request.GET.get('track')
    search = request.GET.get('search', '').strip()

    if source_type:
        if not is_user_visible_notice_source_type(source_type):
            return JsonResponse({'detail': 'Unsupported source_type.'}, status=400)
        queryset = queryset.filter(source_type=source_type)

    rows = list(queryset)
    category = normalize_notice_category(category)
    if category and category != 'all':
        if category not in CATEGORIES:
            return JsonResponse({'detail': 'Unsupported category.'}, status=400)
        rows = [row for row in rows if infer_notice_category(row) == category]
    if track_filter:
        track_key = _normalize_notice_track_key(track_filter)
        if track_key not in _supported_notice_track_keys():
            return JsonResponse({'detail': 'Unsupported track.'}, status=400)
        if track_key != COMMON_TRACK_KEY and category != 'mentoring':
            rows = [row for row in rows if _notice_matches_track(row, track_key)]
    if search:
        rows = [row for row in rows if notice_matches_search(row, search)]

    rows = sorted(rows, key=_notice_sort_key)

    page = _positive_int(request.GET.get('page'), default=1)
    page_size = _positive_int(request.GET.get('page_size'), default=20, maximum=100)
    start = (page - 1) * page_size
    end = start + page_size

    return JsonResponse(
        {
            'count': len(rows),
            'page': page,
            'page_size': page_size,
            'results': [_serialize_notice(row) for row in rows[start:end]],
        }
    )


@require_GET
def notice_detail(request, raw_data_id):
    raw_data = (
        user_visible_notice_queryset(RawSsafyData.objects.filter(pk=raw_data_id))
        .prefetch_related('ai_documents', 'schedule_events')
        .first()
    )
    if raw_data is None:
        return JsonResponse({'detail': 'Notice not found.'}, status=404)
    return JsonResponse(_serialize_notice(raw_data, include_detail=True))


@require_POST
def run_crawl(request):
    permission_error = _staff_permission_error(request)
    if permission_error:
        return permission_error
    mode = _parse_mode(request)
    job_log = run_notice_import(mode=mode)
    response_status = 'error' if job_log.status == CrawlJobLog.STATUS_FAILED else job_log.status
    return JsonResponse(
        {
            'status': response_status,
            'message': job_log.message,
            'raw_count': job_log.raw_count,
            'event_count': job_log.event_count,
            'failed_count': job_log.failed_count,
            'skipped_count': job_log.skipped_count,
            'notice_count': job_log.notice_count,
            'academic_rule_count': job_log.academic_rule_count,
            'no_schedule_count': job_log.no_schedule_count,
            'image_count': job_log.image_count,
            'ocr_processed_count': job_log.ocr_processed_count,
            'ocr_failed_count': job_log.ocr_failed_count,
            'crawler_mode': job_log.crawler_mode,
        }
    )


@require_POST
def set_manual_ocr_text(request, raw_data_id):
    permission_error = _staff_permission_error(request)
    if permission_error:
        return permission_error

    try:
        payload = _parse_json_body(request)
    except ValueError as exc:
        return JsonResponse({'detail': str(exc)}, status=400)

    raw_data = RawSsafyData.objects.filter(pk=raw_data_id).first()
    if raw_data is None:
        return JsonResponse({'detail': 'RawSsafyData not found.'}, status=404)

    try:
        result = apply_manual_ocr_text(
            raw_data,
            payload.get('ocr_text', ''),
            reparse=bool(payload.get('reparse')),
        )
    except ValueError as exc:
        return JsonResponse({'detail': str(exc)}, status=400)

    return JsonResponse(
        {
            'status': 'success',
            'raw_data_id': result.raw_data_id,
            'ocr_provider': result.ocr_provider,
            'ocr_status': result.ocr_status,
            'ocr_text_length': result.ocr_text_length,
            'updated': result.updated,
            'reparse_created_count': result.reparse_created_count,
        }
    )


def _staff_permission_error(request):
    user = request.user
    if not getattr(user, 'is_authenticated', False) and request.META.get('HTTP_AUTHORIZATION'):
        try:
            authenticated = JwtAuthentication().authenticate(request)
        except AuthenticationFailed:
            authenticated = None
        if authenticated:
            user, _auth = authenticated
            request.user = user
    if not getattr(user, 'is_authenticated', False):
        return JsonResponse({'detail': 'Authentication credentials were not provided.'}, status=401)
    if not getattr(user, 'is_staff', False):
        return JsonResponse({'detail': 'Admin permission is required.'}, status=403)
    return None


def _parse_mode(request):
    if not request.body:
        return 'sample'

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return 'sample'

    return payload.get('mode') or 'sample'


def _parse_json_body(request):
    if not request.body:
        return {}

    try:
        return json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError as exc:
        raise ValueError('Invalid JSON body.') from exc


def _serialize_notice(raw_data, include_detail=False):
    title = notice_title(raw_data)
    track_info = _notice_track_info(raw_data)
    content = _build_notice_content(raw_data)
    published_at, notice_date = _notice_publication_values(raw_data)
    related_schedules = [_serialize_notice_event(event) for event in _notice_schedule_events(raw_data)]
    source_url = notice_source_url(raw_data)
    payload = {
        'id': raw_data.id,
        'title': title,
        'source_type': raw_data.source_type,
        'category': infer_notice_category(raw_data),
        'track': track_info['track'],
        'track_key': track_info['track_key'],
        'is_common': track_info['is_common'],
        'summary': _build_notice_summary(raw_data, content),
        'content': content,
        'published_at': published_at.isoformat() if published_at else None,
        'notice_date': notice_date.isoformat() if notice_date else None,
        'collected_at': raw_data.collected_at.isoformat() if raw_data.collected_at else None,
        'source_url': source_url,
        'external_url': source_url,
        'related_schedules': related_schedules,
        'metadata_json': raw_data.metadata_json,
        'created_at': raw_data.collected_at.isoformat() if raw_data.collected_at else None,
        'updated_at': raw_data.collected_at.isoformat() if raw_data.collected_at else None,
    }
    if include_detail:
        payload.update(
            {
                'raw_json': raw_data.metadata_json,
                'ocr_text': raw_data.raw_text,
                'body': content,
                'schedule_events': related_schedules,
            }
        )
    return payload


def _notice_schedule_events(raw_data):
    prefetched = getattr(raw_data, '_prefetched_objects_cache', {})
    if 'schedule_events' in prefetched:
        return sorted(prefetched['schedule_events'], key=lambda event: (event.start_at, event.id))
    if not raw_data.pk:
        return []
    return list(ScheduleEvent.objects.filter(raw_data=raw_data).order_by('start_at', 'id'))


def _build_notice_summary(raw_data, content=None):
    metadata = raw_data.metadata_json or {}
    title = _clean_notice_text(notice_title(raw_data))
    for candidate in _notice_summary_candidates(raw_data, metadata, content):
        text = _prepare_user_notice_text(candidate, title=title, for_summary=True)
        if text:
            return _truncate_summary(text, SUMMARY_MAX_LENGTH)
    return ''


def _notice_summary_candidates(raw_data, metadata, content=None):
    return [
        metadata.get('summary'),
        metadata.get('notice_summary'),
        metadata.get('description'),
        metadata.get('body'),
        metadata.get('content'),
        metadata.get('raw_text'),
        raw_data.raw_text,
        raw_data.raw_html,
        _first_ai_document_content(raw_data),
        content,
    ]


def _build_notice_content(raw_data):
    metadata = raw_data.metadata_json or {}
    title = _clean_notice_text(notice_title(raw_data))
    for candidate in [
        metadata.get('body'),
        metadata.get('content'),
        metadata.get('raw_text'),
        raw_data.raw_text,
        raw_data.raw_html,
        _first_ai_document_content(raw_data),
    ]:
        text = _prepare_user_notice_text(candidate, title=title, for_summary=False)
        if text:
            return text
    return ''


def _first_ai_document_content(raw_data):
    documents = getattr(raw_data, '_prefetched_objects_cache', {}).get('ai_documents')
    if documents is None and raw_data.pk:
        documents = raw_data.ai_documents.all().order_by('-updated_at', '-id')[:1]
    for document in documents or []:
        content = _clean_notice_text(getattr(document, 'content', ''))
        if content:
            return content
    return ''


def _prepare_user_notice_text(value, title='', for_summary=False):
    text = _clean_notice_text(value)
    if not text:
        return ''
    if _is_debug_like_notice_text(text):
        return ''
    text = _remove_url_text(text)
    text = _clean_notice_text(text)
    text = _remove_repeated_title_prefix(text, title)
    if not text:
        return ''
    if _is_debug_like_notice_text(text):
        return ''
    if _is_title_only_summary(text, title):
        return ''
    if for_summary and len(text) < 20:
        return ''
    return text


def _is_debug_like_notice_text(text):
    if not text:
        return False
    if any(pattern.lower() in text.lower() for pattern in DEBUG_SUMMARY_PATTERNS):
        return True
    if INTERNAL_KEY_VALUE_PATTERN.search(text):
        return True
    if URL_PATTERN.fullmatch(text.strip()):
        return True
    if 'edu.ssafy.com' in text.lower():
        return True
    return False


def _remove_url_text(text):
    return URL_PATTERN.sub('', text)


def _notice_publication_values(raw_data):
    metadata = raw_data.metadata_json or {}
    for path in NOTICE_PUBLICATION_DATE_PATHS:
        published_at, notice_date = _parse_notice_publication_value(_nested_metadata_value(metadata, path))
        if published_at or notice_date:
            return published_at, notice_date
    return None, None


def _parse_notice_publication_value(value):
    if not value:
        return None, None
    if isinstance(value, datetime):
        parsed = _ensure_aware_datetime(value)
        return parsed, timezone.localdate(parsed)
    if isinstance(value, date):
        return None, value

    text = str(value).strip()
    if not text:
        return None, None

    parsed_datetime = parse_datetime(text)
    if parsed_datetime:
        parsed_datetime = _ensure_aware_datetime(parsed_datetime)
        return parsed_datetime, timezone.localdate(parsed_datetime)

    parsed_date = parse_date(text[:10]) or _parse_loose_notice_date(text)
    if parsed_date:
        return None, parsed_date
    return None, None


def _parse_loose_notice_date(text):
    match = re.search(r'(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})', text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _ensure_aware_datetime(value):
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _notice_sort_key(raw_data):
    published_at, notice_date = _notice_publication_values(raw_data)
    collected_timestamp = _datetime_timestamp(getattr(raw_data, 'collected_at', None))
    if published_at:
        local_published = timezone.localtime(published_at)
        return (0, -local_published.date().toordinal(), -local_published.timestamp(), -collected_timestamp, -raw_data.id)
    if notice_date:
        return (0, -notice_date.toordinal(), 0, -collected_timestamp, -raw_data.id)
    return (1, 0, 0, -collected_timestamp, -raw_data.id)


def _datetime_timestamp(value):
    if not value:
        return 0
    return _ensure_aware_datetime(value).timestamp()


def _notice_track_info(raw_data):
    metadata = raw_data.metadata_json or {}
    audience = metadata.get('audience') or {}
    inferred = infer_notice_track(raw_data)
    source_value = (
        metadata.get('track_key')
        or audience.get('track_key')
        or metadata.get('track')
        or audience.get('track')
        or inferred
    )
    track_key = _normalize_notice_track_key(source_value)
    is_common = _is_common_notice_track(track_key, source_value, metadata)
    if is_common:
        track_key = COMMON_TRACK_KEY
    display = metadata.get('track_display') or metadata.get('track_name') or canonical_track_display(track_key)
    return {
        'track': display if not is_common else canonical_track_display(COMMON_TRACK_KEY),
        'track_key': track_key,
        'is_common': is_common,
    }


def _normalize_notice_track_key(value):
    normalized = normalize_track_key(value)
    alias_key = str(normalized or value or '').strip().lower().replace('-', '_').replace(' ', '_')
    if alias_key in NOTICE_TRACK_QUERY_ALIASES:
        return NOTICE_TRACK_QUERY_ALIASES[alias_key]
    if normalized == 'common':
        return COMMON_TRACK_KEY
    if normalized:
        return normalized
    inferred = str(value or '').strip().lower()
    return COMMON_TRACK_KEY if inferred == 'common' else inferred


def _is_common_notice_track(track_key, source_value, metadata):
    if metadata.get('is_common') is True or metadata.get('is_global') is True:
        return True
    if track_key == COMMON_TRACK_KEY:
        return True
    return str(source_value or '').strip().lower() in {'', 'common', 'all', 'global', '공통', '전체'}


def _notice_matches_track(raw_data, expected_track_key):
    track_info = _notice_track_info(raw_data)
    if track_info['is_common']:
        return True
    return track_info['track_key'] == expected_track_key


def _supported_notice_track_keys():
    return set(TRACKS) | {COMMON_TRACK_KEY} | set(canonical_track_keys()) | set(NOTICE_TRACK_QUERY_ALIASES.values())


def _clean_notice_text(value):
    text = _stringify_text(value)
    if not text:
        return ''
    text = html.unescape(strip_tags(text))
    return re.sub(r'\s+', ' ', text).strip()


def _stringify_text(value):
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return ''
    return str(value)


def _nested_metadata_value(metadata, path):
    value = metadata
    for key in path:
        if not isinstance(value, dict):
            return ''
        value = value.get(key)
    return value


def _remove_repeated_title_prefix(text, title):
    if not text or not title:
        return text
    if _compact_text(text) == _compact_text(title):
        return ''
    if _compact_text(text).startswith(_compact_text(title)):
        stripped = text[len(title):].strip(' :-|·\n\t')
        if stripped:
            return stripped
    return text


def _is_title_only_summary(text, title):
    if not text:
        return True
    if not title:
        return False
    compact_text = _compact_text(text)
    compact_title = _compact_text(title)
    if compact_text == compact_title:
        return True
    return len(text) < 20 and compact_text in compact_title


def _compact_text(value):
    return re.sub(r'\s+', '', str(value or '')).lower()


def _truncate_summary(text, max_length):
    if len(text) <= max_length:
        return text
    return text[:max_length].rstrip() + '...'


def _serialize_notice_event(event):
    raw_data = event.raw_data
    return {
        'id': event.id,
        'title': event.title,
        'event_type': event.event_type,
        'start_at': event.start_at.isoformat(),
        'end_at': event.end_at.isoformat(),
        'source_url': notice_source_url(raw_data) if raw_data else None,
        'metadata_json': event.metadata_json,
    }


def _positive_int(value, default, maximum=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 1:
        return default
    return min(parsed, maximum) if maximum else parsed
