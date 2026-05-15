import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.utils import timezone


DEFAULT_YEAR = 2026
GENERIC_TITLES = {'공지사항 상세', '게시물 상세', 'SSAFY document', 'SSAFY 일정'}
DEADLINE_KEYWORDS = ['마감', '제출', '까지', 'due', 'deadline']

DATE_PATTERN = re.compile(
    r'(?<![\d:])'
    r'(?:(?P<year>\d{4})\s*(?:[.\-/]|년|\?+)\s*)?'
    r'(?P<month>\d{1,2})\s*(?:[.\-/]|월|\?+)\s*'
    r'(?P<day>\d{1,2})\s*(?:일|\?+)?'
    r'(?![\d:])'
)
DATE_RANGE_PATTERN = re.compile(
    r'(?<![\d:])'
    r'(?:(?P<start_year>\d{4})\s*(?:[.\-/]|년|\?+)\s*)?'
    r'(?P<start_month>\d{1,2})\s*(?:[.\-/]|월|\?+)\s*'
    r'(?P<start_day>\d{1,2})\s*(?:일|\?+)?'
    r'\s*(?:~|-|부터|至|～)\s*'
    r'(?:(?P<end_year>\d{4})\s*(?:[.\-/]|년|\?+)\s*)?'
    r'(?:(?P<end_month>\d{1,2})\s*(?:[.\-/]|월|\?+)\s*)?'
    r'(?P<end_day>\d{1,2})\s*(?:일|\?+)?'
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
EVENT_KEYWORDS = [
    '제출', '마감', '평가', '시험', '테스트', '특강', '프로젝트', '멘토링', '발표', '설명회',
    '입과', '수료', '방학', '개강', '종강', '공통', '코딩', '월말', '관통', '해커톤',
]
CONTEXT_KEYWORDS = ['15기', '1학기', '진행일정', '전체 일정', '일정']
META_LINE_KEYWORDS = ['운영자', '목록', '공지사항 상세', '메뉴 네비게이션', 'HOME', 'Copyright']


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
    context_title = _context_title(raw_text, default_title)

    for line in _candidate_lines(raw_text):
        date_match = DATE_RANGE_PATTERN.search(line) or DATE_PATTERN.search(line)
        if not date_match:
            continue

        try:
            start_date, end_date = _parse_date_span(date_match)
        except ValueError:
            continue

        start_at, end_at, is_all_day = _parse_datetimes(line[date_match.end():], start_date, end_date)
        title = _parse_title(line, context_title)

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
    raw_lines = [_normalize_line(raw_line) for raw_line in raw_text.splitlines()]
    raw_lines = [line for line in raw_lines if line]
    lines = []

    for index, line in enumerate(raw_lines):
        if _is_meta_line(line):
            continue
        has_date = bool(DATE_PATTERN.search(line))
        has_event_keyword = any(keyword in line for keyword in EVENT_KEYWORDS)
        should_expand_context = has_date and not has_event_keyword
        if (has_date or has_event_keyword) and not should_expand_context:
            lines.append(line)

        if should_expand_context:
            previous_line = raw_lines[index - 1] if index > 0 else ''
            next_line = raw_lines[index + 1] if index + 1 < len(raw_lines) else ''
            expanded = False
            if _looks_like_published_at(line, previous_line, next_line):
                continue
            if previous_line and not DATE_PATTERN.search(previous_line) and not _is_meta_line(previous_line):
                lines.append(f'{previous_line} {line}')
                expanded = True
            if next_line and not DATE_PATTERN.search(next_line) and not _is_meta_line(next_line):
                lines.append(f'{line} {next_line}')
                expanded = True
            if not expanded:
                lines.append(line)

    return _dedupe(lines)


def _normalize_line(raw_line):
    line = raw_line.strip(' -\t|')
    line = re.sub(r'\s+', ' ', line)
    return line


def _dedupe(lines):
    seen = set()
    deduped = []
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        deduped.append(line)
    return deduped


def _is_meta_line(line):
    return any(keyword in line for keyword in META_LINE_KEYWORDS)


def _looks_like_published_at(line, previous_line='', next_line=''):
    has_time = bool(SINGLE_TIME_PATTERN.search(line))
    has_event_word = any(keyword in line for keyword in EVENT_KEYWORDS)
    meta_context = _is_meta_line(previous_line) or _is_meta_line(next_line)
    return has_time and not has_event_word and meta_context


def _parse_date_span(date_match):
    groupdict = date_match.groupdict()
    if 'start_month' in groupdict:
        start_year = int(groupdict.get('start_year') or DEFAULT_YEAR)
        start_month = int(groupdict['start_month'])
        start_day = int(groupdict['start_day'])
        end_year = int(groupdict.get('end_year') or start_year)
        end_month = int(groupdict.get('end_month') or start_month)
        end_day = int(groupdict['end_day'])
        return date(start_year, start_month, start_day), date(end_year, end_month, end_day)

    year = int(groupdict.get('year') or DEFAULT_YEAR)
    month = int(groupdict['month'])
    day = int(groupdict['day'])
    event_date = date(year, month, day)
    return event_date, event_date


def _parse_datetimes(line, event_date, end_date=None):
    end_date = end_date or event_date
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
        return _aware(event_date, start), _aware(end_date, end), False

    single_match = SINGLE_TIME_PATTERN.search(line)
    if single_match:
        start = time(int(single_match.group('hour')), int(single_match.group('minute')))
        start_at = _aware(event_date, start)
        if _is_deadline_line(line):
            return start_at, start_at + timedelta(minutes=1), False
        return start_at, start_at + timedelta(hours=1), False

    start_at = _aware(event_date, time.min)
    return start_at, _aware(end_date, time.min) + timedelta(days=1), True


def _aware(event_date, event_time):
    naive = datetime.combine(event_date, event_time)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _parse_title(line, default_title):
    cleaned = re.sub(DATE_RANGE_PATTERN, '', line)
    cleaned = re.sub(DATE_PATTERN, '', cleaned)
    cleaned = re.sub(TIME_RANGE_PATTERN, '', cleaned)
    cleaned = re.sub(SINGLE_TIME_PATTERN, '', cleaned)
    cleaned = re.sub(r'\b\d{1,2}\s*(?:주차|차)\b', '', cleaned)
    cleaned = cleaned.strip(' :-|[]()~')
    if not cleaned or cleaned in GENERIC_TITLES:
        return default_title
    return cleaned[:255]


def _parse_event_type(line):
    if any(keyword in line for keyword in ['평가', '시험', '테스트', '월말']):
        return 'exam'
    if any(keyword in line for keyword in ['제출', '마감', '과제']):
        return 'assignment'
    if '프로젝트' in line or '관통' in line:
        return 'project'
    if any(keyword in line for keyword in ['특강', '강의', '멘토링', '설명회']):
        return 'lecture'
    return 'notice'


def _is_deadline_line(line):
    return any(keyword in line.lower() for keyword in DEADLINE_KEYWORDS)


def _context_title(raw_text, default_title):
    if default_title and default_title not in GENERIC_TITLES:
        return default_title

    for line in raw_text.splitlines():
        cleaned = _normalize_line(line)
        if any(keyword in cleaned for keyword in CONTEXT_KEYWORDS):
            return cleaned[:255]

    return default_title or 'SSAFY 일정'
