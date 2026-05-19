import json


SOURCE_TYPES = {
    'notice',
    'academic_rule',
    'mentoring_notice',
    'curriculum',
    'learning_material',
    'quest',
    'faq',
}
CATEGORIES = {'all', 'study', 'exam', 'mentoring', 'etc'}
TRACKS = {'python', 'java', 'embedded', 'mobile', 'common'}

SOURCE_CATEGORY_MAP = {
    'academic_rule': 'etc',
    'mentoring_notice': 'mentoring',
    'curriculum': 'study',
    'learning_material': 'study',
    'quest': 'exam',
    'faq': 'etc',
}
CATEGORY_ALIASES = {
    'learning': 'study',
    'education': 'study',
    'evaluation': 'exam',
    'test': 'exam',
    'rule': 'etc',
    'other': 'etc',
}

EXAM_KEYWORDS = (
    'exam',
    'test',
    'evaluation',
    '\uc6d4\ub9d0\ud3c9\uac00',
    '\uacfc\ubaa9\ud3c9\uac00',
    '\uc5ed\ub7c9\ud14c\uc2a4\ud2b8',
    '\uc2dc\ud5d8',
    '\ud3c9\uac00',
)
STUDY_KEYWORDS = (
    'study',
    'lecture',
    'curriculum',
    'course',
    'learning',
    'project',
    '\ud559\uc2b5',
    '\uac15\uc758',
    '\uacfc\uc81c',
    '\ud504\ub85c\uc81d\ud2b8',
    '\ucee4\ub9ac\ud058\ub7fc',
    '\ubcf4\ucda9',
    '\ub77c\uc774\ube0c',
)
TRACK_KEYWORDS = {
    'python': ('python', '\ud30c\uc774\uc36c'),
    'java': ('java', '\uc790\ubc14'),
    'embedded': ('embedded', '\uc784\ubca0\ub514\ub4dc'),
    'mobile': ('mobile', '\ubaa8\ubc14\uc77c'),
    'common': ('common', '\uacf5\ud1b5', '\uc804\uccb4'),
}


def normalize_notice_category(category):
    normalized = str(category or '').strip().lower()
    return CATEGORY_ALIASES.get(normalized, normalized)


def infer_notice_category(raw):
    source_type = str(getattr(raw, 'source_type', '') or '').strip().lower()
    if source_type in SOURCE_CATEGORY_MAP:
        return SOURCE_CATEGORY_MAP[source_type]

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
    if source_type in {'mentoring_notice', 'academic_rule', 'faq'}:
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
