import os
from datetime import datetime, time, timedelta
from urllib.parse import urljoin, urlparse

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.risk.services import RiskService
from schedules.models import ScheduleEvent
from schedules.services import filter_events_for_user_profile
from schedules.utils import normalize_schedule_display_title
from sync.models import RawSsafyData
from sync.services.notice_policy import (
    notice_publication_date,
    notice_publication_values,
    notice_sort_key,
    notice_source_url,
    notice_title,
    user_visible_notice_queryset,
)


UPCOMING_LIMIT = 3
NOTICE_LIMIT = 3
EVENT_SCAN_LIMIT = 50
GENERIC_NOTICE_TITLES = {
    '게시물 목록',
    '게시물 상세',
    '공지사항 상세',
    '멘토 스토리 상세',
    '상세',
    '목록',
    'SSAFY',
}
NOTICE_TITLE_KEYS = (
    'title',
    'subject',
    'notice_title',
    'article_title',
    'board_title',
    'original_title',
    'list_title',
)
NOTICE_URL_KEYS = (
    'source_url',
    'detail_url',
    'original_url',
    'url',
    'link',
    'href',
)
RAW_TEXT_TITLE_STOPWORDS = {
    '멘토 스토리',
    '멘토칼럼',
    '공지사항',
    '학사규정',
    'FAQ',
    '1:1 문의',
    '조회',
    '좋아요수',
    '댓글',
}


