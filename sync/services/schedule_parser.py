import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.utils import timezone

from sync.services.ocr_grid_parser import GridParseDebug, parse_grid_schedule_candidates


DEFAULT_YEAR = 2026
GENERIC_TITLES = {
    '공지사항 상세',
    '게시물 상세',
    'SSAFY document',
    'SSAFY 일정',
    '怨듭??ы빆 ?곸꽭',
    '寃뚯떆臾??곸꽭',
    'SSAFY ?쇱젙',
}
DEADLINE_KEYWORDS = ['마감', '제출', '까지', 'due', 'deadline', '留덇컧', '?쒖텧', '源뚯?']
EVENT_KEYWORDS = [
    '제출', '마감', '평가', '시험', '테스트', '특강', '프로젝트', '멘토링', '발표', '설명회',
    '입과', '수료', '방학', '개강', '종강', '공통', '코딩', '월말', '과제',
    '?쒖텧', '留덇컧', '?됯?', '?쒗뿕', '?뚯뒪??', '?밴컯', '?꾨줈?앺듃',
]
CONTEXT_KEYWORDS = ['15기', '1학기', '진행일정', '전체 일정', '일정', '15湲?', '1?숆린', '吏꾪뻾?쇱젙']
META_LINE_KEYWORDS = ['운영팀', '목록', '공지사항 상세', '메뉴 네비게이션', 'HOME', 'Copyright']
WEEKDAY_HEADERS = {'SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'}
CALENDAR_KEYWORDS = [
    '신정',
    '15기 SW. AI 스타트캠프',
    '15기 SW-AI 스타트캠프',
    '15기 SW - AI 스타트캠프',
    '15기 입학식',
    '15기본학습 시작',
    'SSAFY DAY',
    '과목평가',
    '월말평가',
    'SW 역량테스트',
    'AI 강의',
    'AI 챌린지',
    '상반기 밋업',
    '어린이날',
    '근로자의 날',
    '부처님 오신날',
    '현충일',
    '온라인 위크',
    '관통 프로젝트 집중기간',
    '관통PJT 경진대회',
    '지방선거',
]

