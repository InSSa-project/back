import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.utils import timezone

from schedules.utils import (
    is_meaningless_schedule_title,
    normalize_event_title_for_dedupe,
    normalize_schedule_display_title,
)
from sync.management.commands.seed_korean_holidays import get_korean_holidays
from sync.services.ocr_grid_parser import GridParseDebug, parse_grid_schedule_candidates
from sync.services.tracks import (
    COMMON_TRACK_KEY,
    canonical_track_display,
    canonical_track_keys,
    common_track_metadata,
    normalize_track_key,
    track_key_from_text,
)


DEFAULT_YEAR = 2026
ONLINE_WEEK_KEYWORDS = ('온라인 위크', 'online week')
GENERIC_TITLES = {
    '공지사항 상세',
    '게시물 상세',
    'SSAFY document',
    'SSAFY 일정',
}
DEADLINE_KEYWORDS = ['마감', '제출', '까지', 'due', 'deadline']
EVENT_KEYWORDS = [
    '제출', '마감', '평가', '시험', '테스트', '특강', '프로젝트', '멘토링', '발표', '설명회',
    '입과', '수료', '방학', '개강', '종강', '공통', '코딩', '월말', '과제',
]
CONTEXT_KEYWORDS = ['15기', '1학기', '진행일정', '전체 일정', '일정']
META_LINE_KEYWORDS = ['운영팀', '목록', '공지사항 상세', '메뉴 네비게이션', 'HOME', 'Copyright']
WEEKDAY_HEADERS = {'SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'}
CALENDAR_KEYWORDS = [
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
    '온라인 위크',
    '관통 프로젝트 집중기간',
    '관통PJT 경진대회',
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
NOISE_TITLE_COMPACTS = {
    '시간',
    'VIEWMODEL',
    'WITHOUTQUESTIONS',
    'LIVE방송',
    'LIVE',
    '방송',
}
NOISE_TITLE_CONTAINS = [
    '싸피티비',
    '치킨세트',
    '박슬기',
    '수다타임',
    '중요!방송인',
    '시간표운영자',
    '알림신청',
    '이벤트게시물',
    '영상에댓글',
    '삼성청년SW',
    '사무국입니다',
    '첨부파일',
]


@dataclass
class ParsedSchedule:
    title: str
    description: str
    start_at: datetime
    end_at: datetime
    is_all_day: bool
    event_type: str
    metadata_json: dict = None


def parse_schedule_candidates(raw_text, default_title='SSAFY 일정', ocr_boxes=None):
    schedules, _grid_debug = parse_schedule_candidates_with_debug(raw_text, default_title=default_title, ocr_boxes=ocr_boxes)
    return schedules


def parse_schedule_candidates_with_debug(raw_text, default_title='SSAFY 일정', ocr_boxes=None):
    schedules = []
    context_title = _context_title(raw_text, default_title)

    evaluation_schedules, evaluation_debug = _parse_evaluation_notice(raw_text, default_title)
    if evaluation_schedules or evaluation_debug.review_required_candidate_count:
        return _dedupe_schedules(evaluation_schedules), evaluation_debug

    grid_candidates, grid_debug = parse_grid_schedule_candidates(ocr_boxes or [], source_title=default_title)
    for candidate in grid_candidates:
        start_at = _aware(candidate.event_date, time.min)
        is_timetable_candidate = candidate.reason == 'timetable_cell_text'
        schedules.append(
            ParsedSchedule(
                title=candidate.title,
                description='' if is_timetable_candidate else candidate.description,
                start_at=start_at,
                end_at=start_at + timedelta(days=1),
                is_all_day=True,
                event_type=candidate.event_type,
                metadata_json={
                    'parser': 'ocr_timetable_grid' if is_timetable_candidate else 'ocr_calendar_grid',
                    'parser_type': 'timetable_grid' if is_timetable_candidate else 'calendar_grid',
                    'raw_title': candidate.source_text or candidate.title,
                    'source_title': default_title,
                    'source_text': candidate.source_text,
                    'display_title': normalize_schedule_display_title(candidate.title),
                    'category_label': _category_label(candidate.event_type),
                    'track': _extract_track_from_title(default_title),
                    'candidate_date': candidate.event_date.isoformat(),
                    'date_mapping_source': 'ocr_header' if is_timetable_candidate else 'explicit_text_date',
                    'original_header_date': candidate.event_date.isoformat() if is_timetable_candidate else '',
                    'fallback_date': '',
                    'skip_reason': '',
                    'row_index': candidate.row_index,
                    'col_index': candidate.col_index,
                    'confidence': candidate.confidence,
                    'overlap_ratio': candidate.overlap_ratio,
                },
            )
        )
    if grid_candidates and '시간표' in str(default_title or ''):
        return _dedupe_schedules(schedules), grid_debug

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
        if _looks_like_online_week(line):
            schedules.extend(_build_online_week_schedules(line, default_title, start_date, end_date))
            continue

        schedules.append(
            ParsedSchedule(
                title=title,
                description='SSAFY 공지에서 추출한 일정',
                start_at=start_at,
                end_at=end_at,
                is_all_day=is_all_day,
                event_type=_parse_event_type(line),
                metadata_json={
                    'parser': 'notice_text',
                    'parser_type': 'text_date',
                    'raw_title': title,
                    'source_title': default_title,
                    'display_title': normalize_schedule_display_title(title),
                    'candidate_date': start_date.isoformat(),
                    'date_mapping_source': 'explicit_text_date',
                    'confidence': 0.85,
                    'skip_reason': '',
                },
            )
        )

    if not grid_candidates and not getattr(grid_debug, 'review_required_candidate_count', 0):
        schedules.extend(_parse_calendar_ocr_candidates(raw_text))
    return _dedupe_schedules(schedules), grid_debug


def _category_label(event_type):
    labels = {
        'exam': '평가',
        'project': '프로젝트',
        'study': '학습',
        'lecture': '학습',
        'notice': '기타',
        'assignment': '기타',
    }
    return labels.get(event_type, '기타')


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
                metadata_json={
                    'parser': 'calendar_ocr_text',
                    'parser_type': 'calendar_text',
                    'raw_title': title,
                    'display_title': normalize_schedule_display_title(title),
                    'candidate_date': event_date.isoformat(),
                    'date_mapping_source': 'unknown' if inferred else 'explicit_text_date',
                    'confidence': 0.55 if inferred else 0.75,
                    'skip_reason': '',
                },
            )
        )
    return schedules


