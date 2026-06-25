import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher


DEFAULT_YEAR = 2026
WEEKDAY_HEADERS = {'SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'}
KOREAN_WEEKDAY_HEADERS = {'월', '화', '수', '목', '금', '토', '일'}
MONTH_TOKENS = {'월', '¿ù'}
MONTH_PATTERN = re.compile(r'^(?P<month>[1-9]|1[0-2])\s*월$')
DAY_PATTERN = re.compile(r'^(?P<day>\d{1,2})$')
DATE_HEADER_PATTERN = re.compile(r'^(?:(?P<month>[1-9]|1[0-2])\s*월\s*)?(?P<day>\d{1,2})\s*일(?:\s*\([^)]*\))?$')
INLINE_DAY_PATTERN = re.compile(r'^(?P<day>\d{1,2})\s+(?P<title>.+)$')
TIME_TOKEN_PATTERN = r'\d{1,2}\s*:\s*\d{2}'
OCR_TIME_FRAGMENT_PATTERN = r'\d{1,2}\s*(?:~|-)\s*\d{1,2}'
TIME_ONLY_PATTERN = re.compile(
    rf'^\s*(?:{TIME_TOKEN_PATTERN}(?:\s*(?:~|-|부터|to)?\s*{TIME_TOKEN_PATTERN})?|{OCR_TIME_FRAGMENT_PATTERN})\s*$',
    re.IGNORECASE,
)
BARE_NUMBER_TITLE_PATTERN = re.compile(r'^\s*\d{1,2}\s*$')
NUMERIC_FRAGMENT_TITLE_PATTERN = re.compile(r'^\s*\d{1,2}\s*(?:[-:~]\s*\d{1,2})?\s*$')
LIVE_BROADCAST_PREFIX = '[Live 방송]'
LEADING_TIME_RANGE_PATTERN = re.compile(
    rf'^\s*(?:{TIME_TOKEN_PATTERN}(?:\s*(?:~|-|부터|to)?\s*{TIME_TOKEN_PATTERN})?|{OCR_TIME_FRAGMENT_PATTERN})\s*:?\s*',
    re.IGNORECASE,
)
EVENT_KEYWORDS = [
    '스타트캠프',
    '입학식',
    '본학습',
    '기본학습',
    'SSAFY DAY',
    '과목평가',
    '월말평가',
    'SW 역량테스트',
    '역량',
    '역량테스트',
    '테스트',
    'AI 강의',
    'AI 챌린지',
    '밋업',
    '온라인 위크',
    '온라인 워크',
    '관통 프로젝트',
    '관통PJT',
    '경진대회',
]
HOLIDAY_COMPACT_KEYWORDS = {
    '신정',
    '어린이날',
    '근로자의날',
    '부처님오신날',
    '현충일',
    '지방선거',
}
EXAM_COMPACT_KEYWORDS = {
    '과목평가',
    '월말평가',
    'SW역량테스트',
    '역량테스트',
}
MIN_EXAM_OVERLAP_RATIO = 0.35
REVIEW_REQUIRED_EXAM_FILTER_REASONS = {
    'review_required_exam_title',
    'low_overlap_exam',
    'generic_exam_calendar_marker',
}
TIMETABLE_NOISE_COMPACTS = {
    '시간',
    '시간표',
    'LIVE',
    '방송',
    'VIEWMODEL',
    'WITHOUTQUESTIONS',
    'THEREISNOCHANGE',
    'SWAISAMSUNGACADEMY',
    'SWAISAMSUNG',
    'SAMSUNGACADEMY',
}
TIMETABLE_LUNCH_COMPACTS = {'중식', '점심', '점심시간', 'LUNCH'}


@dataclass
class GridScheduleCandidate:
    title: str
    event_date: date
    event_type: str
    description: str
    source_text: str = ''
    end_date: date = None
    source_box_count: int = 0
    row_index: int = None
    col_index: int = None
    confidence: float = None
    overlap_ratio: float = None
    reason: str = ''


@dataclass
class GridParseDebug:
    ocr_box_count: int = 0
    normalized_box_count: int = 0
    date_cell_count: int = 0
    candidate_count: int = 0
    used_grid_parser: bool = False
    reason: str = ''
    candidates: list = None
    unmatched_texts: list = None
    filtered_candidate_count: int = 0
    filtered_candidates: list = None
    review_required_candidate_count: int = 0
    review_required_candidates: list = None
    metadata_json: dict = field(default_factory=dict)

    def as_dict(self):
        return {
            'ocr_box_count': self.ocr_box_count,
            'normalized_box_count': self.normalized_box_count,
            'date_cell_count': self.date_cell_count,
            'candidate_count': self.candidate_count,
            'used_grid_parser': self.used_grid_parser,
            'reason': self.reason,
            'candidates': self.candidates or [],
            'unmatched_texts': self.unmatched_texts or [],
            'filtered_candidate_count': self.filtered_candidate_count,
            'filtered_candidates': self.filtered_candidates or [],
            'review_required_candidate_count': self.review_required_candidate_count,
            'review_required_candidates': self.review_required_candidates or [],
            **(self.metadata_json or {}),
        }