def build_home_dashboard(user):
    now = timezone.now()
    week_start, week_end = _week_bounds(now)
    future_events = _future_events(_upcoming_event_candidates(user, now, week_start, week_end), now)
    upcoming_events = sorted(future_events, key=lambda event: (_event_target_at(event, now), event.id))
    recent_notices = _recent_notices()

    today_count = _safe_count(lambda: _today_event_count(user, now))
    new_notice_count = _safe_count(lambda: _new_notice_count(now))
    notice_action_count = _safe_count(lambda: _unread_notice_count(user, now, fallback_count=new_notice_count))
    risk_count = _safe_count(lambda: RiskService().get_recommended_schedule_count(user=user, now=now))

    return {
        'focus': _serialize_focus(upcoming_events[0], now) if upcoming_events else None,
        'new_notice_count': new_notice_count,
        'new_notice_count_label': 'recent_7_days_notice_date_or_collected_at',
        'unread_new_notice_count': notice_action_count,
        'unread_notice_count_basis': 'recent_7_days',
        'period_label': 'this_week',
        'week_start': timezone.localtime(week_start).isoformat(),
        'week_end': timezone.localtime(week_end).isoformat(),
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
        'week_schedules': [_serialize_upcoming_event(event) for event in upcoming_events[:UPCOMING_LIMIT]],
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


def _upcoming_event_candidates(user, now, week_start, week_end):
    queryset = (
        _visible_event_queryset(user)
        .filter(start_at__lte=week_end, end_at__gte=week_start)
        .order_by('start_at', 'id')[:EVENT_SCAN_LIMIT]
    )
    events = list(queryset)
    events = _filter_events_for_profile(user, events)
    return [event for event in events if _event_occurs_in_range(event, week_start, week_end)]


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
    since = timezone.localdate(now) - timedelta(days=7)
    today = timezone.localdate(now)
    rows = user_visible_notice_queryset(RawSsafyData.objects.all()).only('id', 'source_type', 'metadata_json')
    return sum(1 for row in rows if _notice_date_in_range(row, since, today))


def _unread_notice_count(user, now, fallback_count=None):
    # TODO: 사용자별 공지 읽음 상태 모델이 추가되면 unread notice count 기준으로 변경
    return _new_notice_count(now) if fallback_count is None else fallback_count


def _recent_notices():
    try:
        rows = list(
            user_visible_notice_queryset(RawSsafyData.objects.all())
            .only('id', 'title', 'source_type', 'source_url', 'metadata_json', 'raw_text', 'collected_at')
            .order_by('-collected_at', '-id')[:50]
        )
        return sorted(rows, key=notice_sort_key)[:NOTICE_LIMIT]
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
    published_at, notice_date = notice_publication_values(raw_data)
    return {
        'id': raw_data.id,
        'title': notice_title(raw_data),
        'date': notice_date.strftime('%Y.%m.%d') if notice_date else collected_at.strftime('%Y.%m.%d'),
        'published_at': published_at.isoformat() if published_at else None,
        'notice_date': notice_date.isoformat() if notice_date else None,
        'source_type': raw_data.source_type,
        'source_url': notice_source_url(raw_data),
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


def _event_occurs_in_range(event, range_start, range_end):
    if event.start_at and event.end_at and event.start_at <= range_end and event.end_at >= range_start:
        return True
    deadline_at = _event_deadline_at(event)
    return bool(deadline_at and range_start <= deadline_at <= range_end)


def _week_bounds(now):
    local_now = timezone.localtime(now)
    week_start_date = local_now.date() - timedelta(days=local_now.weekday())
    week_end_date = week_start_date + timedelta(days=6)
    current_tz = timezone.get_current_timezone()
    week_start = timezone.make_aware(datetime.combine(week_start_date, time.min), current_tz)
    week_end = timezone.make_aware(datetime.combine(week_end_date, time.max), current_tz)
    return week_start, week_end


def _notice_date_in_range(raw_data, start_date, end_date):
    notice_date = notice_publication_date(raw_data)
    if notice_date is None:
        collected_at = getattr(raw_data, 'collected_at', None)
        if collected_at is None:
            return False
        notice_date = timezone.localdate(collected_at)
    return start_date <= notice_date <= end_date


def _notice_action_value(count):
    if count:
        return f'확인 안 한 공지 {count}개'
    return '새 공지 없음'


def _risk_action_value(count):
    if count:
        return f'오늘 확인할 추천 {count}개'
    return '추천 항목 없음'


def _notice_source_url(raw_data):
    candidates = [getattr(raw_data, 'source_url', '')]
    metadata = raw_data.metadata_json or {}
    for source in _notice_metadata_sources(metadata):
        candidates.extend(_metadata_value(source, key) for key in NOTICE_URL_KEYS)

    normalized_urls = [_normalize_notice_url(candidate) for candidate in candidates]
    detail_urls = [url for url in normalized_urls if url and not _is_list_page_url(url)]
    if detail_urls:
        return detail_urls[0]
    return None


def _notice_title(raw_data):
    title = str(getattr(raw_data, 'title', '') or '').strip()
    if not _is_generic_notice_title(title):
        return title

    metadata = raw_data.metadata_json or {}
    for source in _notice_metadata_sources(metadata):
        for key in NOTICE_TITLE_KEYS:
            candidate = str(_metadata_value(source, key) or '').strip()
            if candidate and not _is_generic_notice_title(candidate):
                return candidate
    raw_text_title = _notice_title_from_raw_text(getattr(raw_data, 'raw_text', ''))
    if raw_text_title:
        return raw_text_title
    return title


def _notice_metadata_sources(metadata):
    if not isinstance(metadata, dict):
        return []
    return [
        metadata,
        metadata.get('raw_json'),
        metadata.get('metadata_json'),
    ]


def _metadata_value(metadata, key):
    if not isinstance(metadata, dict):
        return ''
    return metadata.get(key) or ''


def _is_generic_notice_title(title):
    cleaned = ' '.join(str(title or '').split())
    return cleaned in GENERIC_NOTICE_TITLES or cleaned.endswith('상세')


def _notice_title_from_raw_text(raw_text):
    tokens = [token.strip() for token in str(raw_text or '').replace('\n', '|').split('|')]
    tokens = [token for token in tokens if token]
    if len(tokens) >= 3 and tokens[0] in RAW_TEXT_TITLE_STOPWORDS:
        candidate = tokens[2]
        if _is_raw_text_title_candidate(candidate):
            return candidate
    for candidate in tokens:
        if _is_raw_text_title_candidate(candidate):
            return candidate
    return ''


def _is_raw_text_title_candidate(value):
    value = str(value or '').strip()
    if not value or value in RAW_TEXT_TITLE_STOPWORDS or _is_generic_notice_title(value):
        return False
    if value.isdigit() or len(value) < 4:
        return False
    if ' ' not in value and value.endswith(('건', '수')):
        return False
    return True


def _normalize_notice_url(value):
    value = str(value or '').strip()
    if not value or value == '#':
        return None
    lowered = value.lower()
    if lowered.startswith(('javascript:', 'mailto:', 'tel:')):
        return None
    if lowered.startswith(('http://', 'https://')):
        return value
    if lowered.startswith('//'):
        return f'https:{value}'
    if lowered.startswith('/'):
        base_url = _ssafy_base_url()
        return urljoin(base_url, value) if base_url else None
    return None


def _is_list_page_url(value):
    path_name = urlparse(value).path.rstrip('/').split('/')[-1].lower()
    return path_name in {'list', 'list.do', 'index', 'index.do'}


def _ssafy_base_url():
    for env_name in (
        'SSAFY_MAIN_URL',
        'SSAFY_NOTICE_LIST_URL',
        'SSAFY_MENTORING_LIST_URL',
        'SSAFY_MENTORING_NOTICE_LIST_URL',
    ):
        env_value = os.getenv(env_name, '').strip()
        if env_value:
            parsed = urlparse(env_value)
            if parsed.scheme and parsed.netloc:
                return f'{parsed.scheme}://{parsed.netloc}'
    return ''


def _safe_count(counter):
    try:
        return counter()
    except Exception:
        return 0


def _user_profile(user):
    if not getattr(user, 'is_authenticated', False):
        return None
    return getattr(user, 'profile', None) or getattr(user, 'userprofile', None) or user
