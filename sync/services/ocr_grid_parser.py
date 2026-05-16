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


@dataclass
class GridParseDebug:
    ocr_box_count: int = 0
    normalized_box_count: int = 0
    date_cell_count: int = 0
    candidate_count: int = 0
    used_grid_parser: bool = False
    reason: str = ''
    candidates: list = None

    def as_dict(self):
        return {
            'ocr_box_count': self.ocr_box_count,
            'normalized_box_count': self.normalized_box_count,
            'date_cell_count': self.date_cell_count,
            'candidate_count': self.candidate_count,
            'used_grid_parser': self.used_grid_parser,
            'reason': self.reason,
            'candidates': self.candidates or [],
        }


def parse_grid_schedule_candidates(ocr_boxes):
    debug = GridParseDebug(
        ocr_box_count=len(ocr_boxes or []),
        candidates=[],
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
    for month, section_boxes in month_sections:
        cells = _build_date_cells(month, section_boxes)
        debug.date_cell_count += len(cells)
        candidates.extend(_assign_events_to_cells(section_boxes, cells))

    candidates = _dedupe_candidates(candidates)
    debug.used_grid_parser = bool(candidates)
    debug.candidate_count = len(candidates)
    debug.reason = 'ok' if candidates else 'no_event_boxes_matched'
    debug.candidates = [
        {
            'title': candidate.title,
            'date': candidate.event_date.isoformat(),
            'event_type': candidate.event_type,
        }
        for candidate in candidates
    ]
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
        match = DAY_PATTERN.match(box['text'])
        if not match:
            inline_match = INLINE_DAY_PATTERN.match(box['text'])
            if not inline_match:
                continue
            day = int(inline_match.group('day'))
        else:
            day = int(match.group('day'))
        if _valid_day(month, day):
            day_boxes.append((day, box))

    day_boxes = _dedupe_day_boxes(day_boxes)
    if not day_boxes:
        return []

    columns = _cluster_centers([box['cx'] for _, box in day_boxes], max_gap=80)
    rows = _cluster_centers([box['cy'] for _, box in day_boxes], max_gap=55)
    cells = []
    for day, box in day_boxes:
        col_index = _nearest_index(columns, box['cx'])
        row_index = _nearest_index(rows, box['cy'])
        cells.append(
            {
                'day': day,
                'date': date(DEFAULT_YEAR, month, day),
                'row_index': row_index,
                'col_index': col_index,
                'x1': _lower_bound(columns, col_index),
                'x2': _upper_bound(columns, col_index),
                'y1': _row_top(rows, row_index, box),
                'y2': _row_bottom(rows, row_index, box),
            }
        )
    return cells


def _assign_events_to_cells(boxes, cells):
    candidates = []
    for box in boxes:
        title = _event_title_from_box(box['text'])
        if not title:
            continue
        cell = _find_cell_for_box(box, cells)
        if not cell:
            continue
        candidates.append(
            GridScheduleCandidate(
                title=title,
                event_date=cell['date'],
                event_type=_event_type(title),
                description='SSAFY OCR bounding box 달력 grid에서 추출한 일정',
            )
        )
    for cell in cells:
        for title in _event_titles_from_cell(boxes, cell):
            candidates.append(
                GridScheduleCandidate(
                    title=title,
                    event_date=cell['date'],
                    event_type=_event_type(title),
                    description='SSAFY OCR bounding box 달력 grid에서 추출한 일정',
                )
            )
    return candidates


def _event_titles_from_cell(boxes, cell):
    cell_boxes = [
        box for box in boxes
        if cell['x1'] <= box['cx'] <= cell['x2']
        and cell['y1'] <= box['cy'] <= cell['y2']
        and not _is_structural_box(box)
    ]
    titles = []
    for row in _group_rows(cell_boxes):
        phrase = ' '.join(box['text'] for box in sorted(row, key=lambda item: item['x1']))
        title = _event_title_from_box(phrase)
        if title:
            titles.append(title)
    return titles


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
    if any(keyword in text for keyword in EVENT_KEYWORDS):
        return _canonical_title(text)
    return ''


def _canonical_title(title):
    if '스타트캠프' in title and '15기' in title:
        return '15기 SW. AI 스타트캠프'
    if 'SW' in title and '역량' in title and '테스트' in title:
        return 'SW 역량테스트'
    return title[:255]


def _find_cell_for_box(box, cells):
    direct_matches = [
        cell for cell in cells
        if cell['x1'] <= box['cx'] <= cell['x2'] and cell['y1'] <= box['cy'] <= cell['y2']
    ]
    if direct_matches:
        return min(direct_matches, key=lambda cell: abs(cell['date'].day - _safe_int(box['text'], cell['date'].day)))

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


def _lower_bound(values, index):
    if index == 0:
        return float('-inf')
    return (values[index - 1] + values[index]) / 2


def _upper_bound(values, index):
    if index + 1 >= len(values):
        return float('inf')
    return (values[index] + values[index + 1]) / 2


def _row_top(rows, index, box):
    if index == 0:
        return box['y1'] - 8
    return (rows[index - 1] + rows[index]) / 2


def _row_bottom(rows, index, box):
    if index + 1 >= len(rows):
        return box['y2'] + 80
    return (rows[index] + rows[index + 1]) / 2


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
        key = (candidate.title, candidate.event_date, candidate.event_type)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _valid_day(month, day):
    try:
        date(DEFAULT_YEAR, month, day)
    except ValueError:
        return False
    return True


def _safe_int(value, default):
    try:
        return int(str(value).split()[0])
    except (TypeError, ValueError):
        return default


def _event_type(title):
    if any(keyword in title for keyword in ['평가', '월말평가', '과목평가', 'SW 역량테스트', '역량', '테스트']):
        return 'exam'
    if any(keyword in title for keyword in ['프로젝트', 'PJT', '경진대회']):
        return 'project'
    if any(keyword in title for keyword in ['강의', '특강', '캠프']):
        return 'lecture'
    if any(keyword in title for keyword in ['신정', '어린이날', '근로자의 날', '부처님', '현충일', '지방선거']):
        return 'holiday'
    if any(keyword in title for keyword in ['SSAFY DAY', '입학식', '밋업']):
        return 'event'
    return 'notice'
