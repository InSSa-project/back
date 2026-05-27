from datetime import datetime, time, timedelta

import json

from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import ScheduleEvent
from .services import filter_events_for_user_profile


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
    'java비전공': 'java_non_major',
    'java_비전공': 'java_non_major',
    'java(비전공)': 'java_non_major',
    'java_major': 'java_major',
    'java전공': 'java_major',
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
    '마이스터고': 'meister',
}
COMMON_TRACK_VALUES = {'', 'common', 'all', '공통', '전체'}
COMMON_TITLE_KEYWORDS = (
    'SSAFY DAY',
    '설날',
    '온라인 위크',
    '과목평가',
    '월말평가',
    '관통 프로젝트 집중기간',
    '상반기 밋업',
)
OTHER_EVENT_TYPES = {
    'study',
    'assignment',
    'lecture',
    'deadline',
    'notice',
    'mentoring',
    'unknown',
    'other',
    '기타',
    'etc',
}
ALLOWED_EVENT_TYPES = OTHER_EVENT_TYPES | {
    'exam',
    'project',
    'personal',
    'holiday',
}


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def event_list(request):
    if request.method == 'POST':
        return _create_event(request)

    start_at = _parse_boundary(request.GET.get('start'), is_end=False)
    end_at = _parse_boundary(request.GET.get('end'), is_end=True)

    events = ScheduleEvent.objects.all()
    if start_at:
        events = events.filter(end_at__gte=start_at)
    if end_at:
        events = events.filter(start_at__lte=end_at)
    event_type = request.GET.get('event_type')
    if event_type:
        events = _filter_queryset_by_event_type(events, event_type)
    events = filter_events_for_user_profile(list(events), _user_profile(request.user))
    events = _filter_events_by_audience_params(events, request.GET)

    events = sorted(events, key=_event_list_sort_key)
    return JsonResponse([_serialize_event(event) for event in events], safe=False)


def _create_event(request):
    # TODO: Require authentication and attach created_by before production use.
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
    if getattr(request.user, 'is_authenticated', False):
        metadata_json['user_id'] = request.user.id
    if payload.get('track') is not None:
        metadata_json['track'] = payload['track']
    if payload.get('is_global') is not None:
        metadata_json['is_global'] = payload['is_global']
    metadata_json['is_important'] = bool(payload.get('is_important', metadata_json.get('is_important', False)))
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
        source_type=str(payload.get('source_type') or 'manual').strip() or 'manual',
        metadata_json=metadata_json,
    )
    _ingest_schedule_event_to_rag(event)
    return JsonResponse(_serialize_event(event), status=201)


@csrf_exempt
@require_http_methods(['PATCH'])
def event_detail(request, event_id):
    # TODO: Require authentication and per-user/admin permission before production use.
    try:
        event = ScheduleEvent.objects.get(pk=event_id)
    except ScheduleEvent.DoesNotExist:
        return JsonResponse({'detail': 'Schedule event not found.'}, status=404)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'detail': 'Invalid JSON payload.'}, status=400)

    invalid_fields = set(payload) - {'title', 'description', 'start_at', 'end_at', 'is_all_day', 'event_type'}
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

    for field in ['title', 'description', 'is_all_day', 'event_type']:
        if field in payload:
            setattr(event, field, payload[field])
    for field, value in parsed_datetimes.items():
        setattr(event, field, value)
    event.save(update_fields=[*payload.keys(), 'updated_at'])
    _ingest_schedule_event_to_rag(event)
    return JsonResponse(_serialize_event(event))


def _ingest_schedule_event_to_rag(event):
    if event.raw_data_id:
        return
    try:
        from apps.ai.calendar_ingestion import ScheduleEventRagIngestionService

        ScheduleEventRagIngestionService().ingest_event(event, ingest_vectors=True)
    except Exception:
        # Calendar writes should not fail because the AI index is unavailable.
        return


def _parse_boundary(value, is_end):
    if not value:
        return None

    parsed_datetime = parse_datetime(value)
    if parsed_datetime:
        if timezone.is_naive(parsed_datetime):
            return timezone.make_aware(parsed_datetime, timezone.get_current_timezone())
        return parsed_datetime

    parsed_date = parse_date(value)
    if not parsed_date:
        return None

    boundary_time = time.max if is_end else time.min
    boundary = datetime.combine(parsed_date, boundary_time)
    if is_end:
        boundary = boundary + timedelta(microseconds=1)
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


def _serialize_event(event):
    start_at = timezone.localtime(event.start_at)
    end_at = timezone.localtime(event.end_at)
    raw_data = event.raw_data
    return {
        'id': event.id,
        'title': event.title,
        'description': event.description,
        'start_at': start_at.isoformat(),
        'end_at': end_at.isoformat(),
        'is_all_day': event.is_all_day,
        'is_important': _is_important_event(event),
        'event_type': event.event_type,
        'source_type': event.source_type,
        'metadata': event.metadata_json,
        'metadata_json': event.metadata_json,
        'audience': (event.metadata_json or {}).get('audience', {}),
        'source_url': raw_data.source_url if raw_data else None,
        'source_title': raw_data.title if raw_data else None,
    }


def _event_list_sort_key(event):
    return (timezone.localdate(event.start_at), not _is_important_event(event), event.start_at, event.id)


def _is_important_event(event):
    return bool((event.metadata_json or {}).get('is_important', False))


def _user_profile(user):
    if not getattr(user, 'is_authenticated', False):
        return None
    return getattr(user, 'profile', None) or getattr(user, 'userprofile', None) or user


def _filter_events_by_audience_params(events, params):
    filters = {
        key: params.get(key)
        for key in ['track', 'generation', 'campus', 'class_number']
        if params.get(key)
    }
    if not filters:
        return events

    filtered = []
    for event in events:
        audience = (event.metadata_json or {}).get('audience') or {}
        if _matches_audience_filters(event, event.metadata_json or {}, audience, filters):
            filtered.append(event)
    return filtered


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
    return audience.get('track') or metadata.get('track')


def _is_common_event(event, metadata):
    audience = metadata.get('audience') or {}
    track = _event_track(metadata, audience)
    if _is_common_track(track):
        return True
    if metadata.get('is_global') is True or getattr(event, 'is_global', False) is True:
        return True
    title = str(getattr(event, 'title', '') or metadata.get('title') or metadata.get('source_title') or '')
    return any(keyword in title for keyword in COMMON_TITLE_KEYWORDS)


def _is_common_track(value):
    if _is_blank(value):
        return True
    return _normalize_track(value) in COMMON_TRACK_VALUES


def _normalize_track(value):
    text = str(value or '').strip()
    if not text:
        return ''
    lowered = text.lower().replace('-', '_')
    compact = lowered.replace(' ', '').replace('_', '')
    aliases = {key.lower().replace('-', '_'): track for key, track in TRACK_ALIASES.items()}
    aliases.update({key.lower().replace(' ', '').replace('_', ''): track for key, track in TRACK_ALIASES.items()})
    aliases.update({track: track for track in CANONICAL_TRACKS})
    return aliases.get(lowered, aliases.get(compact, lowered))


def _is_blank(value):
    return value is None or str(value).strip() == ''
