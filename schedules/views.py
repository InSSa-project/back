import logging
from datetime import datetime, time

import json

from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework.exceptions import AuthenticationFailed

from .models import ScheduleEvent
from apps.users.authentication import JwtAuthentication
from schedules.services import filter_calendar_visible_events
from sync.models import RawSsafyData
from sync.services.tracks import COMMON_TRACK_KEY, normalize_track_key

from .utils import is_meaningless_schedule_title, normalize_schedule_display_title


CANONICAL_TRACKS = {
    'python',
    'java_non_major',
    'java_major',
    'embedded',
    'mobile',
    'embedded_robot',
    'data',
    'meister',
}
TRACK_ALIASES = {
    'python': 'python',
    '파이썬': 'python',
    'java_non_major': 'java_non_major',
    'java 비전공': 'java_non_major',
    'java_비전공': 'java_non_major',
    'java(비전공)': 'java_non_major',
    'java_major': 'java_major',
    'java 전공': 'java_major',
    'java_전공': 'java_major',
    'java(전공)': 'java_major',
    'embedded': 'embedded',
    '임베디드': 'embedded',
    'mobile': 'mobile',
    '모바일': 'mobile',
    'embedded_robot': 'embedded_robot',
    'embedded robot': 'embedded_robot',
    '임베디드로봇': 'embedded_robot',
    '임베디드 로봇': 'embedded_robot',
    'data': 'data',
    '데이터': 'data',
    'meister': 'meister',
    '마이스터': 'meister',
}
COMMON_TRACK_VALUES = {'', 'common', 'all', 'global', '공통', '전체'}
COMMON_TITLE_KEYWORDS = (
    'SSAFY DAY',
    '공휴일',
    '온라인 위크',
    '과목평가',
    '월말평가',
    '개인 프로젝트 발표',
    '관통프로젝트',
)
OTHER_EVENT_TYPES = {
    'study',
    'assignment',
    'lecture',
    'deadline',
    'notice',
    'generated',
    'evaluation',
    'learning',
    'mentoring',
    'unknown',
    'other',
    'etc',
    '기타',
}
ALLOWED_EVENT_TYPES = OTHER_EVENT_TYPES | {
    'exam',
    'project',
    'personal',
    'holiday',
}
logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def event_list(request):
    user = _authenticate_request(request)
    if user is None:
        return JsonResponse({'detail': 'Authentication credentials were invalid.'}, status=401)
    if request.method == 'POST':
        return _create_event(request, user)

    start_at = _parse_boundary(request.GET.get('start'), is_end=False)
    end_at = _parse_boundary(request.GET.get('end'), is_end=True)

    events = ScheduleEvent.objects.all()
    if getattr(user, 'is_authenticated', False):
        events = events.filter(Q(owner__isnull=True) | Q(owner=user))
    else:
        events = events.filter(owner__isnull=True)
    event_type = request.GET.get('event_type')
    if event_type:
        events = _filter_queryset_by_event_type(events, event_type)
    events = filter_calendar_visible_events(events.select_related('raw_data'))
    events = _filter_events_by_audience_params(events, request.GET, user)
    events = [event for event in events if _event_occurs_in_range(event, start_at, end_at)]
    events = [event for event in events if not _is_hidden_meaningless_event(event)]

    events = sorted(events, key=_event_list_sort_key)
    return JsonResponse([_serialize_event(event) for event in events], safe=False)


