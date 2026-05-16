import re
from dataclasses import dataclass
from datetime import date


DEFAULT_YEAR = 2026
WEEKDAY_HEADERS = {'SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'}
MONTH_TOKENS = {'월', '¿ù'}
MONTH_PATTERN = re.compile(r'^(?P<month>[1-9]|1[0-2])\s*월$')
DAY_PATTERN = re.compile(r'^(?P<day>\d{1,2})$')
INLINE_DAY_PATTERN = re.compile(r'^(?P<day>\d{1,2})\s+(?P<title>.+)$')
EVENT_KEYWORDS = [
    '신정',
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
    '어린이날',
    '근로자의 날',
    '부처님',
    '현충일',
    '온라인 위크',
    '온라인 워크',
    '관통 프로젝트',
    '관통PJT',
    '경진대회',
    '지방선거',
]


@dataclass
class GridScheduleCandidate:
    title: str
    event_date: date
    event_type: str
    description: str
    end_date: date = None
    source_box_count: int = 0
    row_index: int = None
    col_index: int = None
    confidence: float = None
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
        }


def parse_grid_schedule_candidates(ocr_boxes):
    debug = GridParseDebug(
        ocr_box_count=len(ocr_boxes or []),
        candidates=[],
        unmatched_texts=[],
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
    for month, section_boxes in month_sections:
        cells = _build_date_cells(month, section_boxes)
        all_cells.extend(cells)
        all_section_boxes.extend(section_boxes)
        debug.date_cell_count += len(cells)
        candidates.extend(_assign_events_to_cells(section_boxes, cells))

    candidates = _dedupe_candidates(candidates)
    debug.used_grid_parser = bool(candidates)
    debug.candidate_count = len(candidates)
    debug.reason = 'ok' if candidates else 'no_event_boxes_matched'
    debug.candidates = [
        {
            'title': candidate.title,
            'inferred_date': candidate.event_date.isoformat(),
            'start_date': candidate.event_date.isoformat(),
            'end_date': (candidate.end_date or candidate.event_date).isoformat(),
            'event_type': candidate.event_type,
            'source_box_count': candidate.source_box_count,
            'row_index': candidate.row_index,
            'col_index': candidate.col_index,
            'confidence': candidate.confidence,
            'reason': candidate.reason,
        }
        for candidate in sorted(candidates, key=lambda item: (item.event_date, item.end_date or item.event_date, item.title))
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


def _build_date_cells(month, boxes):
    day_boxes = []
    for box in boxes:
        day = _day_from_box(box)
        if day and _valid_day(month, day):
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
    match = DAY_PATTERN.match(box['text'])
    if match:
        return int(match.group('day'))
    inline_match = INLINE_DAY_PATTERN.match(box['text'])
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
        cell = matched_cells[0][0] if matched_cells else _find_cell_for_box(box, cells)
        if not cell:
            continue
        candidates.append(
            GridScheduleCandidate(
                title=title,
                event_date=cell['date'],
                event_type=_event_type(title),
                description='SSAFY OCR bounding box 달력 grid에서 추출한 일정',
                source_box_count=1,
                row_index=cell.get('row_index'),
                col_index=cell.get('col_index'),
                confidence=box.get('confidence'),
                reason='single_box_keyword_overlap',
            )
        )

    for cell in cells:
        for title, row_boxes in _event_titles_from_cell(boxes, cell):
            if len(row_boxes) == 1 and _event_title_from_box(row_boxes[0]['text']) == title:
                continue
            candidates.append(
                GridScheduleCandidate(
                    title=title,
                    event_date=cell['date'],
                    event_type=_event_type(title),
                    description='SSAFY OCR bounding box 달력 grid에서 추출한 일정',
                    source_box_count=len(row_boxes),
                    row_index=cell.get('row_index'),
                    col_index=cell.get('col_index'),
                    confidence=_average_confidence(row_boxes),
                    reason='cell_row_group_overlap',
                )
            )
    return candidates


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


def _overlapping_cells(rect, cells):
    box_area = max((rect['x2'] - rect['x1']) * (rect['y2'] - rect['y1']), 1)
    matches = []
    for cell in cells:
        area = _overlap_area(rect, cell)
        if area <= 0:
            continue
        matches.append((cell, area / box_area))
    return sorted(matches, key=lambda item: (-item[1], item[0]['date']))


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
    if DAY_PATTERN.match(text):
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


def _has_compact_event_keyword(compact):
    return any(
        keyword in compact
        for keyword in [
            '신정',
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
            '어린이날',
            '근로자의날',
            '부처님오신날',
            '현충일',
            '온라인위크',
            '온라인워크',
            '관통프로젝트',
            '관통PJT',
            '경진대회',
            '지방선거',
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
        return '과목평가/월말평가'
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
    seen = set()
    deduped = []
    for candidate in candidates:
        if _is_weekend_false_positive(candidate):
            continue
        key = (candidate.title, candidate.event_date, candidate.event_type)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


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
    if any(keyword in title for keyword in ['신정', '어린이날', '근로자의 날', '부처님', '현충일', '지방선거']) or any(
        keyword in compact for keyword in ['신정', '어린이날', '근로자의날', '부처님', '현충일', '지방선거']
    ):
        return 'holiday'
    if any(keyword in title for keyword in ['SSAFY DAY', '입학식', '밋업']) or any(
        keyword in compact for keyword in ['SSAFYDAY', '입학식', '밋업']
    ):
        return 'event'
    return 'notice'
