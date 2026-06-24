import html
import json
import logging
import re
from datetime import date, datetime
from urllib.parse import urlencode, urljoin

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.html import strip_tags
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from rest_framework.exceptions import AuthenticationFailed

from apps.users.authentication import JwtAuthentication

from schedules.models import ScheduleEvent
from sync.models import CrawlJobLog, RawSsafyData, UserNoticeReadStatus
from sync.services.notice_normalizer import (
    CATEGORIES,
    TRACKS,
    infer_notice_category,
    normalize_notice_category,
)
from sync.services.notice_policy import (
    is_user_visible_notice_source_type,
    notice_source_url,
    notice_title,
    user_visible_notice_queryset,
)
from sync.services.tracks import COMMON_TRACK_KEY, canonical_track_display, canonical_track_keys, normalize_track_key, track_key_from_text
from sync.services.import_service import run_notice_import
from sync.services.manual_ocr_service import apply_manual_ocr_text


LOGGER = logging.getLogger(__name__)

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
    user = _authenticate_user(request)
    queryset = user_visible_notice_queryset(RawSsafyData.objects.all()).prefetch_related(
        'ai_documents',
        'schedule_events',
    )
    source_type = request.GET.get('source_type')
    category = request.GET.get('category')
    explicit_track_filter = request.GET.get('track_key') or request.GET.get('track')
    scope = request.GET.get('scope')
    track_filter = explicit_track_filter
    search = _normalize_search_query(request.GET.get('search') or request.GET.get('q'))

    if source_type:
        if not is_user_visible_notice_source_type(source_type):
            return JsonResponse({'detail': 'Unsupported source_type.'}, status=400)
        queryset = queryset.filter(source_type=source_type)

    if search:
        queryset = _apply_notice_search_queryset(queryset, search)

    category = normalize_notice_category(category)
    if not track_filter and _explicit_all_scope(scope):
        track_filter = COMMON_TRACK_KEY
    if not track_filter and getattr(user, 'is_authenticated', False):
        track_filter = _user_notice_track(user)

    rows = list(queryset)
    if category and category != 'all':
        if category not in CATEGORIES:
            return JsonResponse({'detail': 'Unsupported category.'}, status=400)
        rows = [row for row in rows if infer_notice_category(row) == category]
    if track_filter:
        track_key = _normalize_notice_track_key(track_filter)
        if track_key not in _supported_notice_track_keys():
            return JsonResponse({'detail': 'Unsupported track.'}, status=400)
        if track_key == COMMON_TRACK_KEY and _explicit_all_track(explicit_track_filter):
            pass  # track=all 명시 요청 → 트랙 필터 없이 전체 반환
        elif track_key != COMMON_TRACK_KEY and category != 'mentoring':
            rows = [row for row in rows if _notice_matches_track(row, track_key)]
        elif track_key == COMMON_TRACK_KEY:
            rows = [row for row in rows if _notice_track_info(row)['is_common']]

    rows = sorted(rows, key=_notice_sort_key)

    page = _positive_int(request.GET.get('page'), default=1)
    page_size = _positive_int(request.GET.get('page_size'), default=20, maximum=100)
    start = (page - 1) * page_size
    end = start + page_size

    return JsonResponse(
        {
            'count': len(rows),
            'next': _page_url(request, page + 1, page_size, len(rows)),
            'previous': _page_url(request, page - 1, page_size, len(rows)) if page > 1 else None,
            'page': page,
            'page_size': page_size,
            'results': [_serialize_notice(row, request=request) for row in rows[start:end]],
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
    return JsonResponse(_serialize_notice(raw_data, include_detail=True, request=request))


@require_GET
def notice_summary(request, raw_data_id):
    raw_data = (
        user_visible_notice_queryset(RawSsafyData.objects.filter(pk=raw_data_id))
        .prefetch_related('ai_documents', 'schedule_events')
        .first()
    )
    if raw_data is None:
        return JsonResponse({'detail': 'Notice not found.'}, status=404)

    content = _build_notice_content(raw_data)
    summary = _build_notice_summary(raw_data, content)
    if not summary:
        return JsonResponse(
            {
                'detail': 'Summary is not available for this notice.',
                'source_title': notice_title(raw_data),
                'source_url': notice_source_url(raw_data),
            },
            status=422,
        )

    return JsonResponse(
        {
            'summary': summary,
            'source_title': notice_title(raw_data),
            'source_url': notice_source_url(raw_data),
            'generated_at': timezone.now().isoformat(),
            'is_ai_generated': False,
            'summary_type': 'rule_based',
        }
    )


@csrf_exempt
@require_POST
def mark_notice_read(request, raw_data_id):
    user = _authenticate_user(request)
    if user is None:
        return JsonResponse({'detail': 'Authentication credentials were invalid.'}, status=401)
    if not getattr(user, 'is_authenticated', False):
        return JsonResponse({'detail': 'Authentication credentials were not provided.'}, status=401)

    raw_data = user_visible_notice_queryset(RawSsafyData.objects.filter(pk=raw_data_id)).first()
    if raw_data is None:
        return JsonResponse({'detail': 'Notice not found.'}, status=404)

    status, _created = UserNoticeReadStatus.objects.update_or_create(
        user=user,
        raw_data=raw_data,
        defaults={'read_at': timezone.now()},
    )
    return JsonResponse(
        {
            'notice_id': raw_data.id,
            'is_read': True,
            'read_at': status.read_at.isoformat(),
        }
    )


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


def _authenticate_user(request):
    if getattr(request.user, 'is_authenticated', False):
        return request.user
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header:
        return request.user
    try:
        authenticated = JwtAuthentication().authenticate(request)
    except AuthenticationFailed:
        return None
    if authenticated is None:
        return request.user
    user, _auth = authenticated
    request.user = user
    return user


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


def _normalize_search_query(value):
    search = str(value or '').strip()
    if not search:
        return ''
    return search[:100]


def _apply_notice_search_queryset(queryset, search):
    return queryset.filter(Q(title__icontains=search))


def _explicit_all_scope(value):
    return str(value or '').strip().lower() == 'all'


def _explicit_all_track(value):
    return str(value or '').strip().lower().replace('-', '_').replace(' ', '_') in {'all', 'all_tracks'}


def _user_notice_track(user):
    try:
        profile = getattr(user, 'profile', None)
    except (AttributeError, ObjectDoesNotExist):
        profile = None
    profile_track = getattr(profile, 'track', None) if profile is not None else None
    user_track = getattr(user, 'track', '')
    track_key = _normalize_notice_track_key(profile_track or user_track)
    return track_key or COMMON_TRACK_KEY


def _page_url(request, page, page_size, total_count):
    if page < 1:
        return None
    start = (page - 1) * page_size
    if start >= total_count:
        return None
    query = request.GET.copy()
    query['page'] = page
    query['page_size'] = page_size
    return request.build_absolute_uri(f'{request.path}?{urlencode(query, doseq=True)}')


def _serialize_notice(raw_data, include_detail=False, request=None):
    title = notice_title(raw_data)
    track_info = _notice_track_info(raw_data)
    content = _build_notice_content(raw_data)
    published_at, notice_date = _notice_publication_values(raw_data)
    linked_events = [
        _serialize_notice_event(event)
        for event in _notice_schedule_events(raw_data, include_metadata_links=include_detail)
    ]
    source_url = notice_source_url(raw_data)
    images = _serialize_notice_images(raw_data, request=request, title=title)
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
        'images': images,
        'linked_events': linked_events,
        'related_schedules': linked_events,
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
                'schedule_events': linked_events,
            }
        )
    return payload