DATE_PATTERN = re.compile(
    r'(?<![\d:])'
    r'(?:(?P<year>\d{4})\s*(?:[.\-/]|년)\s*)?'
    r'(?P<month>\d{1,2})\s*(?:[.\-/]|월)\s*'
    r'(?P<day>\d{1,2})\s*(?:일)?'
    r'(?![\d:])'
)
DATE_RANGE_PATTERN = re.compile(
    r'(?<![\d:])'
    r'(?:(?P<start_year>\d{4})\s*(?:[.\-/]|년)\s*)?'
    r'(?P<start_month>\d{1,2})\s*(?:[.\-/]|월)\s*'
    r'(?P<start_day>\d{1,2})\s*(?:일)?'
    r'\s*(?:~|-|부터)\s*'
    r'(?:(?P<end_year>\d{4})\s*(?:[.\-/]|년)\s*)?'
    r'(?:(?P<end_month>\d{1,2})\s*(?:[.\-/]|월)\s*)?'
    r'(?P<end_day>\d{1,2})\s*(?:일)?'
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
CALENDAR_MONTH_PATTERN = re.compile(r'^(?P<month>[1-9]|1[0-2])\s*월$')
CALENDAR_DAY_PATTERN = re.compile(r'^(?P<day>\d{1,2})$')
CALENDAR_INLINE_EVENT_PATTERN = re.compile(r'^(?P<day>\d{1,2})\s+(?P<title>.+)$')
EVALUATION_DATE_PATTERN = re.compile(
    r'(?:(?P<year>\d{4})[.\-/년]\s*)?(?P<month>\d{1,2})\s*(?:[.\-/월])\s*(?P<day>\d{1,2})\s*(?:일)?'
)
EVALUATION_TYPE_PATTERN = re.compile(r'(월말평가|과목평가)')
TRACK_KEYWORDS = ['마이스터고', '비전공', '전공', '임베디드', '모바일', 'Python', 'Java']


@dataclass
class ParsedSchedule:
    title: str
    description: str
    start_at: datetime
    end_at: datetime
    is_all_day: bool
    event_type: str


def parse_schedule_candidates(raw_text, default_title='SSAFY 일정', ocr_boxes=None):
    schedules, _grid_debug = parse_schedule_candidates_with_debug(raw_text, default_title=default_title, ocr_boxes=ocr_boxes)
    return schedules


def parse_schedule_candidates_with_debug(raw_text, default_title='SSAFY 일정', ocr_boxes=None):
    schedules = []
    context_title = _context_title(raw_text, default_title)

    evaluation_schedules, evaluation_debug = _parse_evaluation_notice(raw_text, default_title)
    if evaluation_schedules or evaluation_debug.review_required_candidate_count:
        return _dedupe_schedules(evaluation_schedules), evaluation_debug

    grid_candidates, grid_debug = parse_grid_schedule_candidates(ocr_boxes or [])
    for candidate in grid_candidates:
        start_at = _aware(candidate.event_date, time.min)
        schedules.append(
            ParsedSchedule(
                title=candidate.title,
                description=candidate.description,
                start_at=start_at,
                end_at=start_at + timedelta(days=1),
                is_all_day=True,
                event_type=candidate.event_type,
            )
        )

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

    if not grid_candidates and not getattr(grid_debug, 'review_required_candidate_count', 0):
        schedules.extend(_parse_calendar_ocr_candidates(raw_text))
    return _dedupe_schedules(schedules), grid_debug


def _parse_evaluation_notice(raw_text, default_title):
    text = f'{default_title or ""}\n{raw_text or ""}'
    if not _looks_like_evaluation_notice(text):
        return [], GridParseDebug(candidates=[], review_required_candidates=[])

    track = _extract_clear_track(text)
    debug = GridParseDebug(candidates=[], review_required_candidates=[])
    if not track:
        debug.reason = 'evaluation_notice_track_review_required'
        debug.review_required_candidates = [
            {
                'title': default_title or '평가 안내',
                'source_text': _debug_text_sample(text),
                'review_required_reason': 'missing_or_ambiguous_track',
            }
        ]
        debug.review_required_candidate_count = 1
        return [], debug

    schedules = []
    for line in _candidate_lines_for_evaluation(text):
        match = EVALUATION_DATE_PATTERN.search(line)
        type_match = EVALUATION_TYPE_PATTERN.search(line)
        if not match or not type_match:
            continue
        subject = _evaluation_subject(line, match, type_match)
        if not subject:
            continue
        event_date = date(
            int(match.group('year') or DEFAULT_YEAR),
            int(match.group('month')),
            int(match.group('day')),
        )
        evaluation_type = type_match.group(1)
        title = f'{evaluation_type}: {subject}'
        schedules.append(
            ParsedSchedule(
                title=title[:255],
                description=f'SSAFY 평가 안내 OCR에서 추출한 {track} 트랙 시험 일정',
                start_at=_aware(event_date, time.min),
                end_at=_aware(event_date, time.min) + timedelta(days=1),
                is_all_day=True,
                event_type='exam',
            )
        )

    if not schedules:
        debug.reason = 'evaluation_notice_no_parseable_rows'
        debug.review_required_candidates = [
            {
                'title': default_title or '평가 안내',
                'source_text': _debug_text_sample(text),
                'review_required_reason': 'no_parseable_evaluation_rows',
            }
        ]
        debug.review_required_candidate_count = 1
    else:
        debug.reason = 'evaluation_notice_ok'
        debug.candidates = [
            {
                'title': schedule.title,
                'inferred_date': schedule.start_at.date().isoformat(),
                'event_type': schedule.event_type,
                'track': track,
            }
            for schedule in schedules
        ]
        debug.candidate_count = len(debug.candidates)
        debug.metadata_json = {'track': track, 'parser': 'evaluation_notice_ocr'}
    return schedules, debug


def _looks_like_evaluation_notice(text):
    return (
        '[OCR_TEXT]' in text
        and
        ('평가 안내' in text or '평가안내' in text)
        and ('과목평가' in text or '월말평가' in text)
    )


def _extract_clear_track(text):
    found = []
    for keyword in TRACK_KEYWORDS:
        if re.search(re.escape(keyword), text, re.I):
            found.append(keyword)
    return found[0] if len(set(found)) == 1 else ''


def _candidate_lines_for_evaluation(text):
    return [_normalize_line(line) for line in text.splitlines() if _normalize_line(line)]


def _evaluation_subject(line, date_match, type_match):
    subject = line[type_match.end():].strip(' :-|[]()~')
    if not subject:
        subject = line[date_match.end():type_match.start()].strip(' :-|[]()~')
    subject = re.sub(r'\b(마이스터고|비전공|전공|임베디드|모바일|Python|Java)\b', '', subject, flags=re.I).strip(' :-|[]()~')
    subject = re.sub(r'\s+', ' ', subject)
    return subject[:80]


def _debug_text_sample(text):
    return re.sub(r'\s+', ' ', text).strip()[:300]


def _parse_calendar_ocr_candidates(raw_text):
    schedules = []
    current_month = None
    current_day = None

    for raw_line in raw_text.splitlines():
        line = _normalize_line(raw_line)
        if not line or _is_calendar_noise_line(line):
            continue

        month_match = CALENDAR_MONTH_PATTERN.match(line)
        if month_match:
            current_month = int(month_match.group('month'))
            current_day = None
            continue

        if current_month is None:
            continue

        inline_match = CALENDAR_INLINE_EVENT_PATTERN.match(line)
        if inline_match:
            day = int(inline_match.group('day'))
            title_text = inline_match.group('title').strip()
            if _is_valid_calendar_day(current_month, day):
                current_day = day
                schedules.extend(_build_calendar_schedules(current_month, day, title_text, inferred=False))
                continue

        day_match = CALENDAR_DAY_PATTERN.match(line)
        if day_match:
            day = int(day_match.group('day'))
            if _is_valid_calendar_day(current_month, day):
                current_day = day
            continue

        if _has_calendar_keyword(line) and current_day is not None:
            schedules.extend(_build_calendar_schedules(current_month, current_day, line, inferred=True))

    return schedules


def _build_calendar_schedules(month, day, line, inferred):
    schedules = []
    event_date = date(DEFAULT_YEAR, month, day)
    description = 'SSAFY OCR 달력형 일정표에서 추출한 일정'
    if inferred:
        description = f'{description} - OCR 날짜 추론 필요'

    for title in _extract_calendar_titles(line):
        schedules.append(
            ParsedSchedule(
                title=title,
                description=description,
                start_at=_aware(event_date, time.min),
                end_at=_aware(event_date, time.min) + timedelta(days=1),
                is_all_day=True,
                event_type=_parse_event_type(title),
            )
        )
    return schedules


def _extract_calendar_titles(line):
    titles = []
    for part in re.split(r'\s*/\s*', line):
        title = _canonical_calendar_title(part.strip(' :-|[]()~'))
        if title and _has_calendar_keyword(title):
            titles.extend(_expand_calendar_title(title))
    return _dedupe(titles)


def _expand_calendar_title(title):
    compact = re.sub(r'[\s.()\-_/\]]+', '', str(title or '')).upper()
    if '과목평가' in compact and '월말평가' in compact:
        suffix_match = re.search(r'(?:과목평가|월말평가)(\d+)$', compact)
        suffix = suffix_match.group(1) if suffix_match else ''
        return [f'과목평가{suffix}', f'월말평가{suffix}']
    return [title[:255]]


def _canonical_calendar_title(title):
    title = re.sub(r'\s+', ' ', title)
    if '스타트캠프' in title and '15기' in title:
        return '15기 SW. AI 스타트캠프'
    return title


def _has_calendar_keyword(line):
    return any(keyword in line for keyword in CALENDAR_KEYWORDS) or (
        '스타트캠프' in line and '15기' in line
    )


def _is_calendar_noise_line(line):
    if line.upper() in WEEKDAY_HEADERS:
        return True
    return line in {'[OCR_TEXT]', 'SAMSUNG', 'SW', 'AI ACADEMY', 'FOR', 'YOUTH', '기타'}


def _is_valid_calendar_day(month, day):
    if day < 1 or day > 31:
        return False
    try:
        date(DEFAULT_YEAR, month, day)
    except ValueError:
        return False
    return True


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


def _dedupe_schedules(schedules):
    seen = set()
    deduped = []
    for schedule in schedules:
        key = (schedule.title, schedule.start_at, schedule.event_type, 'notice')
        if key in seen:
            continue
        seen.add(key)
        deduped.append(schedule)
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
    if any(keyword in line for keyword in ['평가', '월말평가', '과목평가', 'SW 역량테스트', '?됯?', '?쒗뿕', '?뚯뒪??']):
        return 'exam'
    if any(keyword in line for keyword in ['제출', '마감', '과제', '?쒖텧', '留덇컧']):
        return 'assignment'
    if any(keyword in line for keyword in ['프로젝트', 'PJT', '경진대회', '?꾨줈?앺듃']):
        return 'project'
    if any(keyword in line for keyword in ['강의', '특강', '캠프', '?밴컯']):
        return 'lecture'
    if any(keyword in line for keyword in ['공휴일', '신정', '어린이날', '근로자의 날', '부처님', '현충일', '지방선거']):
        return 'holiday'
    if any(keyword in line for keyword in ['SSAFY DAY', '입학식', '밋업']):
        return 'event'
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
