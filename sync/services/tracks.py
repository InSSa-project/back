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

# Aliases are sorted longest-first in track_key_from_text so more specific
# entries (e.g. "Java 비전공") are checked before shorter ones (e.g. "Java").
TRACK_ALIASES = {
    # Python
    'python': 'python',
    'Python': 'python',
    '파이썬': 'python',
    # Java 비전공 — must be listed before plain "java" entries
    'java비전공': 'java_non_major',
    'Java비전공': 'java_non_major',
    'java 비전공': 'java_non_major',
    'Java 비전공': 'java_non_major',
    'Java(비전공)': 'java_non_major',
    '비전공': 'java_non_major',
    # Java 전공
    'java전공': 'java_major',
    'Java전공': 'java_major',
    'java 전공': 'java_major',
    'Java 전공': 'java_major',
    'Java(전공)': 'java_major',
    '전공': 'java_major',
    # Java alone → java_major (fallback; "비전공" above takes priority via length sort)
    'java': 'java_major',
    'Java': 'java_major',
    # Embedded Robot — before plain "embedded"
    'embedded robot': 'embedded_robot',
    'Embedded Robot': 'embedded_robot',
    '임베디드 로봇': 'embedded_robot',
    '임베디드로봇': 'embedded_robot',
    # Embedded
    'embedded': 'embedded',
    'Embedded': 'embedded',
    '임베디드': 'embedded',
    # Mobile
    'mobile': 'mobile',
    'Mobile': 'mobile',
    '모바일': 'mobile',
    # Data
    'data': 'data',
    'Data': 'data',
    '데이터': 'data',
    # Meister
    'meister': 'meister',
    'Meister': 'meister',
    '마이스터고': 'meister',
    '마이스터': 'meister',
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
    if lowered_text in {v.lower().replace('-', '_').replace(' ', '_') for v in COMMON_TRACK_VALUES}:
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
    """Extract canonical track key by searching for aliases in text (case-insensitive).

    More-specific aliases (longer strings) take priority because the list is
    sorted by descending length before iteration.  For example "Java 비전공"
    is matched before the shorter "Java" alias.
    """
    source = str(text or '')
    source_lower = source.lower()
    for alias in sorted(TRACK_ALIASES, key=len, reverse=True):
        if alias and alias.lower() in source_lower:
            return TRACK_ALIASES[alias]
    return ''


def classify_notice_track(title, metadata=None):
    """Classify a notice into a canonical track.

    Priority:
    1. Explicit ``is_common`` flag in metadata.
    2. Explicit ``track_key`` / ``track`` stored in metadata or audience sub-dict.
    3. Title / text inference via :func:`track_key_from_text`.
    4. Default to common when no specific track can be determined.

    Returns a dict with keys ``track_key`` (str) and ``is_common`` (bool).
    """
    meta = metadata or {}

    # 1. Explicit common flag
    if meta.get('is_common') is True or meta.get('is_global') is True:
        return {'track_key': COMMON_TRACK_KEY, 'is_common': True}

    # 2. Stored track value (metadata or audience sub-dict)
    audience = meta.get('audience') or {}
    raw_track = (
        meta.get('track_key')
        or audience.get('track_key')
        or meta.get('track')
        or audience.get('track')
    )
    if raw_track:
        normalized = normalize_track_key(raw_track)
        if normalized == COMMON_TRACK_KEY or str(raw_track).strip().lower() in COMMON_TRACK_VALUES:
            return {'track_key': COMMON_TRACK_KEY, 'is_common': True}
        if normalized:
            return {'track_key': normalized, 'is_common': False}

    # 3. Infer from title
    inferred = track_key_from_text(str(title or ''))
    if inferred and inferred != COMMON_TRACK_KEY:
        return {'track_key': inferred, 'is_common': False}

    # 4. Default to common
    return {'track_key': COMMON_TRACK_KEY, 'is_common': True}