def _serialize_notice_images(raw_data, request=None, title=''):
    metadata = raw_data.metadata_json or {}
    candidates = _notice_image_candidates(metadata)
    if not candidates:
        return []

    images = []
    seen = set()
    for index, candidate in enumerate(candidates):
        image = _normalize_notice_image(candidate, index, raw_data, request=request, title=title)
        if not image or image['url'] in seen:
            continue
        seen.add(image['url'])
        images.append(image)
    return sorted(images, key=lambda item: (item['sort_order'], item['id'] or 0, item['url']))


def _notice_image_candidates(metadata):
    # notice_images (structured, new format) takes priority
    notice_images = metadata.get('notice_images') if isinstance(metadata, dict) else None
    if isinstance(notice_images, list) and notice_images:
        return list(notice_images)

    candidates = []
    for source in _notice_metadata_sources_for_images(metadata):
        for key in ('images', 'image_urls', 'image_url', 'attachments', 'files'):
            value = source.get(key) if isinstance(source, dict) else None
            if isinstance(value, list):
                candidates.extend(value)
            elif value:
                candidates.append(value)
    return candidates


def _notice_metadata_sources_for_images(metadata):
    if not isinstance(metadata, dict):
        return []
    sources = [metadata]
    for key in ('raw_json', 'metadata_json', 'detail', 'notice'):
        value = metadata.get(key)
        if isinstance(value, dict):
            sources.append(value)
    return sources


def _normalize_notice_image(candidate, index, raw_data, request=None, title=''):
    if isinstance(candidate, str):
        source = {'url': candidate}
    elif isinstance(candidate, dict):
        source = candidate
    else:
        return None

    url = _notice_image_url(source, raw_data, request=request)
    if not url:
        return None

    sort_order = _non_negative_int(source.get('sort_order'), default=index)
    image_id = source.get('id')
    result = {
        'id': image_id if image_id is not None else index + 1,
        'url': url,
        'alt': str(source.get('alt') or source.get('title') or title or '').strip(),
        'sort_order': sort_order,
    }
    # Include optimisation metadata when available (from notice_images structured entries)
    for extra_key in ('width', 'height', 'size_bytes', 'format'):
        val = source.get(extra_key) if isinstance(source, dict) else None
        if val is not None:
            result[extra_key] = val
    return result


