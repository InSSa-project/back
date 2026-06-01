import hashlib
import re

from django.utils import timezone

from sync.services.tracks import COMMON_TRACK_KEY, normalize_track_key


STATUS_WORD_PATTERN = re.compile(r'\b(?:예정|안내|수정|변경)\b', re.IGNORECASE)
PREFIX_PATTERN = re.compile(r'^\s*\[(?:공지|학습|평가|안내)\]\s*')
SPECIAL_PATTERN = re.compile(r'[^\w가-힣]+', re.UNICODE)


def ensure_raw_identity_metadata(raw_data, save=False):
    metadata = dict(raw_data.metadata_json or {})
    changed = False
    content_hash = normalized_content_hash_for_raw(raw_data)
    ocr_hash = ocr_text_hash_for_raw(raw_data)
    published_at = source_published_at(raw_data)

    for key, value in {
        'normalized_content_hash': content_hash,
        'ocr_text_hash': ocr_hash,
        'source_published_at': published_at,
    }.items():
        if value and metadata.get(key) != value:
            metadata[key] = value
            changed = True

    if changed:
        raw_data.metadata_json = metadata
        if save:
            raw_data.save(update_fields=['metadata_json'])
    return metadata


def normalized_content_hash_for_raw(raw_data):
    metadata = raw_data.metadata_json or {}
    raw_json = metadata.get('raw_json') or metadata.get('raw') or ''
    source = '\n'.join(
        [
            str(raw_data.title or ''),
            str(raw_data.raw_text or ''),
            str(raw_data.raw_html or ''),
            str(raw_json or ''),
        ]
    )
    return _hash_text(normalize_content_text(source))


def ocr_text_hash_for_raw(raw_data):
    metadata = raw_data.metadata_json or {}
    ocr_text = metadata.get('ocr_text') or ''
    raw_text = raw_data.raw_text or ''
    if '[OCR_TEXT]' in raw_text:
        ocr_text = raw_text.split('[OCR_TEXT]', 1)[1]
    return _hash_text(normalize_content_text(ocr_text))


def normalize_content_text(value):
    text = PREFIX_PATTERN.sub('', str(value or ''))
    text = STATUS_WORD_PATTERN.sub(' ', text)
    text = SPECIAL_PATTERN.sub(' ', text)
    text = re.sub(r'\s+', ' ', text).strip().lower()
    return text


def source_published_at(raw_data):
    metadata = raw_data.metadata_json or {}
    for key in ('source_published_at', 'published_at', 'created_at', 'posted_at'):
        value = metadata.get(key)
        if value:
            return str(value)[:10]
    return ''


def extracted_date_range_for_schedule(schedule):
    metadata = getattr(schedule, 'metadata_json', None) or {}
    if metadata.get('extracted_date_range'):
        return metadata.get('extracted_date_range')
    start_date = timezone.localdate(schedule.start_at).isoformat()
    end_date = timezone.localdate(schedule.end_at).isoformat() if getattr(schedule, 'end_at', None) else start_date
    return f'{start_date}..{end_date}'


def date_mapping_source_for_schedule(schedule):
    metadata = getattr(schedule, 'metadata_json', None) or {}
    current = metadata.get('date_mapping_source')
    parser_type = metadata.get('parser_type') or metadata.get('parser')
    if current in {'ocr_header', 'fallback_week', 'unknown'}:
        return current
    if parser_type == 'text_date_range':
        return 'text_date_range'
    if parser_type in {'calendar_text', 'text_date', 'evaluation_notice'}:
        return 'ocr_text_date'
    if current:
        return current
    return 'unknown'


def generated_identity_key(raw_data, schedule):
    raw_metadata = ensure_raw_identity_metadata(raw_data, save=False)
    schedule_metadata = getattr(schedule, 'metadata_json', None) or {}
    return (
        str(raw_data.id),
        str(raw_data.id),
        raw_metadata.get('normalized_content_hash') or '',
        raw_metadata.get('ocr_text_hash') or '',
        schedule_metadata.get('extracted_date_range') or extracted_date_range_for_schedule(schedule),
        schedule.event_type,
        _track_from_metadata(schedule_metadata),
    )


def event_identity_key(event):
    metadata = event.metadata_json or {}
    return (
        event.source_id or str(event.raw_data_id or metadata.get('raw_data_id') or ''),
        str(event.raw_data_id or metadata.get('raw_data_id') or ''),
        metadata.get('normalized_content_hash') or '',
        metadata.get('ocr_text_hash') or '',
        metadata.get('extracted_date_range') or f'{timezone.localdate(event.start_at).isoformat()}..{timezone.localdate(event.end_at).isoformat()}',
        event.event_type,
        _track_from_metadata(metadata),
    )


def choose_representative_title(titles):
    cleaned = [str(title or '').strip() for title in titles if str(title or '').strip()]
    if not cleaned:
        return ''
    return sorted(cleaned, key=lambda value: (len(value), value))[0]


def _track_from_metadata(metadata):
    audience = metadata.get('audience') or {}
    return normalize_track_key(
        metadata.get('track_key')
        or metadata.get('track')
        or audience.get('track_key')
        or audience.get('track')
        or COMMON_TRACK_KEY
    )


def _hash_text(text):
    if not text:
        return ''
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]