def parse_grid_schedule_candidates(ocr_boxes, source_title=''):
    debug = GridParseDebug(
        ocr_box_count=len(ocr_boxes or []),
        candidates=[],
        unmatched_texts=[],
        filtered_candidates=[],
        review_required_candidates=[],
    )
    boxes = normalize_ocr_boxes(ocr_boxes)
    debug.normalized_box_count = len(boxes)
    if not boxes:
        debug.reason = 'no_ocr_boxes'
        return [], debug

    month_sections = _month_sections(boxes)
    if not month_sections:
        debug.reason = 'no_month_headers'
        return [], debug

    candidates = []
    all_cells = []
    all_section_boxes = []
    coverage_by_source = []
    timetable_mode = _looks_like_timetable(source_title)
    for month, section_boxes in month_sections:
        cells = _build_date_cells(month, section_boxes, timetable_mode=timetable_mode)
        all_cells.extend(cells)
        all_section_boxes.extend(section_boxes)
        debug.date_cell_count += len(cells)
        if timetable_mode:
            section_candidates = _assign_timetable_events_to_cells(section_boxes, cells, source_title=source_title)
            candidates.extend(section_candidates)
            coverage_by_source.extend(_timetable_coverage_for_cells(cells, section_candidates, section_boxes))
        else:
            candidates.extend(_assign_events_to_cells(section_boxes, cells))

    candidates = _dedupe_candidates(candidates)
    if timetable_mode:
        coverage_by_source = _refresh_timetable_coverage_counts(coverage_by_source, candidates)
        coverage_by_source = _ensure_source_week_coverage(coverage_by_source, source_title)
        coverage_warnings = _timetable_coverage_warnings(coverage_by_source)
        debug.metadata_json = {
            'coverage': coverage_by_source,
            'coverage_warnings': coverage_warnings,
            'missing_weekday_dates': [
                item['date']
                for item in coverage_by_source
                if item.get('warning') == 'non_holiday_weekday_empty'
            ],
            'second_pass_attempted_dates': [
                item['date']
                for item in coverage_by_source
                if item.get('second_pass_attempted')
            ],
        }
    if timetable_mode:
        filtered_candidates = []
    else:
        candidates, filtered_candidates = _filter_exam_false_positives(candidates)
    review_required_candidates = _collect_review_required_candidates(filtered_candidates)
    debug.used_grid_parser = bool(candidates)
    debug.candidate_count = len(candidates)
    debug.filtered_candidate_count = len(filtered_candidates)
    debug.review_required_candidate_count = len(review_required_candidates)
    if timetable_mode and not candidates:
        debug.reason = 'timetable_no_cell_text_matched'
    else:
        debug.reason = 'ok' if candidates else ('review_required_candidates_only' if review_required_candidates else 'no_event_boxes_matched')
    debug.candidates = [
        {
            'title': candidate.source_text or candidate.title,
            'source_text': candidate.source_text,
            'inferred_date': candidate.event_date.isoformat(),
            'start_date': candidate.event_date.isoformat(),
            'end_date': (candidate.end_date or candidate.event_date).isoformat(),
            'event_type': candidate.event_type,
            'source_box_count': candidate.source_box_count,
            'row_index': candidate.row_index,
            'col_index': candidate.col_index,
            'confidence': candidate.confidence,
            'overlap_ratio': candidate.overlap_ratio,
            'reason': candidate.reason,
        }
        for candidate in sorted(candidates, key=lambda item: (item.event_date, item.end_date or item.event_date, item.title))
    ]
    debug.filtered_candidates = [
        {
            'title': candidate.title,
            'source_text': candidate.source_text,
            'inferred_date': candidate.event_date.isoformat(),
            'event_type': candidate.event_type,
            'source_box_count': candidate.source_box_count,
            'row_index': candidate.row_index,
            'col_index': candidate.col_index,
            'confidence': candidate.confidence,
            'overlap_ratio': candidate.overlap_ratio,
            'filtered_reason': candidate.reason,
        }
        for candidate in sorted(filtered_candidates, key=lambda item: (item.event_date, item.title, item.reason))
    ]
    debug.review_required_candidates = [
        {
            'title': candidate.source_text or candidate.title,
            'source_text': candidate.source_text,
            'canonical_title': candidate.title,
            'inferred_date': candidate.event_date.isoformat(),
            'event_type': candidate.event_type,
            'source_box_count': candidate.source_box_count,
            'row_index': candidate.row_index,
            'col_index': candidate.col_index,
            'confidence': candidate.confidence,
            'overlap_ratio': candidate.overlap_ratio,
            'review_required_reason': candidate.reason,
        }
        for candidate in sorted(review_required_candidates, key=lambda item: (item.event_date, item.title, item.reason))
    ]
    debug.unmatched_texts = _collect_unmatched_texts(all_section_boxes, all_cells, candidates)
    return candidates, debug


def normalize_ocr_boxes(ocr_boxes):
    boxes = []
    for index, raw_box in enumerate(ocr_boxes or []):
        text = str(raw_box.get('text') or '').strip()
        if not text:
            continue
        try:
            x1 = float(raw_box.get('x1'))
            y1 = float(raw_box.get('y1'))
            x2 = float(raw_box.get('x2'))
            y2 = float(raw_box.get('y2'))
        except (TypeError, ValueError):
            continue
        if x2 < x1:
            x1, x2 = x2, x1
        if y2 < y1:
            y1, y2 = y2, y1
        boxes.append(
            {
                'index': index,
                'text': text,
                'x1': x1,
                'y1': y1,
                'x2': x2,
                'y2': y2,
                'cx': (x1 + x2) / 2,
                'cy': (y1 + y2) / 2,
                'confidence': raw_box.get('confidence'),
            }
        )
    return sorted(boxes, key=lambda box: (box['y1'], box['x1'], box['index']))


def _month_sections(boxes):
    month_headers = []
    for box in boxes:
        match = MONTH_PATTERN.match(box['text'])
        if match:
            month_headers.append((int(match.group('month')), box))
            continue
        date_header_match = DATE_HEADER_PATTERN.match(box['text'])
        if date_header_match and date_header_match.group('month'):
            month_headers.append((int(date_header_match.group('month')), box))
            continue
        month = _month_from_split_boxes(box, boxes)
        if month:
            month_headers.append((month, box))

    month_headers = _dedupe_month_headers(month_headers)
    sections = []
    for index, (month, month_box) in enumerate(month_headers):
        start_y = month_box['y1']
        end_y = month_headers[index + 1][1]['y1'] if index + 1 < len(month_headers) else float('inf')
        section_boxes = [box for box in boxes if start_y <= box['cy'] < end_y]
        sections.append((month, section_boxes))
    return sections


def _month_from_split_boxes(number_box, boxes):
    try:
        month = int(number_box['text'])
    except (TypeError, ValueError):
        return None
    if month < 1 or month > 12:
        return None

    for box in boxes:
        if box is number_box:
            continue
        if box['text'] not in MONTH_TOKENS:
            continue
        same_line = abs(box['cy'] - number_box['cy']) <= max(18, number_box['y2'] - number_box['y1'])
        nearby = -8 <= box['x1'] - number_box['x2'] <= 30
        if same_line and nearby:
            return month
    return None


def _has_il_following(box, boxes):
    """Return True if box has '일' immediately to its right on the same line."""
    for other in boxes:
        if other is box:
            continue
        if other['text'] != '일':
            continue
        same_line = abs(other['cy'] - box['cy']) <= max(18, box['y2'] - box['y1'])
        to_right = 0 <= other['x1'] - box['x2'] <= 30
        if same_line and to_right:
            return True
    return False


def _looks_like_timetable_day_header(box, boxes):
    weekday_boxes = [
        other for other in boxes
        if (
            str(other.get('text') or '').strip().upper() in WEEKDAY_HEADERS
            or str(other.get('text') or '').strip() in KOREAN_WEEKDAY_HEADERS
        )
        and not _is_date_token_fragment(other, boxes)
    ]
    if not weekday_boxes:
        return False
    nearest_weekday = min(weekday_boxes, key=lambda other: abs(other['cx'] - box['cx']))
    horizontally_aligned = abs(nearest_weekday['cx'] - box['cx']) <= 55
    below_weekday = 0 <= box['y1'] - nearest_weekday['y2'] <= 80
    return horizontally_aligned and below_weekday


def _is_date_token_fragment(box, boxes):
    text = str(box.get('text') or '').strip()
    if text.upper() in WEEKDAY_HEADERS:
        return False
    if text not in KOREAN_WEEKDAY_HEADERS and text not in {'월', '일'}:
        return False
    for other in boxes:
        if other is box:
            continue
        other_text = str(other.get('text') or '').strip()
        same_line = abs(other['cy'] - box['cy']) <= max(18, box['y2'] - box['y1'])
        if not same_line:
            continue
        nearby = -35 <= other['x1'] - box['x2'] <= 35 or -35 <= box['x1'] - other['x2'] <= 35
        if nearby and (DAY_PATTERN.match(other_text) or other_text in {'월', '일'}):
            return True
    return False


