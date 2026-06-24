from datetime import datetime, time

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from schedules.models import ScheduleEvent
from schedules.services import filter_calendar_visible_events
from sync.services.tracks import COMMON_TRACK_KEY, canonical_track_keys, normalize_track_key

from .models import HiddenCalendarEvent, UserScheduleEvent


COMMON_TRACK_VALUES = {'', 'common', 'all', 'global', COMMON_TRACK_KEY, 'all_tracks'}
ALL_TRACK_VALUES = {'all', 'all_tracks', COMMON_TRACK_KEY}


class ProfileIncompleteError(Exception):
    code = 'PROFILE_INCOMPLETE'
    message = 'User profile track is required to read calendar events.'


class CalendarService:
    def list_events(self, user, start=None, end=None, track=None, event_type=None):
        queryset = ScheduleEvent.objects.select_related('raw_data').all()
        if getattr(user, 'is_authenticated', False):
            queryset = queryset.filter(
                Q(owner__isnull=True) | Q(owner=user) | Q(user_schedule_events__user=user)
            ).distinct()
            hidden_event_ids = HiddenCalendarEvent.objects.filter(user=user).values('schedule_event_id')
            queryset = queryset.exclude(id__in=hidden_event_ids)
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

        events = filter_calendar_visible_events(queryset.order_by('start_at', 'id'))
        return self._filter_events_by_calendar_scope(events, user, track)

    def _filter_events_by_calendar_scope(self, events, user, requested_track):
        if _can_manage_calendar(user):
            admin_track = normalize_track_key(requested_track)
            if not admin_track or admin_track in ALL_TRACK_VALUES:
                return events
            if admin_track not in canonical_track_keys():
                return []
            return [event for event in events if _matches_track(event, admin_track, user=user)]

        user_track = _user_profile_track(user)
        if not user_track:
            raise ProfileIncompleteError()
        return [event for event in events if _matches_track(event, user_track, user=user)]


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


def _matches_track(event, expected, user=None):
    if user is not None and event.owner_id == getattr(user, 'id', None):
        return True
    if user is not None and UserScheduleEvent.objects.filter(user=user, schedule_event=event).exists():
        return True

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
    if metadata.get('is_common') is True:
        return True
    return event_track == COMMON_TRACK_KEY or event_track in COMMON_TRACK_VALUES


def _can_manage_calendar(user):
    return bool(getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False))


def _user_profile_track(user):
    profile = getattr(user, 'profile', None)
    if profile is None:
        return ''
    return normalize_track_key(getattr(profile, 'track', None))
