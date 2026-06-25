import re
import unicodedata
from difflib import SequenceMatcher


STATUS_PAREN_PATTERN = re.compile(r'\s*\((?:예정|수정|변경|안내|공지)\)?')
DANGLING_SHORT_PAREN_PATTERN = re.compile(r'\s*\([^)]{1,2}$')
COMPARISON_PREFIX_PATTERN = re.compile(r'^\s*\[(?:학습|공지|안내)\]\s*')
TIME_TOKEN_PATTERN = r'\d{1,2}\s*:\s*\d{2}'
LEADING_TIME_RANGE_PATTERN = re.compile(
    rf'^\s*{TIME_TOKEN_PATTERN}(?:\s*(?:~|-|to|부터)\s*{TIME_TOKEN_PATTERN})?\s*',
    re.IGNORECASE,
)
BRACKET_PREFIX_PATTERN = re.compile(r'^\s*\[[^\]]+\]\s*')
LIVE_BROADCAST_PATTERN = re.compile(r'^\s*\[?\s*Live\s*방송\s*\]?\s*', re.IGNORECASE)
NOISE_BRACKET_LABELS = {'학습', 'Live 방송', 'LIVE 방송'}
WRAPPER_PHRASES = (
    '트랙 시간표',
    '주차 시간표',
    '학습 주차',
    '커리큘럼',
)
MEANINGLESS_TITLE_COMPACTS = {
    '0012',
    'DB',
    'JS',
    '&A',
    'A',
    'SSAFY',
    'SAMSUNG',
    'SW',
    'AI',
    'AIACADEMY',
    'SAMSUNGSWAIACADEMY',
    'FOR',
    'YOUTH',
    'FORYOUTH',
}
DATE_FRAGMENT_PATTERN = re.compile(
    r'^\s*(?:\d{1,2}\s*(?:[./-]\s*\d{1,2}|월\s*\d{0,2}\s*일?|일)|\d{1,2}\s*월)\s*$'
)


def normalize_event_title_for_dedupe(title):
    text = unicodedata.normalize('NFKC', _normalize_spaces(title))
    text = COMPARISON_PREFIX_PATTERN.sub('', text)
    text = LIVE_BROADCAST_PATTERN.sub('', text)
    text = STATUS_PAREN_PATTERN.sub('', text)
    text = DANGLING_SHORT_PAREN_PATTERN.sub('', text)
    for phrase in WRAPPER_PHRASES:
        text = text.replace(phrase, ' ')
    text = _normalize_qna_for_compare(text)
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
    if _looks_like_timetable_parser(metadata) and _looks_like_source_title(
        compact_title,
        compact_source,
        source_title,
    ):
        return True
    return False


def normalize_schedule_display_title(title):
    """Return a short calendar-facing title while preserving the stored title."""
    original = _normalize_spaces(title)
    text = original
    if not text:
        return ''

    text = _strip_display_noise_prefixes(text)
    text = _normalize_learning_subject(text)
    text = _normalize_spaces(text).strip(' :-|[]()~')
    return text[:80] if text else original[:80]


def is_meaningless_schedule_title(title):
    text = _normalize_spaces(title)
    compact = re.sub(r'[\s\[\].:()_\-/~]+', '', text).upper()
    if not compact:
        return True
    if re.fullmatch(r'\d{1,2}\s*(?::\s*\d{2})?\s*(?:-|~|to|부터|부터\s*)\s*\d{1,2}\s*(?::\s*\d{2})?', text, re.I):
        return True
    if DATE_FRAGMENT_PATTERN.fullmatch(text):
        return True
    if compact in MEANINGLESS_TITLE_COMPACTS:
        return True
    if _looks_like_logo_or_slogan_fragment(compact):
        return True
    if len(compact) <= 2 and not _is_allowed_short_title(compact):
        return True
    return False


def _strip_display_noise_prefixes(text):
    for _ in range(4):
        previous = text
        text = LEADING_TIME_RANGE_PATTERN.sub('', text, count=1)
        text = _strip_display_bracket_prefix(text)
        text = LIVE_BROADCAST_PATTERN.sub('', text, count=1)
        if text == previous:
            break
    return text


def _strip_display_bracket_prefix(text):
    match = BRACKET_PREFIX_PATTERN.match(text)
    if not match:
        return text

    label = match.group(0).strip()[1:-1].strip()
    rest = text[match.end():]
    if label in NOISE_BRACKET_LABELS:
        return rest
    if label in {'실습 및 Q&A', '실습 Q&A', 'Q&A'}:
        return f'실습 Q&A {rest}'
    return text


def _normalize_learning_subject(text):
    text = _normalize_spaces(text)
    text = re.sub(r'\bJS\s+Basic\s+Syntax\s*\d*\b', 'Basic Syntax', text, flags=re.IGNORECASE)
    text = re.sub(r'\bBasic\s+Syntax\s*\d+\b', 'Basic Syntax', text, flags=re.IGNORECASE)
    text = text.replace('실습 및 Q&A', '실습 Q&A')
    return text


def _is_allowed_short_title(compact):
    return compact in {
        'SQL',
        'PJT',
        '평가',
        '특강',
        '중식',
        '설날',
    }


def _looks_like_logo_or_slogan_fragment(compact):
    return compact in {
        'SAMSUNGSW',
        'SWAIA',
        'SWAIACADEMY',
    } or compact.startswith('SAMSUNGSWAI') or compact.endswith('FORYOUTH')


def _looks_like_timetable_parser(metadata):
    parser = str(metadata.get('parser') or metadata.get('parser_type') or '')
    return parser in {'ocr_timetable_grid', 'timetable_grid'}


def _looks_like_source_title(compact_title, compact_source, source_title=''):
    if not compact_source:
        return False
    if compact_title == compact_source:
        return True
    if not _has_wrapper_phrase(source_title):
        return False
    return SequenceMatcher(None, compact_title, compact_source).ratio() >= 0.9


def _has_wrapper_phrase(title):
    if any(phrase in title for phrase in WRAPPER_PHRASES):
        return True
    return bool(re.search(r'(?:DATA|PYTHON|JAVA|마이스터고|임베디드|모바일)?\s*트랙\s*시간표', title, re.I))


def _normalize_spaces(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _normalize_qna_for_compare(value):
    text = str(value or '')
    text = re.sub(r'\bQ\s*&\s*A\b|\bQNA\b|\bQ\s+AND\s+A\b', 'QnA', text, flags=re.IGNORECASE)
    text = re.sub(r'실습\s*(?:및)?\s*QnA|QnA\s*실습\s*QnA', '실습 및 QnA', text, flags=re.IGNORECASE)
    text = re.sub(r'관통\s*PJT\s+실습\s*및\s*QnA', '관통 PJT: 실습 및 QnA', text, flags=re.IGNORECASE)
    return text


def _compact_for_compare(value):
    return re.sub(r'[\s.()\-_/\]:\[\]]+', '', str(value or '')).upper()