def _dedupe_month_headers(month_headers):
    seen = set()
    deduped = []
    for month, box in sorted(month_headers, key=lambda item: (item[1]['y1'], item[1]['x1'])):
        key = (month, round(box['y1'] / 30))
        if key in seen:
            continue
        seen.add(key)
        deduped.append((month, box))
    return deduped


def _build_date_cells(month, boxes, timetable_mode=False):
    day_boxes = []
    for box in boxes:
        # Skip digit boxes that are month prefixes (e.g. "2" in split "2월 4일")
        if _month_from_split_boxes(box, boxes) is not None:
            continue
        day = _day_from_box(box)
        if day is None or not _valid_day(month, day):
            continue
        text = str(box['text'] or '').strip()
        # In timetable mode, a bare digit (DAY_PATTERN) must be followed by '일'
        # to qualify as a date header. This prevents time-slot digits ("9" in "9:00")
        # and numbered-item digits ("1" in "1부") from creating spurious row splits.
        if timetable_mode and DAY_PATTERN.match(text) and not _has_il_following(box, boxes) and not _looks_like_timetable_day_header(box, boxes):
            continue
        day_boxes.append((day, box))

    day_boxes = _dedupe_day_boxes(day_boxes)
    if not day_boxes:
        return []

    rows = _cluster_centers([box['cy'] for _, box in day_boxes], max_gap=55)
    columns = _cluster_centers([box['cx'] for _, box in day_boxes], max_gap=80)
    row_bounds = _bounds_from_centers(rows)
    col_bounds = _bounds_from_centers(columns)
    cells = []
    for day, box in day_boxes:
        row_index = _nearest_index(rows, box['cy'])
        col_index = _nearest_index(columns, box['cx'])
        cells.append(
            {
                'day': day,
                'date': date(DEFAULT_YEAR, month, day),
                'row_index': row_index,
                'col_index': col_index,
                'x1': col_bounds[col_index][0],
                'x2': col_bounds[col_index][1],
                'y1': row_bounds[row_index][0],
                'y2': row_bounds[row_index][1],
            }
        )
    return cells


def _day_from_box(box):
    text = str(box['text'] or '').strip()
    date_match = DATE_HEADER_PATTERN.match(text)
    if date_match:
        return int(date_match.group('day'))
    match = DAY_PATTERN.match(text)
    if match:
        return int(match.group('day'))
    inline_match = INLINE_DAY_PATTERN.match(text)
    if inline_match:
        return int(inline_match.group('day'))
    return None


def _bounds_from_centers(centers):
    bounds = []
    for index, center in enumerate(centers):
        lower = float('-inf') if index == 0 else (centers[index - 1] + center) / 2
        upper = float('inf') if index + 1 >= len(centers) else (center + centers[index + 1]) / 2
        bounds.append((lower, upper))
    return bounds


def _assign_events_to_cells(boxes, cells):
    candidates = []
    for box in boxes:
        title = _event_title_from_box(box['text'])
        if not title:
            continue
        matched_cells = _overlapping_cells(_box_rect(box), cells)
        cell, overlap_ratio = (matched_cells[0] if matched_cells else (_find_cell_for_box(box, cells), None))
        if not cell:
            continue
        for expanded_title in _expand_combined_exam_titles(title):
            candidates.append(
                GridScheduleCandidate(
                    title=expanded_title,
                    event_date=cell['date'],
                    event_type=_event_type(expanded_title),
                    description='SSAFY OCR bounding box 달력 grid에서 추출한 일정',
                    source_text=box['text'],
                    source_box_count=1,
                    row_index=cell.get('row_index'),
                    col_index=cell.get('col_index'),
                    confidence=box.get('confidence'),
                    overlap_ratio=overlap_ratio,
                    reason='single_box_keyword_overlap',
                )
            )

    for cell in cells:
        for title, row_boxes in _event_titles_from_cell(boxes, cell):
            if len(row_boxes) == 1 and _event_title_from_box(row_boxes[0]['text']) == title:
                continue
            row_rect = _boxes_rect(row_boxes)
            overlap_ratio = _overlap_ratio(row_rect, cell)
            for expanded_title in _expand_combined_exam_titles(title):
                candidates.append(
                    GridScheduleCandidate(
                        title=expanded_title,
                        event_date=cell['date'],
                        event_type=_event_type(expanded_title),
                        description='SSAFY OCR bounding box 달력 grid에서 추출한 일정',
                        source_text=' '.join(box['text'] for box in row_boxes),
                        source_box_count=len(row_boxes),
                        row_index=cell.get('row_index'),
                        col_index=cell.get('col_index'),
                        confidence=_average_confidence(row_boxes),
                        overlap_ratio=overlap_ratio,
                        reason='cell_row_group_overlap',
                    )
                )
    return candidates


def _assign_timetable_events_to_cells(boxes, cells, source_title=''):
    candidates = []
    candidates_by_date = {}
    for cell in cells:
        for title, row_boxes in _timetable_titles_from_cell(boxes, cell, source_title=source_title):
            row_rect = _boxes_rect(row_boxes)
            candidate = _build_timetable_candidate(
                title=title,
                row_boxes=row_boxes,
                row_rect=row_rect,
                cell=cell,
                reason='timetable_cell_text',
            )
            candidates.append(candidate)
            candidates_by_date.setdefault(cell['date'], []).append(candidate)

    for cell in cells:
        if cell['date'].weekday() >= 5 or candidates_by_date.get(cell['date']):
            continue
        if _cell_contains_holiday_marker(boxes, cell):
            continue
        expanded_cell = {**cell, 'x1': cell['x1'] - 35, 'x2': cell['x2'] + 35}
        for title, row_boxes in _timetable_titles_from_cell(boxes, expanded_cell, source_title=source_title):
            row_rect = _boxes_rect(row_boxes)
            candidate = _build_timetable_candidate(
                title=title,
                row_boxes=row_boxes,
                row_rect=row_rect,
                cell=cell,
                reason='timetable_cell_text_second_pass',
            )
            candidates.append(candidate)
            candidates_by_date.setdefault(cell['date'], []).append(candidate)
    return candidates


def _build_timetable_candidate(title, row_boxes, row_rect, cell, reason):
    return GridScheduleCandidate(
        title=title,
        event_date=cell['date'],
        event_type=_event_type(title),
        description='SSAFY OCR 시간표 셀에서 추출한 일정',
        source_text=' '.join(box['text'] for box in row_boxes),
        source_box_count=len(row_boxes),
        row_index=cell.get('row_index'),
        col_index=cell.get('col_index'),
        confidence=_average_confidence(row_boxes),
        overlap_ratio=_overlap_ratio(row_rect, cell),
        reason=reason,
    )


def _cell_contains_holiday_marker(boxes, cell):
    cell_text = ' '.join(
        box['text']
        for box in boxes
        if _timetable_box_belongs_to_cell(box, cell)
    )
    compact = _compact_text(cell_text)
    return any(keyword in compact for keyword in HOLIDAY_COMPACT_KEYWORDS)


