from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from schedules.models import ScheduleEvent
from schedules.services import filter_events_for_user_profile
from schedules.utils import normalize_schedule_display_title
from sync.models import RawSsafyData
from sync.services.notice_normalizer import SOURCE_TYPES


UPCOMING_LIMIT = 3
NOTICE_LIMIT = 3


def build_home_dashboard(user):
    now = timezone.now()
    visible_events = _visible_events(user)
    future_events = _future_events(visible_events, now)
    upcoming_events = sorted(future_events, key=lambda event: (_event_target_at(event, now), event.id))
    recent_notices = _recent_notices()

    today_count = _safe_count(lambda: _today_event_count(visible_events, now))
    new_notice_count = _safe_count(lambda: _new_notice_count(now))

    return {
        'focus': _serialize_focus(upcoming_events[0], now) if upcoming_events else None,
        'highlights': [
            {'label': '오늘 일정', 'value': f'{today_count}개'},
            {'label': '새 공지', 'value': f'{new_notice_count}개'},
        ],
        'action_cards': [
            {
                'key': 'calendar',
                'title': '캘린더',
                'value': '가까운 일정부터 보기',
                'target_route': '/calendar',
            },
            {
                'key': 'notices',
                'title': '공지',
                'value': '새로 확인할 공지 보기',
                'target_route': '/notices',
            },
            {
                'key': 'risk',
                'title': '리스크 관리',
                'value': _risk_summary(upcoming_events),
                'target_route': '/risk',
            },
        ],
        'upcoming_schedules': [_serialize_upcoming_event(event) for event in upcoming_events[:UPCOMING_LIMIT]],
        'recent_notices': [_serialize_notice(raw_data) for raw_data in recent_notices[:NOTICE_LIMIT]],
    }


def _visible_events(user):
    queryset = ScheduleEvent.objects.select_related('raw_data').all()
    if getattr(user, 'is_authenticated', False):
        queryset = queryset.filter(Q(owner__isnull=True) | Q(owner=user))
    else:
        queryset = queryset.filter(owner__isnull=True)

    events = list(queryset)
    profile = _user_profile(user)
    if profile is not None:
        events = filter_events_for_user_profile(events, profile)
    # TODO: generation/campus/class/track filtering depends on completed profile metadata coverage.
    return events


def _future_events(events, now):
    return [event for event in events if _event_target_at(event, now) >= now]


def _today_event_count(events, now):
    today = timezone.localdate(now)
    start_at = timezone.make_aware(datetime.combine(today, time.min), timezone.get_current_timezone())
    end_at = timezone.make_aware(datetime.combine(today, time.max), timezone.get_current_timezone())
    return sum(1 for event in events if event.start_at <= end_at and event.end_at >= start_at)


def _new_notice_count(now):
    since = now - timedelta(days=7)
    return RawSsafyData.objects.filter(source_type__in=SOURCE_TYPES, collected_at__gte=since).count()


def _recent_notices():
    try:
        return list(RawSsafyData.objects.filter(source_type__in=SOURCE_TYPES).order_by('-collected_at', '-id')[:NOTICE_LIMIT])
    except Exception:
        return []


def _serialize_focus(event, now):
    target_at = timezone.localtime(_event_target_at(event, now))
    days_left = (target_at.date() - timezone.localdate(now)).days
    remaining = 'D-Day' if days_left == 0 else f'D-{days_left}'
    return {
        'remaining': remaining,
        'title': _event_title(event),
        'date': target_at.strftime('%Y.%m.%d'),
        'time': _focus_time_label(event, target_at),
        'target_route': '/calendar',
        'source_event_id': event.id,
    }


def _serialize_upcoming_event(event):
    start_at = timezone.localtime(event.start_at)
    end_at = timezone.localtime(event.end_at)
    return {
        'id': event.id,
        'day': str(start_at.day),
        'month': f'{start_at.month}월',
        'title': _event_title(event),
        'time': _event_time_label(event, start_at, end_at),
        'start_at': start_at.isoformat(),
        'end_at': end_at.isoformat(),
        'event_type': event.event_type,
    }


def _serialize_notice(raw_data):
    collected_at = timezone.localtime(raw_data.collected_at)
    return {
        'id': raw_data.id,
        'title': raw_data.title,
        'date': collected_at.strftime('%Y.%m.%d'),
        'source_type': raw_data.source_type,
    }


def _event_target_at(event, now):
    metadata = event.metadata_json or {}
    deadline_at = _parse_metadata_datetime(metadata.get('deadline_at'))
    return deadline_at or event.start_at or now


def _parse_metadata_datetime(value):
    if not value:
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _event_title(event):
    metadata = event.metadata_json or {}
    display_title = str(metadata.get('display_title') or '').strip()
    if display_title:
        return display_title
    return normalize_schedule_display_title(event.title) or event.title


def _focus_time_label(event, target_at):
    deadline_at = _parse_metadata_datetime((event.metadata_json or {}).get('deadline_at'))
    suffix = '까지' if deadline_at else ''
    return f'{target_at:%H:%M}{suffix}'


def _event_time_label(event, start_at, end_at):
    if event.is_all_day:
        return '종일'
    return f'{start_at:%H:%M} - {end_at:%H:%M}'


def _risk_summary(upcoming_events):
    now = timezone.now()
    urgent_count = sum(
        1
        for event in upcoming_events
        if (_event_target_at(event, now) - now) <= timedelta(days=3)
    )
    if urgent_count:
        return f'3일 안에 {urgent_count}개 일정 확인'
    return '놓치기 쉬운 일정 확인'


def _safe_count(counter):
    try:
        return counter()
    except Exception:
        return 0


def _user_profile(user):
    if not getattr(user, 'is_authenticated', False):
        return None
    return getattr(user, 'profile', None) or getattr(user, 'userprofile', None) or user