def _create_event(request, user):
    if not getattr(user, 'is_authenticated', False):
        return JsonResponse({'detail': 'Authentication credentials were not provided.'}, status=401)
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'detail': 'Invalid JSON payload.'}, status=400)

    invalid_fields = set(payload) - {
        'title',
        'description',
        'start_at',
        'end_at',
        'deadline_at',
        'is_all_day',
        'is_global',
        'is_important',
        'event_type',
        'metadata',
        'metadata_json',
        'source_type',
        'track',
    }
    if invalid_fields:
        return JsonResponse(
            {'detail': f'Unsupported fields: {", ".join(sorted(invalid_fields))}'},
            status=400,
        )

    title = str(payload.get('title') or '').strip()
    if not title:
        return JsonResponse({'detail': 'Title is required.'}, status=400)
    if not payload.get('start_at'):
        return JsonResponse({'detail': 'Start time is required.'}, status=400)
    if not payload.get('end_at'):
        return JsonResponse({'detail': 'End time is required.'}, status=400)

    start_at = _parse_patch_datetime(payload['start_at'])
    end_at = _parse_patch_datetime(payload['end_at'])
    if start_at is None:
        return JsonResponse({'detail': 'Invalid datetime format: start_at'}, status=400)
    if end_at is None:
        return JsonResponse({'detail': 'Invalid datetime format: end_at'}, status=400)
    if end_at < start_at:
        return JsonResponse({'detail': 'End time must be after start time.'}, status=400)

    metadata_json = payload.get('metadata_json', payload.get('metadata', {}))
    if metadata_json is None:
        metadata_json = {}
    if not isinstance(metadata_json, dict):
        return JsonResponse({'detail': 'metadata_json must be an object.'}, status=400)

    event_type = str(payload.get('event_type') or 'personal').strip()
    if event_type not in ALLOWED_EVENT_TYPES:
        return JsonResponse({'detail': f'Unsupported event_type: {event_type}'}, status=400)

    metadata_json = dict(metadata_json)
    metadata_json.pop('owner', None)
    metadata_json.pop('created_by', None)
    metadata_json['user_id'] = user.id
    if payload.get('track') is not None:
        metadata_json['track'] = payload['track']
    metadata_json['is_global'] = False
    metadata_json['is_important'] = _parse_bool(
        payload.get('is_important', metadata_json.get('is_important', False))
    )
    if payload.get('deadline_at'):
        deadline_at = _parse_patch_datetime(payload['deadline_at'])
        if deadline_at is None:
            return JsonResponse({'detail': 'Invalid datetime format: deadline_at'}, status=400)
        metadata_json['deadline_at'] = timezone.localtime(deadline_at).isoformat()

    event = ScheduleEvent.objects.create(
        title=title,
        description=payload.get('description', ''),
        start_at=start_at,
        end_at=end_at,
        is_all_day=payload.get('is_all_day', False),
        event_type=event_type,
        source_type='manual',
        metadata_json=metadata_json,
        owner=user,
    )
    _ingest_schedule_event_to_rag(event)
    return JsonResponse(_serialize_event(event), status=201)


@csrf_exempt
@require_http_methods(['PATCH', 'DELETE'])
def event_detail(request, event_id):
    user = _authenticate_request(request)
    if user is None:
        return JsonResponse({'detail': 'Authentication credentials were invalid.'}, status=401)
    if not getattr(user, 'is_authenticated', False):
        return JsonResponse({'detail': 'Authentication credentials were not provided.'}, status=401)
    try:
        event = ScheduleEvent.objects.get(pk=event_id)
    except ScheduleEvent.DoesNotExist:
        return JsonResponse({'detail': 'Schedule event not found.'}, status=404)

    permission_error = _event_write_permission_error(event, user)
    if permission_error:
        return permission_error

    if request.method == 'DELETE':
        return _delete_event(event, user)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'detail': 'Invalid JSON payload.'}, status=400)

    invalid_fields = set(payload) - {'title', 'description', 'start_at', 'end_at', 'is_all_day', 'event_type', 'is_important', 'metadata', 'metadata_json'}
    if invalid_fields:
        return JsonResponse(
            {'detail': f'Unsupported fields: {", ".join(sorted(invalid_fields))}'},
            status=400,
        )

    parsed_datetimes = {}
    for field in ['start_at', 'end_at']:
        if field not in payload:
            continue
        parsed_value = _parse_patch_datetime(payload[field])
        if parsed_value is None:
            return JsonResponse({'detail': f'Invalid datetime format: {field}'}, status=400)
        parsed_datetimes[field] = parsed_value

    if 'event_type' in payload and payload['event_type'] not in ALLOWED_EVENT_TYPES:
        return JsonResponse({'detail': f'Unsupported event_type: {payload["event_type"]}'}, status=400)

    effective_start_at = parsed_datetimes.get('start_at', event.start_at)
    effective_end_at = parsed_datetimes.get('end_at', event.end_at)
    if effective_end_at < effective_start_at:
        return JsonResponse({'detail': 'End time must be after start time.'}, status=400)

    for field in ['title', 'description', 'is_all_day', 'event_type']:
        if field in payload:
            setattr(event, field, payload[field])
    if 'metadata_json' in payload or 'metadata' in payload or 'is_important' in payload:
        metadata_json = payload.get('metadata_json', payload.get('metadata', event.metadata_json or {}))
        if metadata_json is None:
            metadata_json = {}
        if not isinstance(metadata_json, dict):
            return JsonResponse({'detail': 'metadata_json must be an object.'}, status=400)
        metadata_json = dict(metadata_json)
        if 'is_important' in payload:
            metadata_json['is_important'] = _parse_bool(payload.get('is_important'))
        event.metadata_json = metadata_json
        payload['metadata_json'] = metadata_json
        payload.pop('metadata', None)
        payload.pop('is_important', None)
    for field, value in parsed_datetimes.items():
        setattr(event, field, value)
    event.save(update_fields=[*payload.keys(), 'updated_at'])
    _ingest_schedule_event_to_rag(event)
    return JsonResponse(_serialize_event(event))