def _timetable_coverage_for_cells(cells, candidates, boxes):
    coverage = []
    for cell in sorted(cells, key=lambda item: (item['date'], item.get('col_index') or 0)):
        if cell['date'].weekday() >= 5:
            continue
        event_count = len([candidate for candidate in candidates if candidate.event_date == cell['date']])
        is_holiday = _cell_contains_holiday_marker(boxes, cell)
        second_pass_attempted = any(
            candidate.event_date == cell['date'] and candidate.reason == 'timetable_cell_text_second_pass'
            for candidate in candidates
        )
        warning = 'non_holiday_weekday_empty' if event_count == 0 and not is_holiday else ''
        coverage.append(
            {
                'date': cell['date'].isoformat(),
                'event_count': event_count,
                'row_index': cell.get('row_index'),
                'col_index': cell.get('col_index'),
                'is_holiday': is_holiday,
                'second_pass_attempted': bool(second_pass_attempted or warning),
                'warning': warning,
            }
        )
    return coverage


def _refresh_timetable_coverage_counts(coverage, candidates):
    candidate_counts = {}
    second_pass_dates = set()
    for candidate in candidates:
        key = candidate.event_date.isoformat()
        candidate_counts[key] = candidate_counts.get(key, 0) + 1
        if candidate.reason == 'timetable_cell_text_second_pass':
            second_pass_dates.add(key)

    refreshed = []
    seen_dates = set()
    for item in coverage:
        if item['date'] in seen_dates:
            continue
        seen_dates.add(item['date'])
        event_count = candidate_counts.get(item['date'], 0)
        warning = 'non_holiday_weekday_empty' if event_count == 0 and not item.get('is_holiday') else ''
        refreshed.append(
            {
                **item,
                'event_count': event_count,
                'second_pass_attempted': bool(item.get('second_pass_attempted') or item['date'] in second_pass_dates or warning),
                'warning': warning,
            }
        )
    return refreshed


def _timetable_coverage_warnings(coverage):
    return [
        {
            'date': item['date'],
            'warning': item['warning'],
            'second_pass_attempted': bool(item.get('second_pass_attempted')),
        }
        for item in coverage
        if item.get('warning')
    ]


def _ensure_source_week_coverage(coverage, source_title):
    expected_dates = _expected_weekday_dates_from_source_title(source_title)
    if not expected_dates:
        return coverage
    by_date = {item['date']: item for item in coverage}
    holiday_dates = _holiday_dates_for_year(DEFAULT_YEAR)
    for expected_date in expected_dates:
        key = expected_date.isoformat()
        if key in by_date:
            continue
        is_holiday = expected_date in holiday_dates
        by_date[key] = {
            'date': key,
            'event_count': 0,
            'row_index': None,
            'col_index': None,
            'is_holiday': is_holiday,
            'second_pass_attempted': not is_holiday,
            'warning': '' if is_holiday else 'non_holiday_weekday_empty',
            'coverage_source': 'source_title_week',
        }
    return [by_date[key] for key in sorted(by_date)]


def _expected_weekday_dates_from_source_title(source_title):
    match = re.search(r'(?P<month>\d{1,2})\s*월\s*(?P<week>\d{1,2})\s*주차', str(source_title or ''))
    if not match:
        return []
    month = int(match.group('month'))
    week = int(match.group('week'))
    try:
        first_day = date(DEFAULT_YEAR, month, 1)
    except ValueError:
        return []
    first_monday = first_day + timedelta(days=(7 - first_day.weekday()) % 7)
    monday = first_monday + timedelta(days=(week - 1) * 7)
    return [
        monday + timedelta(days=offset)
        for offset in range(5)
        if (monday + timedelta(days=offset)).month == month
    ]


def _holiday_dates_for_year(year):
    try:
        from sync.management.commands.seed_korean_holidays import get_korean_holidays

        return {holiday_date for _title, holiday_date in get_korean_holidays(year)}
    except Exception:
        return set()


def _event_titles_from_cell(boxes, cell):
    cell_boxes = [
        box for box in boxes
        if _overlap_area(_box_rect(box), cell) > 0
        and not _is_structural_box(box)
    ]
    titles = []
    for row in _group_rows(cell_boxes):
        phrase = ' '.join(box['text'] for box in sorted(row, key=lambda item: item['x1']))
        title = _event_title_from_box(phrase)
        if title:
            titles.append((title, row))
    return titles


def _timetable_titles_from_cell(boxes, cell, source_title=''):
    cell_boxes = [
        box for box in boxes
        if _timetable_box_belongs_to_cell(box, cell)
        and (not _is_structural_box(box) or _is_embedded_timetable_number_box(box, boxes, cell))
    ]
    if not cell_boxes:
        return []

    titles = []
    for block in _timetable_cell_blocks(cell_boxes):
        ordered_boxes = _sort_boxes_in_reading_order(block)
        phrase = ' '.join(box['text'] for box in ordered_boxes)
        title = _clean_timetable_title(phrase, source_title=source_title)
        if title:
            titles.append((title, ordered_boxes))
    return titles


def _sort_boxes_in_reading_order(boxes):
    ordered = []
    for row in _group_rows(boxes):
        ordered.extend(sorted(row, key=lambda item: item['x1']))
    return ordered


def _timetable_box_belongs_to_cell(box, cell):
    if _overlap_area(_box_rect(box), cell) <= 0:
        return False
    return cell['x1'] - 10 <= box['x1'] <= cell['x2']


def _timetable_cell_blocks(boxes):
    blocks = []
    for row in _group_rows(boxes):
        if not blocks:
            blocks.append(list(row))
            continue
        if _should_start_new_timetable_block(blocks[-1], row):
            blocks.append(list(row))
        else:
            blocks[-1].extend(row)
    return blocks


def _should_start_new_timetable_block(previous_boxes, next_row):
    previous_phrase = _timetable_phrase(previous_boxes)
    next_phrase = _timetable_phrase(next_row)
    previous_clean = _clean_timetable_title(previous_phrase)
    next_clean = _clean_timetable_title(next_phrase)
    gap = min(box['y1'] for box in next_row) - max(box['y2'] for box in previous_boxes)

    if gap > 24:
        return True
    if _starts_new_timetable_item(next_phrase):
        return bool(previous_clean)
    if _continues_timetable_item(previous_phrase, next_phrase):
        return False
    if not previous_clean:
        return False
    if previous_clean and next_clean:
        return True
    return False


def _timetable_phrase(boxes):
    return ' '.join(box['text'] for box in _sort_boxes_in_reading_order(boxes))


def _starts_new_timetable_item(text):
    compact = _compact_text(text)
    return (
        compact in TIMETABLE_LUNCH_COMPACTS
        or _is_time_only_text(text)
        or str(text or '').lstrip().startswith('[실습')
    )


