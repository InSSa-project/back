import re


AUDIENCE_METADATA_KEYS = ('track', 'generation', 'class_number', 'campus')


def build_event_metadata_from_raw_data(raw_data):
    metadata = {'audience': {key: None for key in AUDIENCE_METADATA_KEYS}}
    if not raw_data:
        return metadata

    raw_metadata = raw_data.metadata_json or {}
    raw_audience = raw_metadata.get('audience') or {}
    for key in AUDIENCE_METADATA_KEYS:
        metadata['audience'][key] = raw_audience.get(key) or raw_metadata.get(key)

    source_text = f'{raw_data.title or ""}\n{raw_data.raw_text or ""}'
    metadata['audience']['generation'] = metadata['audience']['generation'] or _infer_generation(source_text)
    metadata['audience']['track'] = metadata['audience']['track'] or _infer_track(source_text)
    metadata['audience']['class_number'] = metadata['audience']['class_number'] or _infer_class_number(source_text)
    metadata['audience']['campus'] = metadata['audience']['campus'] or _infer_campus(source_text)
    return metadata


def filter_events_for_user_profile(events, profile):
    if profile is None:
        return events

    filtered = []
    for event in events:
        audience = (event.metadata_json or {}).get('audience') or {}
        if _matches_profile_audience(audience, profile):
            filtered.append(event)
    return filtered


def _matches_profile_audience(audience, profile):
    for key in AUDIENCE_METADATA_KEYS:
        event_value = audience.get(key)
        profile_value = getattr(profile, key, None)
        if event_value and profile_value and str(event_value) != str(profile_value):
            return False
    return True


def _infer_generation(text):
    match = re.search(r'(?P<generation>\d{1,2})\s*기', text or '')
    return int(match.group('generation')) if match else None


def _infer_track(text):
    has_sw = bool(re.search(r'\bSW\b', text or '', flags=re.IGNORECASE))
    has_ai = bool(re.search(r'\bAI\b', text or '', flags=re.IGNORECASE))
    if has_sw and has_ai:
        return 'SW/AI'
    if has_sw:
        return 'SW'
    if has_ai:
        return 'AI'
    return None


def _infer_class_number(text):
    match = re.search(r'(?P<class_number>\d{1,2})\s*반', text or '')
    return int(match.group('class_number')) if match else None


def _infer_campus(text):
    for campus in ['서울', '대전', '광주', '구미', '부울경']:
        if campus in (text or ''):
            return campus
    return None
