import re
from difflib import SequenceMatcher


STATUS_PAREN_PATTERN = re.compile(r'\s*\((?:예정|수정|변경|안내|공지)\)?')
DANGLING_SHORT_PAREN_PATTERN = re.compile(r'\s*\([^)]{1,2}$')
COMPARISON_PREFIX_PATTERN = re.compile(r'^\s*\[(?:학습|공지|안내)\]\s*')
WRAPPER_PHRASES = (
    '트랙 시간표',
    '주차 시간표',
    '학습 주차',
    '커리큘럼',
)


def normalize_event_title_for_dedupe(title):
    text = _normalize_spaces(title)
    text = COMPARISON_PREFIX_PATTERN.sub('', text)
    text = STATUS_PAREN_PATTERN.sub('', text)
    text = DANGLING_SHORT_PAREN_PATTERN.sub('', text)
    for phrase in WRAPPER_PHRASES:
        text = text.replace(phrase, ' ')
    text = _normalize_spaces(text)
    return _compact_for_compare(text)


def is_wrapper_schedule_title(title, metadata=None):
    metadata = metadata or {}
    text = _normalize_spaces(title)
    compact_title = _compact_for_compare(text)
    source_title = metadata.get('source_title') or metadata.get('raw_title') or ''
    compact_source = _compact_for_compare(source_title)

    if not compact_title:
        return True
    if _has_wrapper_phrase(text):
        return True
    if _looks_like_timetable_parser(metadata) and _looks_like_source_title(compact_title, compact_source):
        return True
    return False


def _looks_like_timetable_parser(metadata):
    parser = str(metadata.get('parser') or metadata.get('parser_type') or '')
    return parser in {'ocr_timetable_grid', 'timetable_grid'}


def _looks_like_source_title(compact_title, compact_source):
    if not compact_source:
        return False
    if compact_title == compact_source:
        return True
    if len(compact_title) >= 8 and compact_title in compact_source:
        return True
    return SequenceMatcher(None, compact_title, compact_source).ratio() >= 0.8


def _has_wrapper_phrase(title):
    if any(phrase in title for phrase in WRAPPER_PHRASES):
        return True
    return bool(re.search(r'(?:DATA|PYTHON|JAVA|마이스터고|임베디드|모바일)?\s*트랙\s*시간표', title, re.I))


def _normalize_spaces(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _compact_for_compare(value):
    return re.sub(r'[\s.()\-_/\]:\[\]]+', '', str(value or '')).upper()