def _delete_event(event, user):
    event.delete()
    return JsonResponse({'detail': 'Schedule event deleted.'})


def _ingest_schedule_event_to_rag(event):
    if event.raw_data_id or event.owner_id:
        return
    try:
        from apps.ai.calendar_ingestion import ScheduleEventRagIngestionService

        ScheduleEventRagIngestionService().ingest_event(event, ingest_vectors=True)
    except Exception as exc:
        logger.warning('schedule_event_rag_ingestion_failed event_id=%s error=%s', event.id, exc)
        return


def _authenticate_request(request):
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


def _event_write_permission_error(event, user):
    if event.owner_id:
        if event.owner_id == user.id:
            return None
        return JsonResponse({'detail': 'You do not have permission to modify this schedule event.'}, status=403)
    if getattr(user, 'is_staff', False):
        return None
    return JsonResponse({'detail': 'Public schedule events require admin permission.'}, status=403)

def _parse_boundary(value, is_end):
    if not value:
        return None

    parsed_date = parse_date(value)
    if parsed_date and 'T' not in value and ' ' not in value:
        boundary_time = time.max if is_end else time.min
        boundary = datetime.combine(parsed_date, boundary_time)
        return timezone.make_aware(boundary, timezone.get_current_timezone())

    parsed_datetime = parse_datetime(value)
    if parsed_datetime:
        if timezone.is_naive(parsed_datetime):
            return timezone.make_aware(parsed_datetime, timezone.get_current_timezone())
        return parsed_datetime

    if not parsed_date:
        return None

    boundary_time = time.max if is_end else time.min
    boundary = datetime.combine(parsed_date, boundary_time)
    return timezone.make_aware(boundary, timezone.get_current_timezone())


def _parse_patch_datetime(value):
    if not isinstance(value, str):
        return None

    parsed_datetime = parse_datetime(value)
    if not parsed_datetime:
        for datetime_format in ['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S']:
            try:
                parsed_datetime = datetime.strptime(value, datetime_format)
                break
            except ValueError:
                continue
    if not parsed_datetime:
        return None
    if timezone.is_naive(parsed_datetime):
        return timezone.make_aware(parsed_datetime, timezone.get_current_timezone())
    return parsed_datetime


def _parse_metadata_datetime(value):
    if not isinstance(value, str) or not value.strip():
        return None
    parsed_datetime = _parse_patch_datetime(value)
    if parsed_datetime:
        return parsed_datetime
    parsed_date = parse_date(value)
    if not parsed_date:
        return None
    return timezone.make_aware(datetime.combine(parsed_date, time.min), timezone.get_current_timezone())


def _parse_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'y', 'on'}
    return bool(value)


