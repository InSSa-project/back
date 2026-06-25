from datetime import datetime, time

from django.db.models import Case, IntegerField, Prefetch, Q, When
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from schedules.models import ScheduleEvent
from schedules.services import filter_calendar_visible_events
from sync.services.tracks import COMMON_TRACK_KEY, canonical_track_keys, normalize_track_key

from .models import UserScheduleEvent


COMMON_TRACK_VALUES = {'', 'common', 'all', 'global', COMMON_TRACK_KEY, 'all_tracks'}
ALL_TRACK_VALUES = {'all', 'all_tracks', COMMON_TRACK_KEY}


class ProfileIncompleteError(Exception):
    code = 'PROFILE_INCOMPLETE'
    message = 'User profile track is required to read calendar events.'


class CalendarService:
    def list_events(self, user, start=None, end=None, track=None, event_type=None):
        start_at = _parse_boundary(start, is_end=False)
        end_at = _parse_boundary(end, is_end=True)

        queryset = ScheduleEvent.objects.select_related('raw_data').all()
        if getattr(user, 'is_authenticated', False):
            queryset = queryset.prefetch_related(
                Prefetch(
                    'user_schedule_events',
                    queryset=UserScheduleEvent.objects.filter(user=user),
                    to_attr='_calendar_user_links',
                )
            )
        queryset = _with_calendar_ordering(queryset)

        if getattr(user, 'is_authenticated', False):
            queryset = queryset.filter(
                Q(owner__isnull=True) | Q(owner=user) | Q(user_schedule_events__user=user)
            ).distinct()
            queryset = queryset.exclude(user_schedule_events__user=user, user_schedule_events__is_hidden=True)
        else:
            queryset = queryset.filter(owner__isnull=True)

        queryset = queryset.filter(_range_query(start_at, end_at, user))
        queryset = queryset.filter(_track_scope_query(user, track))
        if event_type:
            queryset = queryset.filter(_event_type_query(str(event_type).strip(), user))
        queryset = queryset.distinct()

        events = filter_calendar_visible_events(queryset)
        events = self._filter_events_by_calendar_scope(events, user, track)
        events = [event for event in events if not is_event_hidden_for_user(event, user)]
        return [
            event
            for event in events
            if _effective_event_occurs_in_range(event, user, start_at, end_at)
            and _effective_event_type_matches(event, user, event_type)
        ]

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


def _range_query(range_start, range_end, user):
    base = Q()
    if range_end is not None:
        base &= Q(start_at__lte=range_end)
    if range_start is not None:
        base &= Q(end_at__gte=range_start)

    override = Q()
    if getattr(user, 'is_authenticated', False) and (range_start is not None or range_end is not None):
        override = Q(user_schedule_events__user=user)
        if range_end is not None:
            override &= Q(user_schedule_events__override_start_at__lte=range_end)
        if range_start is not None:
            override &= Q(user_schedule_events__override_end_at__gte=range_start)

    if override:
        return base | override
    return base


def _event_type_query(event_type, user):
    query = Q(event_type=event_type)
    if getattr(user, 'is_authenticated', False):
        query |= Q(user_schedule_events__user=user, user_schedule_events__override_event_type=event_type)
    return query


def _track_scope_query(user, requested_track):
    if _can_manage_calendar(user):
        admin_track = normalize_track_key(requested_track)
        if not admin_track or admin_track in ALL_TRACK_VALUES:
            return Q()
        if admin_track not in canonical_track_keys():
            return Q(pk__in=[])
        return _metadata_track_query(admin_track, user)

    user_track = _user_profile_track(user)
    if not user_track:
        raise ProfileIncompleteError()
    return _metadata_track_query(user_track, user)


