import json


SOURCE_TYPES = {'notice', 'academic_rule', 'mentoring_notice'}
CATEGORIES = {'all', 'study', 'exam', 'mentoring', 'etc'}
TRACKS = {'python', 'java', 'embedded', 'mobile', 'common'}

CATEGORY_ALIASES = {
    'learning': 'study',
    'education': 'study',
    'evaluation': 'exam',
    'test': 'exam',
    'rule': 'etc',
    'other': 'etc',
}

EXAM_KEYWORDS = ('평가', '시험', '월말평가', '과목평가', '역량테스트')
STUDY_KEYWORDS = ('학습', '강의', '과제', '프로젝트', '커리큘럼', '보충', '라이브')
TRACK_KEYWORDS = {
    'python': ('python', '파이썬'),
    'java': ('java', '자바'),
    'embedded': ('embedded', '임베디드'),
    'mobile': ('mobile', '모바일'),
    'common': ('공통', '전체', '공통반', '전공통합'),
}


def normalize_notice_category(category):
    normalized = str(category or '').strip().lower()
    return CATEGORY_ALIASES.get(normalized, normalized)


def infer_notice_category(raw):
    source_type = str(getattr(raw, 'source_type', '') or '').strip().lower()
    if source_type == 'mentoring_notice':
        return 'mentoring'
    if source_type == 'academic_rule':
        return 'etc'

    metadata_value = normalize_notice_category(_metadata_value(raw, 'category'))
    if metadata_value in CATEGORIES and metadata_value != 'all':
        return metadata_value

    text = _normalization_text(raw).lower()
    if any(keyword in text for keyword in EXAM_KEYWORDS):
        return 'exam'
    if any(keyword in text for keyword in STUDY_KEYWORDS):
        return 'study'
    return 'etc'


def infer_notice_track(raw):
    source_type = str(getattr(raw, 'source_type', '') or '').strip().lower()
    if source_type in {'mentoring_notice', 'academic_rule'}:
        return 'common'

    metadata_value = _metadata_value(raw, 'track')
    if metadata_value in TRACKS:
        return metadata_value

    audience = (getattr(raw, 'metadata_json', None) or {}).get('audience') or {}
    audience_track = str(audience.get('track') or '').strip().lower()
    if audience_track in TRACKS:
        return audience_track

    text = _normalization_text(raw).lower()
    for track, keywords in TRACK_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return track
    return 'common'


def notice_matches_search(raw, keyword):
    if not keyword:
        return True
    return keyword.lower() in _normalization_text(raw).lower()


def _metadata_value(raw, key):
    metadata = getattr(raw, 'metadata_json', None) or {}
    value = metadata.get(key)
    return str(value).strip().lower() if value is not None else ''


def _normalization_text(raw):
    metadata = getattr(raw, 'metadata_json', None) or {}
    values = [
        getattr(raw, 'title', ''),
        getattr(raw, 'raw_text', ''),
        getattr(raw, 'raw_html', ''),
        json.dumps(metadata, ensure_ascii=False),
    ]
    return '\n'.join(value for value in values if value)