def _event_occurs_in_range(event, range_start, range_end):
    if range_start is None and range_end is None:
        return True

    event_start = event.start_at
    event_end = event.end_at
    if event_start and event_end:
        starts_before_range_end = range_end is None or event_start <= range_end
        ends_after_range_start = range_start is None or event_end > range_start
        if starts_before_range_end and ends_after_range_start:
            return True

    deadline_at = _event_deadline_at(event.metadata_json or {})
    if deadline_at is None:
        return False
    if range_start is not None and deadline_at < range_start:
        return False
    if range_end is not None and deadline_at > range_end:
        return False
    return True


def _serialize_event(event):
    start_at = timezone.localtime(event.start_at)
    end_at = timezone.localtime(event.end_at)
    metadata = event.metadata_json or {}
    raw_data = _event_raw_data(event, metadata)
    raw_data_id = raw_data.id if raw_data else (metadata.get('raw_data_id') or None)
    source_title = raw_data.title if raw_data else metadata.get('source_title')
    source_url = raw_data.source_url if raw_data else metadata.get('source_url')
    deadline_at = _event_deadline_at(metadata)
    is_common = _is_common_event(event, metadata)
    track_key = COMMON_TRACK_KEY if is_common else _event_track(metadata, metadata.get('audience') or {})
    return {
        'id': event.id,
        'title': event.title,
        'display_title': _event_display_title(event, metadata),
        'description': event.description,
        'display_memo': _event_display_memo(event, metadata, raw_data),
        'user_friendly_description': _event_display_memo(event, metadata, raw_data),
        'deadline_at': timezone.localtime(deadline_at).isoformat() if deadline_at else None,
        'start_at': start_at.isoformat(),
        'end_at': end_at.isoformat(),
        'is_all_day': event.is_all_day,
        'is_important': _is_important_event(event),
        'event_type': event.event_type,
        'source_type': event.source_type,
        'raw_data_id': raw_data_id,
        'metadata': metadata,
        'metadata_json': metadata,
        'audience': metadata.get('audience', {}),
        'source_url': source_url,
        'source_title': source_title,
        'track': track_key,
        'track_key': track_key,
        'is_common': is_common,
        'is_global': _is_global_event(event, metadata),
        'is_generated': _is_generated_event(event, metadata, raw_data_id),
        'owner_id': event.owner_id,
        'created_by_id': event.owner_id,
    }


def _event_raw_data(event, metadata):
    if event.raw_data_id:
        return event.raw_data
    metadata_raw_data_id = metadata.get('raw_data_id')
    if not metadata_raw_data_id:
        return None
    try:
        return RawSsafyData.objects.filter(pk=metadata_raw_data_id).first()
    except (TypeError, ValueError):
        return None


def _event_display_title(event, metadata):
    metadata_display_title = str(metadata.get('display_title') or '').strip()
    if metadata_display_title:
        return metadata_display_title
    return normalize_schedule_display_title(event.title) or event.title


def _event_display_memo(event, metadata, raw_data=None):
    memo = str(metadata.get('display_memo') or metadata.get('user_friendly_description') or '').strip()
    if memo:
        return memo
    if event.description:
        return event.description
    if raw_data is not None or event.raw_data_id or metadata.get('raw_data_id'):
        return 'SSAFY notice schedule'
    if event.owner_id:
        return ''
    if _event_deadline_at(metadata):
        return 'Deadline schedule'
    return ''


def _event_list_sort_key(event):
    return (timezone.localdate(event.start_at), not _is_important_event(event), event.start_at, event.id)


def _is_important_event(event):
    return bool((event.metadata_json or {}).get('is_important', False))


def _event_deadline_at(metadata):
    return _parse_metadata_datetime(
        metadata.get('deadline_at')
        or metadata.get('deadline')
        or metadata.get('due_at')
        or metadata.get('due_date')
    )


def _is_global_event(event, metadata):
    if metadata.get('is_global') is not None:
        return bool(metadata.get('is_global'))
    return event.owner_id is None


def _is_generated_event(event, metadata, raw_data_id=None):
    if metadata.get('is_generated') is not None:
        return bool(metadata.get('is_generated'))
    return bool(raw_data_id or event.raw_data_id or event.event_type == 'generated' or event.source_type in {'notice', 'ssafy', 'learning', 'evaluation'})