def _metadata_track_query(track_key, user):
    common_query = (
        Q(metadata_json__is_common=True)
        | Q(metadata_json__track_key__in=list(COMMON_TRACK_VALUES))
        | Q(metadata_json__track__in=list(COMMON_TRACK_VALUES))
        | Q(metadata_json__audience__track_key__in=list(COMMON_TRACK_VALUES))
        | Q(metadata_json__audience__track__in=list(COMMON_TRACK_VALUES))
        | Q(metadata_json={})
        | (
            Q(metadata_json__track_key__isnull=True)
            & Q(metadata_json__track__isnull=True)
            & Q(metadata_json__audience__track_key__isnull=True)
            & Q(metadata_json__audience__track__isnull=True)
        )
    )
    track_query = (
        Q(metadata_json__track_key=track_key)
        | Q(metadata_json__track=track_key)
        | Q(metadata_json__audience__track_key=track_key)
        | Q(metadata_json__audience__track=track_key)
    )
    owned_or_linked = Q()
    if getattr(user, 'is_authenticated', False):
        owned_or_linked = Q(owner=user) | Q(user_schedule_events__user=user)
    return common_query | track_query | owned_or_linked


def _with_calendar_ordering(queryset):
    return queryset.annotate(
        _event_type_priority=Case(
            When(event_type__in=['exam', 'monthly_test', 'subject_test', 'MONTHLY_TEST', 'SUBJECT_TEST'], then=1),
            When(event_type__in=['project', 'PROJECT'], then=2),
            When(event_type__in=['personal', 'PERSONAL'], then=3),
            default=4,
            output_field=IntegerField(),
        )
    ).order_by('start_at', 'is_all_day', '_event_type_priority', 'title', 'id')


def _matches_track(event, expected, user=None):
    if user is not None and event.owner_id == getattr(user, 'id', None):
        return True
    if user is not None and user_schedule_link(event, user) is not None:
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


def effective_event_value(event, user, field):
    link = user_schedule_link(event, user)
    override_field = {
        'title': 'override_title',
        'description': 'override_description',
        'event_type': 'override_event_type',
        'start_at': 'override_start_at',
        'end_at': 'override_end_at',
        'is_all_day': 'override_is_all_day',
    }.get(field)
    if override_field and link is not None:
        value = getattr(link, override_field)
        if value is not None:
            return value
    return getattr(event, field)


def user_schedule_link(event, user):
    if not getattr(user, 'is_authenticated', False):
        return None
    if event.owner_id == getattr(user, 'id', None):
        return None
    prefetched_links = getattr(event, '_calendar_user_links', None)
    if prefetched_links is not None:
        link = prefetched_links[0] if prefetched_links else None
        event._calendar_user_link = link
        return link
    cached = getattr(event, '_calendar_user_link', None)
    if cached is not None and getattr(cached, 'user_id', None) == getattr(user, 'id', None):
        return cached
    link = UserScheduleEvent.objects.filter(user=user, schedule_event=event).first()
    event._calendar_user_link = link
    return link


def has_user_override(event, user):
    link = user_schedule_link(event, user)
    if link is None:
        return False
    return any(
        getattr(link, field) is not None
        for field in [
            'override_title',
            'override_description',
            'override_event_type',
            'override_start_at',
            'override_end_at',
            'override_is_all_day',
        ]
    )


def is_event_hidden_for_user(event, user):
    link = user_schedule_link(event, user)
    return bool(link and link.is_hidden)


def is_holiday_event(event):
    metadata = event.metadata_json or {}
    return (
        event.event_type == 'holiday'
        or event.source_type in {'national_holiday', 'holiday'}
        or bool(metadata.get('is_public_holiday'))
    )


def _effective_event_occurs_in_range(event, user, range_start, range_end):
    if range_start is None and range_end is None:
        return True
    event_start = effective_event_value(event, user, 'start_at')
    event_end = effective_event_value(event, user, 'end_at')
    if event_start and event_end:
        starts_before_range_end = range_end is None or event_start <= range_end
        ends_after_range_start = range_start is None or event_end >= range_start
        return starts_before_range_end and ends_after_range_start
    return True


def _effective_event_type_matches(event, user, event_type):
    if not event_type:
        return True
    return effective_event_value(event, user, 'event_type') == str(event_type).strip()


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
