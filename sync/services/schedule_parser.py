import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.utils import timezone


DEADLINE_KEYWORDS = ['\ub9c8\uac10', '\uc81c\ucd9c', '\uae4c\uc9c0', '?쒖텧', '留덇컧', '源뚯?']


DATE_PATTERN = re.compile(
    r'(?<![\d:])'
    r'(?:(?P<year>\d{4})\s*(?:[.\-/]|년|\?+)\s*)?'
    r'(?P<month>\d{1,2})\s*(?:[.\-/]|월|\?+)\s*'
    r'(?P<day>\d{1,2})\s*(?:일|\?+)?'
    r'(?![\d:])'
)
TIME_RANGE_PATTERN = re.compile(
    r'(?<!\d)'
    r'(?P<start_hour>\d{1,2})(?::(?P<start_minute>\d{2}))?\s*'
    r'(?:~|-|부터)\s*'
    r'(?P<end_hour>\d{1,2})(?::(?P<end_minute>\d{2}))?'
    r'(?!\d)'
)
SINGLE_TIME_PATTERN = re.compile(r'(?<!\d)(?P<hour>\d{1,2}):(?P<minute>\d{2})(?:\s*까지)?(?!\d)')
EVENT_KEYWORDS = ['제출', '마감', '평가', '시험', '테스트', '특강', '프로젝트', '멘토링', '발표', '설명회']


@dataclass
class ParsedSchedule:
    title: str
    description: str
    start_at: datetime
    end_at: datetime
    is_all_day: bool
    event_type: str


def parse_schedule_candidates(raw_text, default_title='SSAFY 일정'):
    schedules = []
    current_year = timezone.localdate().year

    for line in _candidate_lines(raw_text):
        date_match = DATE_PATTERN.search(line)
        if not date_match:
            continue

        try:
            year = int(date_match.group('year') or current_year)
            month = int(date_match.group('month'))
            day = int(date_match.group('day'))
            event_date = date(year, month, day)
        except ValueError:
            continue

        start_at, end_at, is_all_day = _parse_datetimes(line[date_match.end():], event_date)
        title = _parse_title(line, default_title)

        schedules.append(
            ParsedSchedule(
                title=title,
                description='SSAFY 공지에서 추출한 일정',
                start_at=start_at,
                end_at=end_at,
                is_all_day=is_all_day,
                event_type=_parse_event_type(line),
            )
        )

    return schedules


def _candidate_lines(raw_text):
    lines = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip(' -\t')
        if not line:
            continue
        if DATE_PATTERN.search(line) or any(keyword in line for keyword in EVENT_KEYWORDS):
            lines.append(line)
    return lines


def _parse_datetimes(line, event_date):
    range_match = TIME_RANGE_PATTERN.search(line)
    if range_match:
        start = time(
            int(range_match.group('start_hour')),
            int(range_match.group('start_minute') or 0),
        )
        end = time(
            int(range_match.group('end_hour')),
            int(range_match.group('end_minute') or 0),
        )
        return _aware(event_date, start), _aware(event_date, end), False

    single_match = SINGLE_TIME_PATTERN.search(line)
    if single_match:
        start = time(int(single_match.group('hour')), int(single_match.group('minute')))
        start_at = _aware(event_date, start)
        if _is_deadline_line(line):
            return start_at, start_at + timedelta(minutes=1), False
        return start_at, start_at + timedelta(hours=1), False

    start_at = _aware(event_date, time.min)
    return start_at, start_at + timedelta(days=1), True


def _aware(event_date, event_time):
    naive = datetime.combine(event_date, event_time)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _parse_title(line, default_title):
    cleaned = re.sub(DATE_PATTERN, '', line)
    cleaned = re.sub(TIME_RANGE_PATTERN, '', cleaned)
    cleaned = re.sub(SINGLE_TIME_PATTERN, '', cleaned)
    cleaned = cleaned.strip(' :-|[]()~까지')
    return cleaned or default_title


def _parse_event_type(line):
    if any(keyword in line for keyword in ['평가', '시험', '테스트']):
        return 'exam'
    if any(keyword in line for keyword in ['제출', '마감', '과제']):
        return 'assignment'
    if '프로젝트' in line:
        return 'project'
    if any(keyword in line for keyword in ['특강', '강의', '멘토링']):
        return 'lecture'
    return 'notice'


def _is_deadline_line(line):
    return any(keyword in line for keyword in DEADLINE_KEYWORDS)
