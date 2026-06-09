from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.risk.services import RiskService
from schedules.models import ScheduleEvent
from schedules.services import filter_events_for_user_profile
from schedules.utils import normalize_schedule_display_title
from sync.models import RawSsafyData
from sync.services.notice_normalizer import SOURCE_TYPES


UPCOMING_LIMIT = 3
NOTICE_LIMIT = 3
EVENT_SCAN_LIMIT = 50


def build_home_dashboard(user):
    now = timezone.now()
    future_events = _future_events(_upcoming_event_candidates(user, now), now)
    upcoming_events = sorted(future_events, key=lambda event: (_event_target_at(event, now), event.id))
    recent_notices = _recent_notices()

    today_count = _safe_count(lambda: _today_event_count(user, now))
    new_notice_count = _safe_count(lambda: _new_notice_count(now))
    notice_action_count = _safe_count(lambda: _unread_notice_count(user, now, fallback_count=new_notice_count))
    risk_count = _safe_count(lambda: RiskService().get_recommended_schedule_count(user=user, now=now))

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
                'value': '전체 일정 한눈에 보기',
                'target_route': '/calendar',
            },
            {
                'key': 'notices',
                'title': '공지',
                'value': _notice_action_value(notice_action_count),
                'target_route': '/notices',
                'count': notice_action_count,
            },
            {
                'key': 'risk',
                'title': '리스크 관리',
                'value': _risk_action_value(risk_count),
                'target_route': '/risk',
                'count': risk_count,
            },
        ],
        'upcoming_schedules': [_serialize_upcoming_event(event) for event in upcoming_events[:UPCOMING_LIMIT]],
        'recent_notices': [_serialize_notice(raw_data) for raw_data in recent_notices[:NOTICE_LIMIT]],
    }


def _visible_event_queryset(user):
    queryset = ScheduleEvent.objects.select_related('raw_data').only(
        'id',
        'owner_id',
        'raw_data_id',
        'title',
        'start_at',
        'end_at',
        'is_all_day',
        'event_type',
        'source_type',
        'metadata_json',
        'raw_data__source_url',
    )
    if getattr(user, 'is_authenticated', False):
        queryset = queryset.filter(Q(owner__isnull=True) | Q(owner=user))
    else:
        queryset = queryset.filter(owner__isnull=True)
    return queryset


def _upcoming_event_candidates(user, now):
    queryset = _visible_event_queryset(user).filter(start_at__gte=now).order_by('start_at', 'id')[:EVENT_SCAN_LIMIT]
    events = list(queryset)
    return _filter_events_for_profile(user, events)


def _filter_events_for_profile(user, events):
    profile = _user_profile(user)
    if profile is not None:
        events = filter_events_for_user_profile(events, profile)
    # TODO: user_profile 기반 generation/campus/class/track 필터 적용
    return events


def _future_events(events, now):
    return [event for event in events if _event_target_at(event, now) >= now]


def _today_event_count(user, now):
    today = timezone.localdate(now)
    start_at = timezone.make_aware(datetime.combine(today, time.min), timezone.get_current_timezone())
    end_at = timezone.make_aware(datetime.combine(today, time.max), timezone.get_current_timezone())
    queryset = _visible_event_queryset(user).filter(start_at__lte=end_at, end_at__gte=start_at)
    if _user_profile(user) is None:
        return queryset.count()
    return sum(1 for event in _filter_events_for_profile(user, list(queryset)) if _event_overlaps_today(event, start_at, end_at))


def _new_notice_count(now):
    since = now - timedelta(days=7)
    return RawSsafyData.objects.filter(source_type__in=SOURCE_TYPES, collected_at__gte=since).count()


def _unread_notice_count(user, now, fallback_count=None):
    # TODO: 사용자별 공지 읽음 상태 모델이 추가되면 unread notice count 기준으로 변경
    return _new_notice_count(now) if fallback_count is None else fallback_count


def _recent_notices():
    try:
        return list(
            RawSsafyData.objects.filter(source_type__in=SOURCE_TYPES)
            .only('id', 'title', 'source_type', 'source_url', 'metadata_json', 'collected_at')
            .order_by('-collected_at', '-id')[:NOTICE_LIMIT]
        )
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
    deadline_at = _event_deadline_at(event)
    local_deadline_at = timezone.localtime(deadline_at) if deadline_at else None
    return {
        'id': event.id,
        'day': str(start_at.day),
        'month': f'{start_at.month}월',
        'title': _event_title(event),
        'time': _event_time_label(event, start_at, end_at),
        'start_at': start_at.isoformat(),
        'end_at': end_at.isoformat(),
        'deadline_at': local_deadline_at.isoformat() if local_deadline_at else None,
        'event_type': event.event_type,
    }


def _serialize_notice(raw_data):
    collected_at = timezone.localtime(raw_data.collected_at)
    return {
        'id': raw_data.id,
        'title': raw_data.title,
        'date': collected_at.strftime('%Y.%m.%d'),
        'source_type': raw_data.source_type,
        'source_url': _notice_source_url(raw_data),
    }


def _event_target_at(event, now):
    deadline_at = _event_deadline_at(event)
    return deadline_at or event.start_at or now


def _event_deadline_at(event):
    metadata = event.metadata_json or {}
    return _parse_metadata_datetime(metadata.get('deadline_at'))


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
    deadline_at = _event_deadline_at(event)
    suffix = '까지' if deadline_at else ''
    return f'{target_at:%H:%M}{suffix}'


def _event_time_label(event, start_at, end_at):
    if event.is_all_day:
        return '종일'
    return f'{start_at:%H:%M} - {end_at:%H:%M}'


def _event_overlaps_today(event, today_start, today_end):
    start_at = getattr(event, 'start_at', None)
    end_at = getattr(event, 'end_at', None)
    if start_at is None:
        return False
    if end_at is None:
        return today_start <= start_at <= today_end
    return start_at <= today_end and end_at >= today_start


def _notice_action_value(count):
    if count:
        return f'확인 안 한 공지 {count}개'
    return '새 공지 없음'


def _risk_action_value(count):
    if count:
        return f'오늘 확인할 추천 {count}개'
    return '추천 항목 없음'


def _notice_source_url(raw_data):
    candidates = [
        getattr(raw_data, 'source_url', ''),
        _metadata_url(raw_data.metadata_json, 'source_url'),
        _metadata_url(raw_data.metadata_json, 'url'),
        _metadata_url(raw_data.metadata_json, 'link'),
        _metadata_url(raw_data.metadata_json, 'href'),
        _metadata_url((raw_data.metadata_json or {}).get('raw_json'), 'source_url'),
        _metadata_url((raw_data.metadata_json or {}).get('raw_json'), 'url'),
        _metadata_url((raw_data.metadata_json or {}).get('raw_json'), 'link'),
        _metadata_url((raw_data.metadata_json or {}).get('raw_json'), 'href'),
        _metadata_url((raw_data.metadata_json or {}).get('metadata_json'), 'source_url'),
        _metadata_url((raw_data.metadata_json or {}).get('metadata_json'), 'url'),
        _metadata_url((raw_data.metadata_json or {}).get('metadata_json'), 'link'),
    ]
    for candidate in candidates:
        value = str(candidate or '').strip()
        if value:
            return value
    return None


def _metadata_url(metadata, key):
    if not isinstance(metadata, dict):
        return ''
    return metadata.get(key) or ''


def _safe_count(counter):
    try:
        return counter()
    except Exception:
        return 0


def _user_profile(user):
    if not getattr(user, 'is_authenticated', False):
        return None
    return getattr(user, 'profile', None) or getattr(user, 'userprofile', None) or user