def _filter_events_by_audience_params(events, params, user=None):
    filters = {
        key: params.get(key)
        for key in ['track', 'generation', 'campus', 'class_number']
        if params.get(key)
    }
    if 'track' not in filters:
        default_track = _default_user_track(user)
        if default_track:
            filters['track'] = default_track
    elif _is_common_track(filters.get('track')):
        filters.pop('track', None)

    if not filters:
        return events

    filtered = []
    for event in events:
        audience = (event.metadata_json or {}).get('audience') or {}
        if _matches_audience_filters(event, event.metadata_json or {}, audience, filters):
            filtered.append(event)
    return filtered


def _default_user_track(user):
    if not getattr(user, 'is_authenticated', False):
        return ''
    profile = getattr(user, 'profile', None) or getattr(user, 'userprofile', None)
    raw_track = getattr(profile, 'track', None) if profile is not None else None
    raw_track = raw_track or getattr(user, 'track', '')
    normalized = _normalize_track(raw_track)
    if normalized in CANONICAL_TRACKS:
        return normalized
    return ''


def _filter_queryset_by_event_type(events, event_type):
    normalized_event_type = str(event_type or '').strip()
    if normalized_event_type == 'other':
        return events.filter(event_type__in=OTHER_EVENT_TYPES)
    return events.filter(event_type=normalized_event_type)


def _matches_audience_filters(event, metadata, audience, filters):
    for key, expected in filters.items():
        if key == 'track':
            if _matches_track_filter(event, metadata, audience, expected):
                continue
            return False

        actual = audience.get(key, metadata.get(key))
        if _is_blank(actual):
            continue
        if actual is None or str(actual).lower() != str(expected).lower():
            return False
    return True


def _matches_track_filter(event, metadata, audience, expected):
    expected_track = _normalize_track(expected)
    if expected_track not in CANONICAL_TRACKS:
        return False

    actual_track = _event_track(metadata, audience)
    if _is_common_event(event, metadata):
        return True
    return _normalize_track(actual_track) == expected_track


def _event_track(metadata, audience):
    return normalize_track_key(
        metadata.get('track_key')
        or audience.get('track_key')
        or metadata.get('track')
        or audience.get('track')
    )


def _is_common_event(event, metadata):
    audience = metadata.get('audience') or {}
    track = _event_track(metadata, audience)
    if metadata.get('is_common') is True:
        return True
    if _is_common_track(track):
        return True
    title = str(getattr(event, 'title', '') or metadata.get('title') or metadata.get('source_title') or '')
    return any(keyword in title for keyword in COMMON_TITLE_KEYWORDS)


def _is_common_track(value):
    if _is_blank(value):
        return True
    return _normalize_track(value) in COMMON_TRACK_VALUES or _normalize_track(value) == COMMON_TRACK_KEY


def _normalize_track(value):
    normalized = normalize_track_key(value)
    if normalized:
        return normalized
    text = str(value or '').strip()
    if not text:
        return ''
    lowered = text.lower().replace('-', '_')
    compact = lowered.replace(' ', '').replace('_', '')
    aliases = {key.lower().replace('-', '_'): track for key, track in TRACK_ALIASES.items()}
    aliases.update({key.lower().replace(' ', '').replace('_', ''): track for key, track in TRACK_ALIASES.items()})
    aliases.update({track: track for track in CANONICAL_TRACKS})
    return aliases.get(lowered, aliases.get(compact, lowered))


def _is_hidden_meaningless_event(event):
    metadata = event.metadata_json or {}
    display_title = _event_display_title(event, metadata)
    raw_title = metadata.get('raw_title') or event.title
    if not is_meaningless_schedule_title(display_title):
        return False
    source_title = metadata.get('source_title') or (event.raw_data.title if event.raw_data_id and event.raw_data else '')
    fallback_title = normalize_schedule_display_title(raw_title or source_title)
    return is_meaningless_schedule_title(fallback_title)


def _is_blank(value):
    return value is None or str(value).strip() == ''