def _continues_timetable_item(previous_text, next_text):
    previous = str(previous_text or '').strip()
    next_value = str(next_text or '').strip()
    if not next_value:
        return False
    if _is_live_broadcast_only(previous) or _is_live_broadcast_only(next_value):
        return True
    if previous.endswith((':', '(', '[', '/', '&', '-', '"')):
        return True
    if previous.endswith('및'):
        return True
    if next_value.startswith(('&', '/', ')', ']', '"')):
        return True
    if previous.count('"') % 2 == 1:
        return True
    if re.match(r'^[a-z]', next_value):
        return True
    return False


def _overlapping_cells(rect, cells):
    box_area = max((rect['x2'] - rect['x1']) * (rect['y2'] - rect['y1']), 1)
    matches = []
    for cell in cells:
        area = _overlap_area(rect, cell)
        if area <= 0:
            continue
        matches.append((cell, area / box_area))
    return sorted(matches, key=lambda item: (-item[1], item[0]['date']))


def _is_embedded_timetable_number_box(box, boxes, cell):
    text = str(box.get('text') or '').strip()
    if not DAY_PATTERN.fullmatch(text):
        return False
    if box['cy'] <= cell['y1'] + 80:
        return False
    for other in boxes:
        if other is box:
            continue
        if not _timetable_box_belongs_to_cell(other, cell):
            continue
        if _is_structural_box(other):
            continue
        same_row = abs(other['cy'] - box['cy']) <= max(18, box['y2'] - box['y1'])
        nearby = -15 <= other['x1'] - box['x2'] <= 35 or -15 <= box['x1'] - other['x2'] <= 35
        if same_row and nearby:
            return True
    return False


def _overlap_ratio(rect, cell):
    rect_area = max((rect['x2'] - rect['x1']) * (rect['y2'] - rect['y1']), 1)
    return _overlap_area(rect, cell) / rect_area


def _overlap_area(rect, cell):
    x1 = max(rect['x1'], cell['x1'])
    y1 = max(rect['y1'], cell['y1'])
    x2 = min(rect['x2'], cell['x2'])
    y2 = min(rect['y2'], cell['y2'])
    if x2 <= x1 or y2 <= y1:
        return 0
    return (x2 - x1) * (y2 - y1)


def _box_rect(box):
    return {'x1': box['x1'], 'y1': box['y1'], 'x2': box['x2'], 'y2': box['y2']}


def _boxes_rect(boxes):
    return {
        'x1': min(box['x1'] for box in boxes),
        'y1': min(box['y1'] for box in boxes),
        'x2': max(box['x2'] for box in boxes),
        'y2': max(box['y2'] for box in boxes),
    }


def _collect_unmatched_texts(boxes, cells, candidates):
    matched = {(candidate.title, candidate.event_date) for candidate in candidates}
    unmatched = []
    for cell in cells:
        cell_boxes = [
            box for box in boxes
            if _overlap_area(_box_rect(box), cell) > 0
            and not _is_structural_box(box)
        ]
        for row in _group_rows(cell_boxes):
            phrase = ' '.join(box['text'] for box in sorted(row, key=lambda item: item['x1'])).strip()
            if not _is_debug_worthy_text(phrase):
                continue
            title = _event_title_from_box(phrase)
            if title and (title, cell['date']) in matched:
                continue
            unmatched.append(
                {
                    'text': phrase,
                    'inferred_date': cell['date'].isoformat(),
                    'row_index': cell.get('row_index'),
                    'col_index': cell.get('col_index'),
                    'source_box_count': len(row),
                    'confidence': _average_confidence(row),
                    'reason': 'no_event_keyword_match' if not title else 'deduped_or_filtered',
                }
            )
    return sorted(unmatched, key=lambda item: (item['inferred_date'], item['text']))[:200]


def _is_debug_worthy_text(text):
    if not text or len(text) < 2:
        return False
    if text.upper() in WEEKDAY_HEADERS or text in MONTH_TOKENS:
        return False
    return any(char.isalpha() for char in text) or any(ord(char) > 127 for char in text)


def _average_confidence(boxes):
    values = []
    for box in boxes:
        confidence = box.get('confidence')
        if confidence is None:
            continue
        try:
            values.append(float(confidence))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _group_rows(boxes):
    rows = []
    for box in sorted(boxes, key=lambda item: (item['cy'], item['x1'])):
        if not rows or abs(_row_center(rows[-1]) - box['cy']) > 16:
            rows.append([box])
        else:
            rows[-1].append(box)
    return rows


def _row_center(row):
    return sum(box['cy'] for box in row) / len(row)


def _is_structural_box(box):
    text = box['text']
    if text.upper() in WEEKDAY_HEADERS or text in MONTH_TOKENS:
        return True
    if text in KOREAN_WEEKDAY_HEADERS:
        return True
    if MONTH_PATTERN.match(text):
        return True
    if DAY_PATTERN.match(text):
        return True
    if DATE_HEADER_PATTERN.match(text):
        return True
    if _is_time_only_text(text):
        return True
    return False


def _event_title_from_box(text):
    text = re.sub(r'\s+', ' ', text).strip(' :-|[]()~')
    inline_match = INLINE_DAY_PATTERN.match(text)
    if inline_match:
        text = inline_match.group('title').strip()
    compact = _compact_text(text)
    if any(keyword in text for keyword in EVENT_KEYWORDS) or _has_compact_event_keyword(compact):
        return _canonical_title(text)
    return ''


def _clean_timetable_title(text, source_title=''):
    text = _normalize_timetable_spacing(text)
    inline_match = INLINE_DAY_PATTERN.match(text)
    if inline_match:
        text = inline_match.group('title').strip(' :-|()~')
    text, has_live_broadcast = _extract_live_broadcast(text)
    text = _remove_leading_time_range(text)
    text = _remove_timetable_noise_prefixes(text)
    text = _normalize_timetable_spacing(text)
    text = _normalize_timetable_special_title(text)
    if not text:
        return ''
    compact = _compact_text(text)
    if _is_numeric_fragment_title(text):
        return ''
    if _is_time_only_text(text):
        return ''
    if _is_timetable_header_only(text):
        return ''
    if _is_lunch_title(text):
        return ''
    if compact in TIMETABLE_NOISE_COMPACTS:
        return ''
    if any(noise in compact for noise in TIMETABLE_NOISE_COMPACTS if len(noise) >= 8):
        return ''
    if len(compact) <= 1:
        return ''
    if has_live_broadcast:
        text = f'{LIVE_BROADCAST_PREFIX} {_strip_live_broadcast(text)}'
    return _prefix_timetable_title(_normalize_timetable_spacing(text))[:255]


