import calendar
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from django.utils import timezone

from schedules.models import ScheduleEvent
from schedules.utils import normalize_event_title_for_dedupe
from sync.management.commands.seed_korean_holidays import get_korean_holidays
from sync.models import RawSsafyData


GENERATED_REPAIR_SOURCES = {'manual_calendar_correction', 'manual_exam_correction'}
CLASS_EVENT_TYPES = {'study', 'lecture', 'exam', 'project'}
TIME_ONLY_PATTERN = re.compile(
    r'^\s*\[?[^\]]{0,20}\]?\s*\d{1,2}\s*:\s*\d{2}\s*(?:~|-|to)?\s*\d{1,2}\s*:\s*\d{2}\s*$',
    re.IGNORECASE,
)
MEANINGLESS_TITLES = {
    '',
    'time',
    'schedule',
    'timetable',
    'live',
    'broadcast',
    'viewmodel',
    'withoutquestions',
    'lunch',
}


@dataclass
class SuspiciousEvent:
    event: ScheduleEvent
    reasons: list = field(default_factory=list)


@dataclass
class CalendarQualityReport:
    year: int
    month: int
    start_at: datetime
    end_at: datetime
    events: list
    daily_counts: dict
    generated_count: int
    manual_count: int
    holiday_count: int
    skip_reason_counts: Counter
    suspicious_events: list
    suspicious_empty_weekdays: list


def parse_month_option(month_value, year_value=2026):
    text = str(month_value or '').strip()
    if re.match(r'^\d{4}-\d{1,2}$', text):
        year_text, month_text = text.split('-', 1)
        return int(year_text), int(month_text)
    return int(year_value), int(text or 1)


def month_bounds(year, month):
    first_day = timezone.make_aware(datetime.combine(datetime(year, month, 1).date(), time.min))
    if month == 12:
        next_month = timezone.make_aware(datetime.combine(datetime(year + 1, 1, 1).date(), time.min))
    else:
        next_month = timezone.make_aware(datetime.combine(datetime(year, month + 1, 1).date(), time.min))
    return first_day, next_month


def build_month_quality_report(year, month):
    start_at, end_at = month_bounds(year, month)
    events = list(
        ScheduleEvent.objects.filter(start_at__lt=end_at, end_at__gt=start_at)
        .select_related('raw_data')
        .order_by('start_at', 'id')
    )
    holidays = korean_holiday_dates(year)
    daily_counts = _daily_counts(events)
    suspicious = _suspicious_events(events, holidays)
    return CalendarQualityReport(
        year=year,
        month=month,
        start_at=start_at,
        end_at=end_at,
        events=events,
        daily_counts=daily_counts,
        generated_count=sum(1 for event in events if is_generated_event(event)),
        manual_count=sum(1 for event in events if is_manual_event(event)),
        holiday_count=sum(1 for event in events if event.event_type == 'holiday'),
        skip_reason_counts=raw_skip_reason_counts(),
        suspicious_events=suspicious,
        suspicious_empty_weekdays=_empty_weekdays(year, month, daily_counts, holidays),
    )


def korean_holiday_dates(year):
    try:
        return {holiday_date for _title, holiday_date in get_korean_holidays(year)}
    except ValueError:
        return set()


def is_generated_event(event):
    metadata = event.metadata_json or {}
    return bool(
        event.raw_data_id
        or metadata.get('raw_data_id')
        or metadata.get('repair_source') in GENERATED_REPAIR_SOURCES
    )


def is_deletable_generated_event(event):
    metadata = event.metadata_json or {}
    return bool(event.raw_data_id or metadata.get('raw_data_id'))


def is_manual_event(event):
    return not is_generated_event(event) and event.event_type != 'holiday'


def raw_skip_reason_counts():
    counts = Counter()
    for raw_data in RawSsafyData.objects.all().only('metadata_json'):
        metadata = raw_data.metadata_json or {}
        for warning in metadata.get('parser_warnings') or []:
            counts[str(warning)] += 1
        for candidate in metadata.get('review_required_candidates') or []:
            reason = candidate.get('review_required_reason') or 'review_required'
            counts[str(reason)] += 1
    return counts


