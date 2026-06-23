import logging
import re
from datetime import timedelta

from django.utils import timezone

from schedules.utils import is_wrapper_schedule_title
from sync.management.commands.seed_korean_holidays import get_korean_holidays
from sync.services.schedule_identity import (
    date_mapping_source_for_schedule,
    ensure_raw_identity_metadata,
    extracted_date_range_for_schedule,
)
from sync.services.tracks import COMMON_TRACK_KEY, common_track_metadata, normalize_track_key


AUDIENCE_METADATA_KEYS = ('track', 'generation', 'class_number', 'campus')
CALENDAR_NOTICE_SOURCE_TYPES = {'notice', 'ssafy_notice', 'announcement'}
CALENDAR_HOLIDAY_SOURCE_TYPES = {'national_holiday', 'holiday'}
CALENDAR_MANUAL_SOURCE_TYPES = {'manual'}
CALENDAR_HIDDEN_SOURCE_TYPES = {
    'academic_rule',
    'academic_rules',
    'geeknews',
    'mentor',
    'mentor_notice',
    'mentor_story',
    'mentoring',
    'mentoring_notice',
    'mentoring_qna',
}
CALENDAR_HIDDEN_EVENT_TYPES = {'mentoring'}
CALENDAR_HIDDEN_URL_MARKERS = ('mento', 'mentor')
logger = logging.getLogger(__name__)


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


def build_generated_event_metadata(raw_data, schedule):
    metadata = build_event_metadata_from_raw_data(raw_data)
    raw_audience = dict(metadata.get('audience') or {})
    metadata.update(getattr(schedule, 'metadata_json', None) or {})
    metadata['audience'] = {**raw_audience, **(metadata.get('audience') or {})}
    _normalize_generated_track_metadata(metadata)
    if raw_data:
        raw_metadata = ensure_raw_identity_metadata(raw_data, save=False)
        metadata['raw_data_id'] = raw_data.id
        metadata['source_title'] = raw_data.title or ''
        metadata['source_url'] = raw_data.source_url or ''
        metadata.setdefault('normalized_content_hash', raw_metadata.get('normalized_content_hash'))
        metadata.setdefault('ocr_text_hash', raw_metadata.get('ocr_text_hash'))
        metadata.setdefault('source_published_at', raw_metadata.get('source_published_at'))
    metadata['date_mapping_source'] = date_mapping_source_for_schedule(schedule)
    metadata.setdefault('extracted_date_range', extracted_date_range_for_schedule(schedule))
    metadata.setdefault('extracted_dates', [timezone.localdate(schedule.start_at).isoformat()])
    metadata.setdefault('confidence', 0.5 if metadata.get('date_mapping_source') in {'fallback_week', 'unknown'} else 0.85)
    warnings = validate_generated_schedule(raw_data, schedule)
    if warnings:
        metadata['parser_warnings'] = warnings
    return metadata


def _normalize_generated_track_metadata(metadata):
    audience = metadata.setdefault('audience', {})
    explicit_track = metadata.get('track_key') or metadata.get('track')
    normalized_track = normalize_track_key(explicit_track)
    if normalized_track:
        metadata['track_key'] = normalized_track
        metadata.setdefault('track', normalized_track)
        metadata['is_common'] = normalized_track == COMMON_TRACK_KEY or bool(metadata.get('is_common'))
    else:
        metadata.update(common_track_metadata())

    if metadata.get('is_common'):
        metadata['track_key'] = COMMON_TRACK_KEY
        metadata['track'] = COMMON_TRACK_KEY
        audience['track_key'] = COMMON_TRACK_KEY
        audience['track'] = COMMON_TRACK_KEY
    else:
        audience['track_key'] = metadata.get('track_key')
        audience['track'] = metadata.get('track_key') or metadata.get('track')


def validate_generated_schedule(raw_data, schedule):
    warnings = []
    source_title = str(getattr(raw_data, 'title', '') or '').strip()
    title = str(getattr(schedule, 'title', '') or '').strip()
    parser = (getattr(schedule, 'metadata_json', None) or {}).get('parser')

    if is_wrapper_schedule_title(title, {'parser': parser, 'source_title': source_title}):
        warnings.append('timetable_title_equals_source_title')

    if getattr(schedule, 'end_at', None) and getattr(schedule, 'start_at', None) and schedule.end_at <= schedule.start_at:
        warnings.append('non_positive_duration')
    if _is_generated_class_on_korean_holiday(schedule, parser):
        warnings.append('generated_class_on_korean_holiday')
    if _is_timetable_source_period_mismatch(source_title, schedule):
        warnings.append('source_title_period_mismatch')

    for warning in warnings:
        logger.warning(
            'schedule_parser_warning raw_data_id=%s parser=%s title=%r source_title=%r warning=%s',
            getattr(raw_data, 'id', None),
            parser,
            title,
            source_title,
            warning,
        )
    return warnings


def is_blocking_generated_schedule_warning(warning):
    return warning in {'timetable_title_equals_source_title', 'non_positive_duration', 'source_title_period_mismatch'}


def _is_generated_class_on_korean_holiday(schedule, parser):
    if getattr(schedule, 'event_type', '') == 'holiday':
        return False
    if parser not in {'ocr_timetable_grid', 'timetable_grid'}:
        return False
    start_at = getattr(schedule, 'start_at', None)
    if not start_at:
        return False
    event_date = timezone.localdate(start_at)
    try:
        holidays = {holiday_date for _title, holiday_date in get_korean_holidays(event_date.year)}
    except ValueError:
        return False
    return event_date in holidays