def _notice_image_url(source, raw_data, request=None):
    # notice_images structured entry: prefer stable storage_url, fall back to source_url
    if isinstance(source, dict):
        storage_url = str(source.get('storage_url') or '').strip()
        if storage_url.startswith(('http://', 'https://')):
            return storage_url
        # source_url is the original CDN URL — use as fallback when storage not yet uploaded
        src_fallback = str(source.get('source_url') or '').strip()
        if src_fallback.startswith(('http://', 'https://')):
            LOGGER.debug('notice_image_url: using source_url fallback for notice id=%s', getattr(raw_data, 'id', '?'))
            return src_fallback

    value = source.get('url') or source.get('image_url') or source.get('src') or source.get('href') or source.get('path') if isinstance(source, dict) else None
    url = str(value or '').strip()
    if not url or url.startswith(('data:', 'javascript:', 'mailto:', '#')):
        return ''
    lowered = url.lower()
    if lowered.startswith(('http://', 'https://')):
        return url
    if lowered.startswith('//'):
        return f'https:{url}'
    if lowered.startswith('/media/') or lowered.startswith('/static/'):
        # Skip /media/ URLs whose file no longer exists on disk
        # (Oracle Cloud ephemeral filesystem: files are lost on every redeploy).
        if lowered.startswith('/media/') and not _local_media_file_exists(url):
            return ''
        return request.build_absolute_uri(url) if request is not None else url
    if lowered.startswith('media/') or lowered.startswith('static/'):
        path = f'/{url}'
        if path.lower().startswith('/media/') and not _local_media_file_exists(path):
            return ''
        return request.build_absolute_uri(path) if request is not None else path

    source_url = notice_source_url(raw_data) or getattr(raw_data, 'source_url', '')
    if source_url:
        return urljoin(source_url, url)
    return ''


def _notice_schedule_events(raw_data, include_metadata_links=False):
    prefetched = getattr(raw_data, '_prefetched_objects_cache', {})
    events = []
    if 'schedule_events' in prefetched:
        events = list(prefetched['schedule_events'])
    if not raw_data.pk:
        return []
    elif not events:
        events = list(ScheduleEvent.objects.filter(raw_data=raw_data))

    if include_metadata_links:
        metadata_events = ScheduleEvent.objects.filter(
            Q(metadata_json__raw_data_id=raw_data.id) | Q(metadata_json__raw_data_id=str(raw_data.id))
        )
        events.extend(metadata_events)

    unique_by_id = {}
    for event in events:
        if event.id is not None:
            unique_by_id[event.id] = event
    return sorted(unique_by_id.values(), key=lambda event: (event.start_at, event.id))


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
    source_value = (
        metadata.get('track_key')
        or audience.get('track_key')
        or metadata.get('track')
        or audience.get('track')
    )
    track_key = _normalize_notice_track_key(source_value)
    is_common = _is_common_notice_track(track_key, source_value, metadata)

    # No explicit track metadata → try to infer from title so that notices like
    # "[학습] Java 전공 트랙 시간표" are not treated as common for all users.
    if not is_common and not track_key and not source_value:
        title = notice_title(raw_data)
        inferred = track_key_from_text(title)
        if inferred and inferred != COMMON_TRACK_KEY:
            track_key = inferred
            is_common = False
        else:
            is_common = True
            track_key = COMMON_TRACK_KEY

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
    # Note: "no track info" is NOT treated as common here. Title-based inference
    # is performed in _notice_track_info after this function returns False.
    return str(source_value or '').strip().lower() in {'common', 'all', 'global', '공통', '전체'}


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
    metadata = event.metadata_json or {}
    audience = metadata.get('audience') or {}
    track_key = _normalize_notice_track_key(
        metadata.get('track_key')
        or audience.get('track_key')
        or metadata.get('track')
        or audience.get('track')
    )
    return {
        'id': event.id,
        'title': event.title,
        'event_type': event.event_type,
        'start_at': event.start_at.isoformat(),
        'end_at': event.end_at.isoformat(),
        'track': canonical_track_display(track_key) if track_key else '',
        'track_key': track_key,
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


def _non_negative_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _local_media_file_exists(url):
    """Return True only if the /media/ path maps to an existing local file."""
    from pathlib import Path
    try:
        rel = str(url or '').lstrip('/')
        if rel.startswith('media/'):
            rel = rel[len('media/'):]
        path = Path(settings.MEDIA_ROOT) / rel
        return path.is_file()
    except Exception:
        return False
