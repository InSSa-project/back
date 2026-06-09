from datetime import datetime, time

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from schedules.models import ScheduleEvent
from schedules.services import filter_events_for_user_profile
from sync.services.tracks import COMMON_TRACK_KEY, normalize_track_key


COMMON_TRACK_VALUES = {'', 'common', 'all', 'global', '전체', '공통'}


class CalendarService:
    def list_events(self, user, start=None, end=None, track=None, event_type=None):
        queryset = ScheduleEvent.objects.select_related('raw_data').all()
        if getattr(user, 'is_authenticated', False):
            queryset = queryset.filter(Q(owner__isnull=True) | Q(owner=user))
        else:
            queryset = queryset.filter(owner__isnull=True)

        start_at = _parse_boundary(start, is_end=False)
        end_at = _parse_boundary(end, is_end=True)
        if start_at:
            queryset = queryset.filter(end_at__gte=start_at)
        if end_at:
            queryset = queryset.filter(start_at__lte=end_at)
        if event_type:
            queryset = queryset.filter(event_type=str(event_type).strip())

        events = list(queryset.order_by('start_at', 'id'))
        profile = _user_profile(user)
        if profile is not None:
            events = filter_events_for_user_profile(events, profile)
        if track:
            events = [event for event in events if _matches_track(event, track)]
        return events


def _parse_boundary(value, is_end):
    if not value:
        return None

    parsed_datetime = parse_datetime(str(value))
    if parsed_datetime:
        if timezone.is_naive(parsed_datetime):
            return timezone.make_aware(parsed_datetime, timezone.get_current_timezone())
        return parsed_datetime

    parsed_date = parse_date(str(value))
    if not parsed_date:
        return None

    boundary_time = time.max if is_end else time.min
    return timezone.make_aware(datetime.combine(parsed_date, boundary_time), timezone.get_current_timezone())


def _user_profile(user):
    if not getattr(user, 'is_authenticated', False):
        return None
    return getattr(user, 'profile', None) or getattr(user, 'userprofile', None) or user


def _matches_track(event, expected):
    expected_track = normalize_track_key(expected)
    metadata = event.metadata_json or {}
    audience = metadata.get('audience') or {}
    event_track = normalize_track_key(
        metadata.get('track_key')
        or audience.get('track_key')
        or metadata.get('track')
        or audience.get('track')
    )
    if _is_common_event(metadata, event_track):
        return True
    return bool(expected_track) and event_track == expected_track


def _is_common_event(metadata, event_track):
    if metadata.get('is_common') is True or metadata.get('is_global') is True:
        return True
    return event_track == COMMON_TRACK_KEY or event_track in COMMON_TRACK_VALUES