def format_count_dict(values):
    if not values:
        return 'none'
    return '|'.join(f'{key}:{values[key]}' for key in sorted(values))


def format_daily_counts(report):
    if not report.daily_counts:
        return 'none'
    return '|'.join(f'{day.isoformat()}:{report.daily_counts[day]}' for day in sorted(report.daily_counts))


def format_suspicious_events(suspicious_events):
    if not suspicious_events:
        return 'none'
    parts = []
    for item in suspicious_events:
        event = item.event
        metadata = event.metadata_json or {}
        parts.append(
            '#'.join(
                [
                    timezone.localdate(event.start_at).isoformat(),
                    str(event.id),
                    '+'.join(sorted(item.reasons)),
                    _safe(metadata.get('display_title') or event.title),
                    _safe(metadata.get('date_mapping_source') or ''),
                    str(event.raw_data_id or ''),
                ]
            )
        )
    return '|'.join(parts)


def format_empty_weekdays(days):
    return '|'.join(day.isoformat() for day in days) or 'none'


def suspicious_events_by_reason(suspicious_events, reason):
    return [item for item in suspicious_events if reason in item.reasons]


def _daily_counts(events):
    counts = Counter()
    for event in events:
        counts[timezone.localdate(event.start_at)] += 1
    return dict(counts)


def _suspicious_events(events, holidays):
    by_id = {}
    for event in events:
        reasons = []
        metadata = event.metadata_json or {}
        event_date = timezone.localdate(event.start_at)
        generated = is_generated_event(event)
        mapping_source = metadata.get('date_mapping_source')

        if generated and event.event_type != 'holiday' and event_date in holidays:
            reasons.append('holiday_generated')
        if generated and event.event_type in CLASS_EVENT_TYPES and event_date.weekday() >= 5:
            reasons.append('weekend_generated')
        if mapping_source == 'fallback_week':
            reasons.append('fallback_week')
        if _is_meaningless_title(metadata.get('display_title') or event.title):
            reasons.append('meaningless_title')

        if reasons:
            by_id[event.id] = SuspiciousEvent(event=event, reasons=reasons)

    for duplicate_group in _duplicate_generated_groups(events):
        for event in duplicate_group:
            item = by_id.setdefault(event.id, SuspiciousEvent(event=event, reasons=[]))
            if 'duplicate_title' not in item.reasons:
                item.reasons.append('duplicate_title')

    return list(by_id.values())


def _duplicate_generated_groups(events):
    groups = defaultdict(list)
    for event in events:
        if not is_generated_event(event):
            continue
        normalized = normalize_event_title_for_dedupe(event.title)
        if not normalized:
            continue
        metadata = event.metadata_json or {}
        audience = metadata.get('audience') or {}
        track = str(metadata.get('track') or audience.get('track') or '').strip().lower()
        key = (
            timezone.localdate(event.start_at),
            event.start_at,
            event.end_at,
            event.event_type,
            track,
            normalized,
        )
        groups[key].append(event)
    return [group for group in groups.values() if len(group) > 1]


def _empty_weekdays(year, month, daily_counts, holidays):
    _, last_day = calendar.monthrange(year, month)
    days = []
    for day in range(1, last_day + 1):
        current = datetime(year, month, day).date()
        if current.weekday() >= 5 or current in holidays:
            continue
        if daily_counts.get(current, 0) == 0:
            days.append(current)
    return days


def _is_meaningless_title(title):
    text = str(title or '').strip()
    compact = re.sub(r'[\s\[\].:()_\-/~]+', '', text).lower()
    if TIME_ONLY_PATTERN.match(text):
        return True
    if len(compact) <= 1:
        return True
    return compact in MEANINGLESS_TITLES


def _safe(value):
    return str(value if value is not None else '').replace('\n', ' ').replace('\r', ' ').replace('|', '/')