def _normalize_timetable_special_title(text):
    normalized = str(text or '').strip(' :-|()~')
    normalized = re.sub(r'^[\s:;\-|]+', '', normalized)
    normalized = normalized.strip('[]')
    normalized = _restore_live_broadcast_text(normalized)
    normalized = _normalize_timetable_spacing(normalized)
    normalized = re.sub(r'^\s*Live\s+', '', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'^\s*시간\s+', '', normalized)
    normalized = re.sub(r'\b\d{1,2}\s*:\s*\d{2}\s*(?:~|-|부터|to)\s*\d{1,2}\s*:\s*\d{2}\b', ' ', normalized, flags=re.IGNORECASE)
    normalized = _normalize_timetable_spacing(normalized)
    normalized = re.sub(r'월말\s+평가', '월말평가', normalized)
    normalized = re.sub(r'싸피\s+레이스', '싸피레이스', normalized)
    normalized = re.sub(r'(?<=\d)\s+학기', '학기', normalized)
    normalized = re.sub(
        r'(?i)^(관통\s*PJT|관통\s*프로젝트)\s*:?\s*(관통\s*프로젝트\s+Overview)$',
        r'\2',
        normalized,
    )
    compact = _compact_text(normalized)
    if compact in {'관통PJT', '관통프로젝트'}:
        return ''
    if _is_practice_qna_compact(compact):
        prefix = _practice_qna_prefix(normalized)
        return f'{prefix}: 실습 및 QnA' if prefix else '실습 및 QnA'
    return normalized


def _remove_timetable_noise_prefixes(text):
    text = re.sub(r'^\s*\[\s*학습\s*\]\s*', '', str(text or ''), flags=re.IGNORECASE)
    text = re.sub(r'^\s*Live\s+', '', text, flags=re.IGNORECASE)
    return text.strip(' :-|()~')


