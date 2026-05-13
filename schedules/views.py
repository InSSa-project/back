from datetime import datetime, time, timedelta

from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_GET

from .models import ScheduleEvent


@require_GET
def event_list(request):
    start_at = _parse_boundary(request.GET.get('start'), is_end=False)
    end_at = _parse_boundary(request.GET.get('end'), is_end=True)

    events = ScheduleEvent.objects.all()
    if start_at:
        events = events.filter(end_at__gte=start_at)
    if end_at:
        events = events.filter(start_at__lte=end_at)

    return JsonResponse([_serialize_event(event) for event in events], safe=False)


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


def _serialize_event(event):
    start_at = timezone.localtime(event.start_at)
    end_at = timezone.localtime(event.end_at)
    return {
        'id': event.id,
        'title': event.title,
        'description': event.description,
        'start_at': start_at.isoformat(),
        'end_at': end_at.isoformat(),
        'is_all_day': event.is_all_day,
        'event_type': event.event_type,
        'source_type': event.source_type,
    }