def _looks_like_online_week(line):
    lowered = str(line or '').lower()
    return any(keyword.lower() in lowered for keyword in ONLINE_WEEK_KEYWORDS)


def _build_online_week_schedules(line, default_title, start_date, end_date):
    holidays = _holiday_dates(start_date.year)
    schedules = []
    current = start_date
    while current <= end_date:
        if current.weekday() < 5 and current not in holidays:
            title = _online_week_title(line)
            schedules.append(
                ParsedSchedule(
                    title=title,
                    description=f'SSAFY notice date range: {default_title}',
                    start_at=_aware(current, time.min),
                    end_at=_aware(current, time.min) + timedelta(days=1),
                    is_all_day=True,
                    event_type='study',
                    metadata_json={
                        'parser': 'notice_text_range',
                        'parser_type': 'text_date_range',
                        'raw_title': title,
                        'source_title': default_title,
                        'display_title': normalize_schedule_display_title(title),
                        'candidate_date': current.isoformat(),
                        'date_mapping_source': 'explicit_text_date',
                        'confidence': 0.9,
                        'skip_reason': '',
                    },
                )
            )
        current += timedelta(days=1)
    return schedules


def _online_week_title(line):
    if '온라인 위크' in str(line or ''):
        return '온라인 위크'
    for keyword in ONLINE_WEEK_KEYWORDS:
        if keyword.lower() in str(line or '').lower():
            return keyword
    return '온라인 위크'