def _normalize_timetable_spacing(text):
    text = re.sub(r'\s+', ' ', str(text or '').replace('\n', ' ')).strip()
    text = re.sub(r'\s*"\s*', ' ', text)
    text = re.sub(r'(?<=\d)\s*:\s*(?=\d)', ':', text)
    text = re.sub(r'(?<=\d)\s*:\s*:\s*(?=\d)', ':', text)
    text = re.sub(r'\s*:\s*', ': ', text)
    text = re.sub(r':\s*[-~]+\s*', ': ', text)
    text = re.sub(r'\s*/\s*', ' / ', text)
    text = re.sub(r'\[\s*', '[', text)
    text = re.sub(r'\s*\]', ']', text)
    text = re.sub(r'(?<=[A-Za-z0-9가-힣])\s*-\s+(?=[A-Za-z가-힣])', ' ', text)
    text = re.sub(r'\s*&\s*', ' & ', text)
    text = re.sub(r'\bQ\s*&\s*A\b', 'Q&A', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(JS|Django)\s+(?=[A-Za-z])', r'\1: ', text)
    text = re.sub(r'\b([A-Za-z][A-Za-z0-9 /]*)\s*:\s*\1\s*:\s*', r'\1: ', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'(?<=\d)\s*:\s*(?=\d)', ':', text)
    return text.strip(' :-|')


def _prefix_timetable_title(text):
    return str(text or '').strip()


def _has_timetable_non_learning_marker(text):
    normalized = str(text or '').strip()
    compact = _compact_text(normalized)
    if normalized.startswith('[실습'):
        return True
    if any(keyword in compact for keyword in ['과목평가', '월말평가', '역량테스트']):
        return True
    return False


def _remove_leading_time_range(text):
    return LEADING_TIME_RANGE_PATTERN.sub('', str(text or ''), count=1).strip(' :-|~')


def _is_time_only_text(text):
    return bool(TIME_ONLY_PATTERN.match(_normalize_time_text(text)))


def _is_numeric_fragment_title(text):
    normalized = _normalize_time_text(text)
    return bool(
        BARE_NUMBER_TITLE_PATTERN.fullmatch(normalized)
        or NUMERIC_FRAGMENT_TITLE_PATTERN.fullmatch(normalized)
        or TIME_ONLY_PATTERN.fullmatch(normalized)
    )


def _extract_live_broadcast(text):
    restored = _restore_live_broadcast_text(text)
    has_live_broadcast = LIVE_BROADCAST_PREFIX.lower() in restored.lower()
    return _strip_live_broadcast(restored), has_live_broadcast


def _restore_live_broadcast_text(text):
    value = str(text or '')
    value = re.sub(r'방송\]\s*\[\s*Live\s*방송', LIVE_BROADCAST_PREFIX, value, flags=re.IGNORECASE)
    value = re.sub(r'\[\s*Live\s*방송\s*\]?', LIVE_BROADCAST_PREFIX, value, flags=re.IGNORECASE)
    value = re.sub(r'(?<!\[)\bLive\s*방송\s*\]', LIVE_BROADCAST_PREFIX, value, flags=re.IGNORECASE)
    value = re.sub(r'(?:\s*\[Live\s*방송\]\s*){2,}', f' {LIVE_BROADCAST_PREFIX} ', value, flags=re.IGNORECASE)
    return _normalize_timetable_spacing(value)


def _strip_live_broadcast(text):
    value = _restore_live_broadcast_text(text)
    value = re.sub(r'\s*\[Live\s*방송\]\s*', ' ', value, flags=re.IGNORECASE)
    return _normalize_timetable_spacing(value)


def _is_live_broadcast_only(text):
    return not _strip_live_broadcast(text) and LIVE_BROADCAST_PREFIX.lower() in _restore_live_broadcast_text(text).lower()


def _is_practice_qna_compact(compact):
    normalized = str(compact or '').upper()
    return (
        ('실습' in normalized and ('Q&A' in normalized or 'QNA' in normalized))
        or normalized in {'Q&A', 'QNA', '&A'}
        or ('Q&A' in normalized and normalized.count('실습') >= 1)
    )


def _practice_qna_prefix(title):
    text = _strip_live_broadcast(title)
    if re.match(r'^\s*\[?\s*실습\s*(?:및)?\s*(?:Q\s*&\s*A|QNA)', text, flags=re.IGNORECASE):
        return ''
    text = re.sub(r'[\[\]]', ' ', text)
    text = re.sub(r'\bQ\s*&\s*A\b|\bQNA\b|실습\s*(?:및)?\s*Q\s*&\s*A|실습\s*(?:및)?\s*QNA', '', text, flags=re.IGNORECASE)
    text = _normalize_timetable_spacing(text).strip(' :-|')
    if re.search(r'관통\s*PJT|관통\s*프로젝트', text, flags=re.IGNORECASE):
        return '관통 PJT'
    return text[:80].strip(' :-|')


def _normalize_time_text(text):
    value = re.sub(r'(?<=\d)\s*:\s*:\s*(?=\d)', ':', str(text or '').strip())
    return re.sub(r'(?<=\d)\s*:\s*(?=\d)', ':', value)


def _is_timetable_header_only(text):
    normalized = str(text or '').strip()
    if normalized.upper() in WEEKDAY_HEADERS or normalized in KOREAN_WEEKDAY_HEADERS:
        return True
    if MONTH_PATTERN.match(normalized) or DATE_HEADER_PATTERN.match(normalized):
        return True
    return False


def _is_lunch_title(text):
    return _compact_text(text) in TIMETABLE_LUNCH_COMPACTS


def _looks_like_timetable(source_title):
    return '시간표' in str(source_title or '')


def _has_compact_event_keyword(compact):
    return any(
        keyword in compact
        for keyword in [
            '스타트캠프',
            '입학식',
            '본학습',
            '기본학습',
            'SSAFYDAY',
            '과목평가',
            '월말평가',
            'SW역량테스트',
            '역량테스트',
            'AI강의',
            'AI챌린지',
            '밋업',
            '온라인위크',
            '온라인워크',
            '관통프로젝트',
            '관통PJT',
            '경진대회',
        ]
    )


def _canonical_title(title):
    compact = _compact_text(title)
    if '스타트캠프' in title and '15기' in title:
        return title[:255]
    if '스타트캠프' in title:
        return title[:255]
    if 'SW' in title and '역량' in title and '테스트' in title:
        return 'SW 역량테스트'
    if '역량테스트' in compact:
        return 'SW 역량테스트'
    if '본학습시작' in compact or '기본학습시작' in compact:
        return '15기본학습 시작'
    if '입학식' in compact:
        return '15기 입학식'
    if 'SSAFYDAY' in compact:
        return 'SSAFY DAY'
    if '과목평가' in compact and '월말평가' in compact:
        suffix_match = re.search(r'(?:과목평가|월말평가)(\d+)', compact)
        suffix = suffix_match.group(1) if suffix_match else ''
        return f'과목평가{suffix}/월말평가{suffix}'
    if '과목평가' in compact:
        return '과목평가'
    if '월말평가' in compact:
        return '월말평가'
    if '근로자의날' in compact:
        return '근로자의 날'
    if '부처님' in compact:
        return '부처님 오신날'
    if '온라인위크' in compact:
        return '온라인 위크'
    if '관통' in compact and 'PJT' in compact and '경진대회' in compact:
        return '관통PJT 경진대회'
    if '관통프로젝트' in compact:
        return '관통 프로젝트'
    return title[:255]


def _expand_combined_exam_titles(title):
    compact = _compact_text(title)
    if '과목평가' in compact and '월말평가' in compact:
        suffix_match = re.search(r'(?:과목평가|월말평가)(\d+)$', compact)
        suffix = suffix_match.group(1) if suffix_match else ''
        return [f'과목평가{suffix}', f'월말평가{suffix}']
    return [title]


def _find_cell_for_box(box, cells):
    matches = _overlapping_cells(_box_rect(box), cells)
    if matches:
        return matches[0][0]

    same_column = [
        cell for cell in cells
        if cell['x1'] <= box['cx'] <= cell['x2'] and cell['y1'] <= box['cy']
    ]
    if same_column:
        return min(same_column, key=lambda cell: abs(cell['y1'] - box['cy']))
    return None


def _cluster_centers(values, max_gap):
    centers = []
    for value in sorted(values):
        if not centers or abs(value - centers[-1]) > max_gap:
            centers.append(value)
        else:
            centers[-1] = (centers[-1] + value) / 2
    return centers


def _nearest_index(values, target):
    return min(range(len(values)), key=lambda index: abs(values[index] - target))


def _dedupe_day_boxes(day_boxes):
    seen = set()
    deduped = []
    for day, box in sorted(day_boxes, key=lambda item: (item[1]['y1'], item[1]['x1'])):
        key = (day, round(box['cx'] / 20), round(box['cy'] / 20))
        if key in seen:
            continue
        seen.add(key)
        deduped.append((day, box))
    return deduped


def _dedupe_candidates(candidates):
    seen = {}
    deduped = []
    for candidate in candidates:
        if _is_weekend_false_positive(candidate):
            continue
        key = _candidate_dedupe_key(candidate)
        existing_index = seen.get(key)
        if existing_index is not None:
            deduped[existing_index] = _merge_duplicate_candidate(deduped[existing_index], candidate)
            continue
        similar_index = _similar_timetable_candidate_index(candidate, deduped)
        if similar_index is not None:
            deduped[similar_index] = _merge_duplicate_candidate(deduped[similar_index], candidate)
            seen[_candidate_dedupe_key(deduped[similar_index])] = similar_index
            continue
        seen[key] = len(deduped)
        deduped.append(candidate)
    return deduped


def _candidate_dedupe_key(candidate):
    parser_reason = str(getattr(candidate, 'reason', '') or '')
    if parser_reason.startswith('timetable_cell_text'):
        return (
            candidate.event_date,
            _timetable_title_compare_key(candidate.title),
            candidate.event_type,
        )
    return (candidate.title, candidate.event_date, candidate.event_type)


def _timetable_title_compare_key(title):
    text = unicodedata.normalize('NFKC', str(title or ''))
    text = _strip_live_broadcast(text)
    text = text.casefold()
    text = re.sub(r'\bq\s*&\s*a\b|\bqna\b|\bq\s+and\s+a\b', 'qna', text, flags=re.IGNORECASE)
    text = re.sub(r'실습\s*(?:및)?\s*qna|qna\s*실습\s*qna', '실습qna', text, flags=re.IGNORECASE)
    text = re.sub(r'[\[\]():;,.|~"\'`]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'관통\s*pjt\s+실습qna', '관통 pjt 실습qna', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', '', text)


def _similar_timetable_candidate_index(candidate, deduped):
    if not str(candidate.reason or '').startswith('timetable_cell_text'):
        return None
    candidate_key = _timetable_title_compare_key(candidate.title)
    if '실습qna' not in candidate_key:
        return None
    for index, existing in enumerate(deduped):
        if existing.event_date != candidate.event_date:
            continue
        if not str(existing.reason or '').startswith('timetable_cell_text'):
            continue
        existing_key = _timetable_title_compare_key(existing.title)
        if '실습qna' not in existing_key:
            continue
        candidate_prefix = _practice_qna_prefix(candidate.title)
        existing_prefix = _practice_qna_prefix(existing.title)
        if not candidate_prefix or not existing_prefix or candidate_prefix == existing_prefix:
            return index
        if SequenceMatcher(None, candidate_key, existing_key).ratio() >= 0.86:
            return index
    return None


def _merge_duplicate_candidate(existing, incoming):
    live_broadcast = (
        LIVE_BROADCAST_PREFIX.lower() in _restore_live_broadcast_text(existing.title).lower()
        or LIVE_BROADCAST_PREFIX.lower() in _restore_live_broadcast_text(incoming.title).lower()
    )
    title = _choose_timetable_representative_title(existing.title, incoming.title)
    if live_broadcast:
        title = f'{LIVE_BROADCAST_PREFIX} {_strip_live_broadcast(title)}'
    source_texts = [existing.source_text, incoming.source_text]
    return GridScheduleCandidate(
        title=_normalize_timetable_spacing(title),
        event_date=existing.event_date,
        event_type=existing.event_type,
        description=existing.description,
        source_text=' | '.join(value for value in source_texts if value),
        end_date=existing.end_date or incoming.end_date,
        source_box_count=(existing.source_box_count or 0) + (incoming.source_box_count or 0),
        row_index=existing.row_index,
        col_index=existing.col_index,
        confidence=existing.confidence if existing.confidence is not None else incoming.confidence,
        overlap_ratio=existing.overlap_ratio if existing.overlap_ratio is not None else incoming.overlap_ratio,
        reason=existing.reason if existing.reason == incoming.reason else f'{existing.reason}+{incoming.reason}',
    )


def _choose_timetable_representative_title(first, second):
    first_clean = _strip_live_broadcast(first)
    second_clean = _strip_live_broadcast(second)
    first_key = _timetable_title_compare_key(first_clean)
    second_key = _timetable_title_compare_key(second_clean)
    if '실습qna' in first_key or '실습qna' in second_key:
        first_prefix = _practice_qna_prefix(first_clean)
        second_prefix = _practice_qna_prefix(second_clean)
        prefix = first_prefix if len(first_prefix) >= len(second_prefix) else second_prefix
        return f'{prefix}: 실습 및 QnA' if prefix else '실습 및 QnA'
    return first_clean if len(first_clean) >= len(second_clean) else second_clean


def _filter_exam_false_positives(candidates):
    filtered = []
    kept = []
    holiday_dates = {
        candidate.event_date
        for candidate in candidates
        if candidate.event_type == 'holiday' or _is_holiday_title(candidate.title)
    }

    for candidate in candidates:
        if _is_exam_candidate(candidate) and candidate.event_date in holiday_dates:
            filtered.append(_with_filtered_reason(candidate, 'holiday_exam_conflict'))
            continue
        if _is_generic_exam_calendar_marker(candidate):
            filtered.append(_with_filtered_reason(candidate, 'generic_exam_calendar_marker'))
            continue
        if _is_exam_candidate(candidate) and not _is_clear_exam_title(candidate.source_text or candidate.title):
            filtered.append(_with_filtered_reason(candidate, 'review_required_exam_title'))
            continue
        if _is_exam_candidate(candidate) and (
            not _has_clear_cell_assignment(candidate)
            and (candidate.overlap_ratio is None or candidate.overlap_ratio < MIN_EXAM_OVERLAP_RATIO)
        ):
            filtered.append(_with_filtered_reason(candidate, 'low_overlap_exam'))
            continue
        kept.append(candidate)

    return _filter_consecutive_exam_runs(kept, filtered)


def _filter_consecutive_exam_runs(candidates, filtered):
    by_title = {}
    for candidate in candidates:
        if _is_exam_candidate(candidate):
            by_title.setdefault(_compact_text(candidate.title), []).append(candidate)

    remove_ids = set()
    for title, title_candidates in by_title.items():
        if not _is_exam_title_compact(title):
            continue
        unique_by_date = {}
        for candidate in title_candidates:
            unique_by_date.setdefault(candidate.event_date, []).append(candidate)

        dates = sorted(unique_by_date)
        run = []
        previous = None
        for event_date in dates:
            if previous is None or (event_date - previous).days == 1:
                run.append(event_date)
            else:
                _mark_long_exam_run(run, unique_by_date, remove_ids)
                run = [event_date]
            previous = event_date
        _mark_long_exam_run(run, unique_by_date, remove_ids)

    kept = []
    for candidate in candidates:
        if id(candidate) in remove_ids:
            filtered.append(_with_filtered_reason(candidate, 'exam_too_many_consecutive_days'))
        else:
            kept.append(candidate)
    return kept, filtered


def _mark_long_exam_run(run, unique_by_date, remove_ids):
    if len(run) < 4:
        return
    for event_date in run[3:]:
        for candidate in unique_by_date[event_date]:
            remove_ids.add(id(candidate))


def _with_filtered_reason(candidate, reason):
    return GridScheduleCandidate(
        title=candidate.title,
        event_date=candidate.event_date,
        event_type=candidate.event_type,
        description=candidate.description,
        end_date=candidate.end_date,
        source_box_count=candidate.source_box_count,
        row_index=candidate.row_index,
        col_index=candidate.col_index,
        confidence=candidate.confidence,
        overlap_ratio=candidate.overlap_ratio,
        source_text=candidate.source_text,
        reason=reason,
    )


def _is_exam_candidate(candidate):
    return candidate.event_type == 'exam' or _is_exam_title_compact(_compact_text(candidate.title))


def _is_generic_exam_calendar_marker(candidate):
    compact_title = _normalize_exam_compact(_compact_text(candidate.title))
    compact_source = _normalize_exam_compact(_compact_text(candidate.source_text or candidate.title))
    generic_titles = {'과목평가', '월말평가'}
    return compact_title in generic_titles and compact_source in generic_titles


def _collect_review_required_candidates(filtered_candidates):
    return [
        candidate
        for candidate in filtered_candidates
        if candidate.event_type == 'exam' and candidate.reason in REVIEW_REQUIRED_EXAM_FILTER_REASONS
    ]


def _is_clear_exam_title(title):
    compact = _normalize_exam_compact(_compact_text(title))
    if _is_exam_title_compact(compact):
        return True

    remaining = compact
    for keyword in sorted(EXAM_COMPACT_KEYWORDS, key=len, reverse=True):
        remaining = remaining.replace(keyword, '')
    remaining = re.sub(r'\d+', '', remaining)
    return not remaining and compact != ''


def _is_exam_title_compact(compact):
    compact = _normalize_exam_compact(compact)
    if not compact:
        return False
    if compact in EXAM_COMPACT_KEYWORDS:
        return True
    for keyword in EXAM_COMPACT_KEYWORDS:
        if compact.startswith(keyword) and compact[len(keyword):].isdigit():
            return True
    return False


def _has_clear_cell_assignment(candidate):
    return candidate.row_index is not None and candidate.col_index is not None


def _normalize_exam_compact(compact):
    return (
        compact
        .replace('평가과목', '과목평가')
        .replace('평가월말', '월말평가')
        .replace('테스트역량', '역량테스트')
    )


def _is_holiday_title(title):
    compact = _compact_text(title)
    return any(keyword in compact for keyword in HOLIDAY_COMPACT_KEYWORDS)


def _is_weekend_false_positive(candidate):
    if candidate.event_date.weekday() < 5:
        return False
    if candidate.event_type == 'holiday':
        return False
    return True


def _valid_day(month, day):
    try:
        date(DEFAULT_YEAR, month, day)
    except ValueError:
        return False
    return True


def _compact_text(text):
    return re.sub(r'[\s.()\-_/\]]+', '', str(text or '')).upper()


def _event_type(title):
    compact = _compact_text(title)
    if any(keyword in title for keyword in ['평가', '월말평가', '과목평가', 'SW 역량테스트', '역량', '테스트']) or any(
        keyword in compact for keyword in ['평가', '월말평가', '과목평가', 'SW역량테스트', '역량테스트']
    ):
        return 'exam'
    if any(keyword in title for keyword in ['프로젝트', 'PJT', '경진대회']) or any(
        keyword in compact for keyword in ['프로젝트', 'PJT', '경진대회']
    ):
        return 'project'
    if any(keyword in title for keyword in ['강의', '특강', '캠프']) or any(
        keyword in compact for keyword in ['강의', '특강', '캠프']
    ):
        return 'lecture'
    if any(keyword in title for keyword in ['SSAFY DAY', '입학식', '밋업']) or any(
        keyword in compact for keyword in ['SSAFYDAY', '입학식', '밋업']
    ):
        return 'event'
    return 'study'