def _is_timetable_source_period_mismatch(source_title, schedule):
    metadata = getattr(schedule, 'metadata_json', None) or {}
    parser_type = metadata.get('parser_type') or metadata.get('parser')
    if parser_type not in {'timetable_grid', 'ocr_timetable_grid'}:
        return False
    start_at = getattr(schedule, 'start_at', None)
    if not start_at:
        return False
    source_text = str(source_title or '')
    numbers = re.findall(r'\d{1,2}', source_text)
    if len(numbers) < 2:
        return False
    event_date = timezone.localdate(start_at)
    source_month = int(numbers[0])
    source_week = int(numbers[1])
    if event_date.month != source_month:
        return True
    expected_week = _month_week_index(event_date)
    return expected_week is not None and expected_week != source_week


def _month_week_index(event_date):
    first_day = event_date.replace(day=1)
    days_until_monday = (7 - first_day.weekday()) % 7
    first_monday = first_day + timedelta(days=days_until_monday)
    if event_date < first_monday:
        return None
    return ((event_date - first_monday).days // 7) + 1


def filter_calendar_visible_events(events):
    events = list(events)
    metadata_raw_by_id = _metadata_raw_data_map(events)
    return [
        event
        for event in events
        if is_calendar_visible_event(event, metadata_raw_by_id=metadata_raw_by_id)
    ]


def is_calendar_visible_event(event, metadata_raw_by_id=None):
    metadata = getattr(event, 'metadata_json', None) or {}
    source_types = _calendar_event_source_types(event, metadata, metadata_raw_by_id or {})
    source_urls = _calendar_event_source_urls(event, metadata, metadata_raw_by_id or {})
    event_type = _normalize_source_value(getattr(event, 'event_type', ''))

    if _is_user_created_calendar_event(event, source_types):
        return True
    if event_type in CALENDAR_HIDDEN_EVENT_TYPES:
        return False
    if any(source_type in CALENDAR_HIDDEN_SOURCE_TYPES for source_type in source_types):
        return False
    if any(_is_hidden_calendar_url(source_url) for source_url in source_urls):
        return False
    if event_type == 'holiday':
        return any(source_type in CALENDAR_HOLIDAY_SOURCE_TYPES for source_type in source_types)
    if any(source_type in CALENDAR_HOLIDAY_SOURCE_TYPES for source_type in source_types):
        return True
    return any(source_type in CALENDAR_NOTICE_SOURCE_TYPES for source_type in source_types)


def _metadata_raw_data_map(events):
    raw_data_ids = set()
    for event in events:
        metadata = getattr(event, 'metadata_json', None) or {}
        raw_data_id = metadata.get('raw_data_id')
        if raw_data_id and not getattr(event, 'raw_data_id', None):
            raw_data_ids.add(raw_data_id)
    if not raw_data_ids:
        return {}

    try:
        from sync.models import RawSsafyData

        return RawSsafyData.objects.in_bulk(raw_data_ids)
    except (TypeError, ValueError):
        return {}


def _calendar_event_source_types(event, metadata, metadata_raw_by_id):
    source_types = {
        _normalize_source_value(getattr(event, 'source_type', '')),
        _normalize_source_value(metadata.get('source_type')),
        _normalize_source_value(metadata.get('raw_source_type')),
    }
    raw_data = getattr(event, 'raw_data', None)
    if raw_data is not None:
        source_types.add(_normalize_source_value(getattr(raw_data, 'source_type', '')))

    metadata_raw_data = _metadata_raw_data(metadata, metadata_raw_by_id)
    if metadata_raw_data is not None:
        source_types.add(_normalize_source_value(getattr(metadata_raw_data, 'source_type', '')))
    return {source_type for source_type in source_types if source_type}


def _calendar_event_source_urls(event, metadata, metadata_raw_by_id):
    source_urls = {
        str(metadata.get('source_url') or ''),
        str(metadata.get('raw_source_url') or ''),
    }
    raw_data = getattr(event, 'raw_data', None)
    if raw_data is not None:
        source_urls.add(str(getattr(raw_data, 'source_url', '') or ''))

    metadata_raw_data = _metadata_raw_data(metadata, metadata_raw_by_id)
    if metadata_raw_data is not None:
        source_urls.add(str(getattr(metadata_raw_data, 'source_url', '') or ''))
    return {source_url for source_url in source_urls if source_url}


def _metadata_raw_data(metadata, metadata_raw_by_id):
    raw_data_id = metadata.get('raw_data_id')
    if not raw_data_id:
        return None
    try:
        return metadata_raw_by_id.get(raw_data_id) or metadata_raw_by_id.get(int(raw_data_id))
    except (TypeError, ValueError):
        return None


def _is_user_created_calendar_event(event, source_types):
    return bool(
        getattr(event, 'owner_id', None)
        or any(source_type in CALENDAR_MANUAL_SOURCE_TYPES for source_type in source_types)
        or _normalize_source_value(getattr(event, 'event_type', '')) == 'personal'
    )


def _is_hidden_calendar_url(source_url):
    normalized_url = str(source_url or '').lower()
    return any(marker in normalized_url for marker in CALENDAR_HIDDEN_URL_MARKERS)


def _normalize_source_value(value):
    return str(value or '').strip().lower()


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
        if key == 'track':
            event_track = normalize_track_key(event_value)
            profile_track = normalize_track_key(profile_value)
            if event_track == COMMON_TRACK_KEY or not event_track:
                continue
            if profile_track and event_track != profile_track:
                return False
            continue
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