def _holiday_dates(year):
    try:
        return {holiday_date for _title, holiday_date in get_korean_holidays(year)}
    except ValueError:
        return set()


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
        if _is_noise_schedule_title(schedule.title):
            continue
        _ensure_schedule_track_metadata(schedule)
        metadata = schedule.metadata_json or {}
        track_key = normalize_track_key(metadata.get('track_key') or metadata.get('track') or '')
        parser_type = metadata.get('parser_type') or metadata.get('parser') or ''
        title_key = (
            normalize_event_title_for_dedupe(schedule.title)
            if parser_type in {'timetable_grid', 'ocr_timetable_grid'}
            else schedule.title
        )
        time_key = schedule.start_at.date() if parser_type in {'timetable_grid', 'ocr_timetable_grid'} else schedule.start_at
        key = (title_key, time_key, schedule.event_type, track_key or 'notice')
        if key in seen:
            continue
        seen.add(key)
        deduped.append(schedule)
    return deduped


def _ensure_schedule_track_metadata(schedule):
    metadata = dict(schedule.metadata_json or {})
    audience = dict(metadata.get('audience') or {})
    track_key = normalize_track_key(metadata.get('track_key') or metadata.get('track') or audience.get('track') or '')
    if track_key and track_key != COMMON_TRACK_KEY:
        metadata['track_key'] = track_key
        metadata.setdefault('track', canonical_track_display(track_key))
        metadata.setdefault('track_display', canonical_track_display(track_key))
        metadata['is_common'] = False
        audience['track_key'] = track_key
        audience['track'] = track_key
    else:
        metadata.update(common_track_metadata())
        audience['track_key'] = COMMON_TRACK_KEY
        audience['track'] = COMMON_TRACK_KEY
    metadata['audience'] = audience
    schedule.metadata_json = metadata


def _is_noise_schedule_title(title):
    if is_meaningless_schedule_title(title):
        return True
    compact = re.sub(r'[\s.()\-_/\]]+', '', str(title or '')).upper()
    if compact in NOISE_TITLE_COMPACTS:
        return True
    if compact in {'운영자', '♥알림신청♥', '공지사항상세', '목록'}:
        return True
    if any(noise.upper() in compact for noise in NOISE_TITLE_CONTAINS):
        return True
    if compact.endswith('운영자') and not _is_allowed_promotional_exception(title):
        return True
    if '출연' in compact and not _is_allowed_promotional_exception(title):
        return True
    if len(compact) <= 1:
        return True
    return False


def _is_allowed_promotional_exception(title):
    normalized = str(title or '')
    allowed_keywords = [
        'SW역량테스트',
        '과목평가',
        '월말평가',
        'AI 강의',
        '설날',
        'SSAFY DAY',
        '온라인 위크',
        '관통 프로젝트 집중기간',
        '상반기 밋업',
    ]
    return any(keyword in normalized for keyword in allowed_keywords)


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
    if any(keyword in line for keyword in ['평가', '월말평가', '과목평가', 'SW 역량테스트']):
        return 'exam'
    if any(keyword in line for keyword in ['제출', '마감', '과제']):
        return 'assignment'
    if any(keyword in line for keyword in ['프로젝트', 'PJT', '경진대회']):
        return 'project'
    if any(keyword in line for keyword in ['강의', '특강', '캠프']):
        return 'lecture'
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


def _extract_clear_track(text):
    canonical = track_key_from_text(text)
    if canonical:
        return canonical
    found = []
    for keyword in TRACK_KEYWORDS:
        if re.search(re.escape(keyword), text, re.I):
            found.append(keyword)
    return found[0] if len(set(found)) == 1 else ''


def _extract_track_from_title(title):
    canonical = track_key_from_text(title)
    if canonical:
        return canonical_track_display(canonical)
    text = str(title or '')
    track_map = [
        ('Python', 'Python'),
        ('Data', 'Data'),
        ('Java', 'Java'),
        ('Embedded Robot', 'Embedded Robot'),
        ('Embedded', 'Embedded'),
        ('Mobile', 'Mobile'),
    ]
    for keyword, label in track_map:
        if keyword.lower() in text.lower():
            return label
    return ''




