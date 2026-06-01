CANONICAL_TRACKS = (
    ('python', 'Python'),
    ('java_non_major', 'Java비전공'),
    ('java_major', 'Java전공'),
    ('embedded', 'Embedded'),
    ('mobile', 'Mobile'),
    ('embedded_robot', 'Embedded Robot'),
    ('data', 'Data'),
    ('meister', '마이스터고'),
)

COMMON_TRACK_KEY = 'all'
COMMON_TRACK_DISPLAY = 'All'
COMMON_TRACK_VALUES = {'', COMMON_TRACK_KEY, 'common', 'global', '전체', '공통', 'all_tracks'}

TRACK_DISPLAY_BY_KEY = dict(CANONICAL_TRACKS)

TRACK_ALIASES = {
    'python': 'python',
    'Python': 'python',
    'java비전공': 'java_non_major',
    'Java비전공': 'java_non_major',
    'Java(비전공)': 'java_non_major',
    '비전공': 'java_non_major',
    'java': 'java_major',
    'Java': 'java_major',
    'java전공': 'java_major',
    'Java전공': 'java_major',
    'Java(전공)': 'java_major',
    '전공': 'java_major',
    'embedded': 'embedded',
    'Embedded': 'embedded',
    'mobile': 'mobile',
    'Mobile': 'mobile',
    'embedded robot': 'embedded_robot',
    'Embedded Robot': 'embedded_robot',
    'data': 'data',
    'Data': 'data',
    'meister': 'meister',
    '마이스터고': 'meister',
}


def canonical_track_keys():
    return [key for key, _display in CANONICAL_TRACKS]


def canonical_track_display(key):
    normalized = normalize_track_key(key)
    return TRACK_DISPLAY_BY_KEY.get(normalized, str(key or '').strip())


def normalize_track_key(value):
    text = str(value or '').strip()
    if not text:
        return ''
    lowered_text = text.lower().replace('-', '_').replace(' ', '_')
    if lowered_text in {value.lower().replace('-', '_').replace(' ', '_') for value in COMMON_TRACK_VALUES}:
        return COMMON_TRACK_KEY
    lowered = text.lower().replace('-', '_').replace(' ', '_')
    alias_by_lower = {
        str(alias).lower().replace('-', '_').replace(' ', '_'): key
        for alias, key in TRACK_ALIASES.items()
    }
    return alias_by_lower.get(lowered) or TRACK_ALIASES.get(text, text)


def is_common_track_key(value):
    normalized = normalize_track_key(value)
    return normalized == COMMON_TRACK_KEY or (not normalized and str(value or '').strip() == '')


def common_track_metadata():
    return {
        'track': COMMON_TRACK_KEY,
        'track_key': COMMON_TRACK_KEY,
        'track_display': COMMON_TRACK_DISPLAY,
        'is_common': True,
    }


def track_key_from_text(text):
    source = str(text or '')
    for alias in sorted(TRACK_ALIASES, key=len, reverse=True):
        if alias and alias.lower() in source.lower():
            return TRACK_ALIASES[alias]
    return ''
