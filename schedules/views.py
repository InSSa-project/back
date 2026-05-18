from datetime import datetime, time, timedelta

import json

from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from .models import ScheduleEvent
from .services import filter_events_for_user_profile


@require_GET
def event_list(request):
    start_at = _parse_boundary(request.GET.get('start'), is_end=False)
    end_at = _parse_boundary(request.GET.get('end'), is_end=True)

    events = ScheduleEvent.objects.all()
    if start_at:
        events = events.filter(end_at__gte=start_at)
    if end_at:
        events = events.filter(start_at__lte=end_at)
    event_type = request.GET.get('event_type')
    if event_type:
        events = events.filter(event_type=event_type)
    events = filter_events_for_user_profile(list(events), _user_profile(request.user))
    events = _filter_events_by_audience_params(events, request.GET)

    return JsonResponse([_serialize_event(event) for event in events], safe=False)


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
    return JsonResponse(_serialize_event(event))


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
        'event_type': event.event_type,
        'source_type': event.source_type,
        'metadata': event.metadata_json,
        'metadata_json': event.metadata_json,
        'audience': (event.metadata_json or {}).get('audience', {}),
        'source_url': raw_data.source_url if raw_data else None,
        'source_title': raw_data.title if raw_data else None,
    }


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
        if _matches_audience_filters(event.metadata_json or {}, audience, filters):
            filtered.append(event)
    return filtered


def _matches_audience_filters(metadata, audience, filters):
    for key, expected in filters.items():
        actual = audience.get(key, metadata.get(key))
        if key == 'track' and _is_common_track(actual):
            continue
        if actual is None or str(actual).lower() != str(expected).lower():
            return False
    return True


def _is_common_track(value):
    if value is None or value == '':
        return True
    return str(value).lower() == 'common'