def _looks_like_evaluation_notice(text):
    value = str(text or '')
    compact = re.sub(r'\s+', '', value)
    eval_notice = '\ud3c9\uac00\uc548\ub0b4'
    subject_exam = '\uacfc\ubaa9\ud3c9\uac00'
    monthly_exam = '\uc6d4\ub9d0\ud3c9\uac00'
    return '[OCR_TEXT]' in value and (
        (eval_notice in compact and (subject_exam in compact or monthly_exam in compact))
        or (('???' in value or '??????' in value or '??????' in value) and ('???' in value or '??? ???' in value))
    )


def _parse_evaluation_notice(raw_text, default_title):
    text = f'{default_title or ""}\n{raw_text or ""}'
    if not _looks_like_evaluation_notice(text):
        return [], GridParseDebug(candidates=[], review_required_candidates=[])

    track = _extract_clear_track(text)
    target_tracks = canonical_track_keys() if track else [COMMON_TRACK_KEY]
    debug = GridParseDebug(candidates=[], review_required_candidates=[])
    schedules = []
    day = '\uc77c'
    month = '\uc6d4'
    subject_exam = '\uacfc\ubaa9\ud3c9\uac00'
    monthly_exam = '\uc6d4\ub9d0\ud3c9\uac00'
    actual_pattern = re.compile(
        rf'(?:(?P<year>\d{{4}})[.\-/\s]*)?'
        rf'(?P<month>\d{{1,2}})\s*(?:[.\-/]|{month})\s*'
        rf'(?P<day>\d{{1,2}})\s*(?:{day})?\s*'
        rf'(?P<kind>{monthly_exam}|{subject_exam})\s*(?P<subject>.*)'
    )

    for line in _candidate_lines_for_evaluation(text):
        actual_match = actual_pattern.search(line)
        if actual_match:
            event_date = date(
                int(actual_match.group('year') or DEFAULT_YEAR),
                int(actual_match.group('month')),
                int(actual_match.group('day')),
            )
            title = f"{actual_match.group('kind')}: {actual_match.group('subject').strip(' :-|[]()~')}".rstrip(': ')
        else:
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
            title = f'{type_match.group(1)}: {subject}'
        for track_key in target_tracks:
            track_display = 'All' if track_key == COMMON_TRACK_KEY else canonical_track_display(track_key)
            common_metadata = common_track_metadata() if track_key == COMMON_TRACK_KEY else {}
            schedules.append(
                ParsedSchedule(
                    title=title[:255],
                    description=f'SSAFY evaluation notice parsed for {track_display} track',
                    start_at=_aware(event_date, time.min),
                    end_at=_aware(event_date, time.min) + timedelta(days=1),
                    is_all_day=True,
                    event_type='exam',
                    metadata_json={
                        'parser': 'evaluation_notice_ocr',
                        'parser_type': 'evaluation_notice',
                        'raw_title': title,
                        'source_title': default_title,
                        'display_title': normalize_schedule_display_title(title),
                        'track': track_display,
                        'track_key': track_key,
                        'track_display': track_display,
                        **common_metadata,
                        'candidate_date': event_date.isoformat(),
                        'date_mapping_source': 'explicit_text_date',
                        'confidence': 0.9 if track else 0.8,
                        'skip_reason': '',
                    },
                )
            )

    if not schedules:
        debug.reason = 'evaluation_notice_no_parseable_rows'
        debug.review_required_candidates = [
            {
                'title': default_title or 'evaluation notice',
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
                'track': (schedule.metadata_json or {}).get('track') or '',
            }
            for schedule in schedules
        ]
        debug.candidate_count = len(debug.candidates)
        debug.metadata_json = {'track': canonical_track_display(track) if track else 'all', 'parser': 'evaluation_notice_ocr'}
    return schedules, debug
