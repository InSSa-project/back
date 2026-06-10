from collections import Counter, defaultdict
from datetime import datetime

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils.dateparse import parse_date
from django.utils import timezone

from schedules.models import ScheduleEvent
from schedules.utils import is_meaningless_schedule_title, normalize_event_title_for_dedupe
from sync.models import RawSsafyData
from sync.services.calendar_quality import (
    build_month_quality_report,
    format_count_dict,
    format_daily_counts,
    format_empty_weekdays,
    format_suspicious_events,
    is_generated_event,
    is_manual_event,
    parse_month_option,
)
from sync.services.reparse_service import reparse_raw_data_to_events
from sync.services.tracks import COMMON_TRACK_KEY, canonical_track_keys, normalize_track_key

MIN_HEALTHY_REPARSE_TARGET_COUNT = 3


class Command(BaseCommand):
    help = 'Print ScheduleEvent and RawSsafyData counts useful for calendar import debugging.'

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, default=2026, help='Calendar year to inspect.')
        parser.add_argument('--month', default='5', help='Calendar month to inspect. Accepts 5 or 2026-05.')
        parser.add_argument('--date', help='Print ScheduleEvent rows overlapping one date in YYYY-MM-DD format.')
        parser.add_argument('--event-id', type=int, help='Print source mapping details for one ScheduleEvent id.')
        parser.add_argument(
            '--reparse-source-type',
            default='notice',
            choices=['notice', 'academic_rule'],
            help='RawSsafyData source_type used for the reparse dry-run summary.',
        )
        parser.add_argument(
            '--no-reparse',
            action='store_true',
            help='Skip the reparse dry-run summary.',
        )

    def handle(self, *args, **options):
        if options.get('event_id'):
            _print_event_source_mapping(self.stdout, options['event_id'])
            return

        if options.get('date'):
            target_date = parse_date(options['date'])
            if target_date is None:
                self.stderr.write(self.style.ERROR('Invalid --date value. Use YYYY-MM-DD.'))
                return
            _print_events_for_date(self.stdout, target_date)
            return

        year, month = parse_month_option(options['month'], options['year'])
        start_at = _aware(datetime(year, month, 1))
        end_at = _next_month(start_at)
        quality_report = build_month_quality_report(year, month)

        self.stdout.write(f'schedule_total={ScheduleEvent.objects.count()}')
        self.stdout.write(
            f'schedule_{year}_{month:02d}_count='
            f'{ScheduleEvent.objects.filter(start_at__lt=end_at, end_at__gt=start_at).count()}'
        )
        all_events = list(ScheduleEvent.objects.all().only('raw_data_id', 'event_type', 'metadata_json'))
        self.stdout.write(f'generated_schedule_count={sum(1 for event in all_events if is_generated_event(event))}')
        self.stdout.write(f'manual_schedule_count={sum(1 for event in all_events if is_manual_event(event))}')
        self.stdout.write(f'monthly_schedule_counts={_monthly_schedule_counts()}')
        self.stdout.write(f'raw_total={RawSsafyData.objects.count()}')
        self.stdout.write(f'raw_source_type_counts={_source_type_counts()}')
        self.stdout.write(f'daily_event_counts={format_daily_counts(quality_report)}')
        self.stdout.write(f'month_generated_count={quality_report.generated_count}')
        self.stdout.write(f'month_manual_count={quality_report.manual_count}')
        self.stdout.write(f'month_holiday_count={quality_report.holiday_count}')
        self.stdout.write(f'track_event_counts={_track_event_counts(quality_report.events)}')
        self.stdout.write(f'common_all_event_count={_common_all_event_count(quality_report.events)}')
        self.stdout.write(f'unclassified_track_events={_unclassified_track_events(quality_report.events)}')
        self.stdout.write(f'track_filter_risk_events={_track_filter_risk_events(quality_report.events)}')
        self.stdout.write(f'common_candidate_events={_common_candidate_events(quality_report.events)}')
        self.stdout.write(f'evaluation_missing_tracks={_evaluation_missing_tracks(quality_report.events)}')
        self.stdout.write(f'source_mismatches={_source_mismatches(quality_report.events)}')
        self.stdout.write(f'meaningless_titles={_meaningless_titles(quality_report.events)}')
        self.stdout.write(f'duplicate_candidates={_duplicate_candidates(quality_report.events)}')
        self.stdout.write(f'skip_reason_counts={format_count_dict(quality_report.skip_reason_counts)}')
        self.stdout.write(f'suspicious_events={format_suspicious_events(quality_report.suspicious_events)}')
        self.stdout.write(f'suspicious_empty_weekdays={format_empty_weekdays(quality_report.suspicious_empty_weekdays)}')

        if options['no_reparse']:
            return

        queryset = RawSsafyData.objects.filter(source_type=options['reparse_source_type'])
        target_count = queryset.count()
        self.stdout.write(f'reparse_target_count={target_count}')
        if target_count < MIN_HEALTHY_REPARSE_TARGET_COUNT:
            self.stdout.write(
                self.style.WARNING(
                    'WARNING: reparse target RawSsafyData count is low. '
                    'Run crawl_ssafy_notices before reset/reparse if this is not a test database.'
                )
            )
        summary = reparse_raw_data_to_events(queryset, dry_run=True)
        self.stdout.write(
            'reparse_dry_run='
            f'raw_checked:{summary.raw_checked}|'
            f'candidate:{summary.candidate_count}|'
            f'created:{summary.created_count}|'
            f'skipped:{summary.skipped_count}|'
            f'duplicate:{summary.duplicate_skip_count}|'
            f'wrapper:{summary.wrapper_skip_count}|'
            f'validation:{summary.validation_skip_count}|'
            f'empty_title:{summary.empty_title_skip_count}|'
            f'no_schedule:{summary.no_schedule_count}|'
            f'failed:{summary.failed_count}'
        )
        if summary.created_count == 0 and summary.skipped_count:
            self.stdout.write(
                'reparse_skip_reasons='
                f'duplicate:{summary.duplicate_skip_count}|'
                f'wrapper:{summary.wrapper_skip_count}|'
                f'validation:{summary.validation_skip_count}|'
                f'empty_title:{summary.empty_title_skip_count}'
            )
        if summary.duplicate_skip_count:
            self.stdout.write(f'existing_duplicate_event_dates={_existing_event_dates()}')


def _aware(value):
    return timezone.make_aware(value, timezone.get_current_timezone())


def _next_month(value):
    if value.month == 12:
        return _aware(datetime(value.year + 1, 1, 1))
    return _aware(datetime(value.year, value.month + 1, 1))


def _source_type_counts():
    rows = RawSsafyData.objects.values('source_type').order_by('source_type').annotate(count=Count('id'))
    return '|'.join(f'{row["source_type"]}:{row["count"]}' for row in rows) or 'none'


def _monthly_schedule_counts():
    counts = {}
    for event in ScheduleEvent.objects.order_by('start_at').only('start_at'):
        key = timezone.localtime(event.start_at).strftime('%Y-%m')
        counts[key] = counts.get(key, 0) + 1
    return '|'.join(f'{key}:{counts[key]}' for key in sorted(counts)) or 'none'


def _existing_event_dates():
    dates = []
    for event in ScheduleEvent.objects.order_by('start_at', 'id')[:20]:
        dates.append(f'{timezone.localdate(event.start_at).isoformat()}:{event.title}')
    return '|'.join(dates) or 'none'


def _track_event_counts(events):
    counts = Counter()
    for event in events:
        track = _event_track_key(event)
        counts[track or 'unclassified'] += 1
    return '|'.join(f'{key}:{counts[key]}' for key in sorted(counts)) or 'none'


def _common_all_event_count(events):
    return sum(1 for event in events if _is_common_event(event))


def _unclassified_track_events(events):
    values = []
    for event in events:
        metadata = event.metadata_json or {}
        audience = metadata.get('audience') or {}
        has_any_track_value = any(
            str(value or '').strip()
            for value in [
                metadata.get('track_key'),
                metadata.get('track'),
                audience.get('track_key'),
                audience.get('track'),
            ]
        )
        if has_any_track_value or _is_common_event(event):
            continue
        values.append(_event_debug_label(event))
    return '|'.join(values[:30]) or 'none'


def _track_filter_risk_events(events):
    values = []
    for event in events:
        track = _event_track_key(event)
        if track in set(canonical_track_keys()) or _is_common_event(event):
            continue
        values.append(_event_debug_label(event))
    return '|'.join(values[:30]) or 'none'


def _common_candidate_events(events):
    values = []
    for event in events:
        if not _is_common_candidate(event):
            continue
        values.append(_event_debug_label(event))
    return '|'.join(values[:30]) or 'none'


def _evaluation_missing_tracks(events):
    required_tracks = set(canonical_track_keys())
    groups = defaultdict(set)
    labels = {}
    for event in events:
        if not _is_evaluation_event(event):
            continue
        key = (timezone.localdate(event.start_at), normalize_event_title_for_dedupe(event.title))
        track = _event_track_key(event)
        if track:
            groups[key].add(track)
        labels[key] = f'{key[0].isoformat()}:{event.title}'

    missing = []
    for key, tracks in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1])):
        absent = sorted(required_tracks - tracks)
        if absent:
            missing.append(f'{_safe(labels[key])}:missing={",".join(absent)}')
    return '|'.join(missing) or 'none'


def _source_mismatches(events):
    mismatches = []
    for event in events:
        metadata = event.metadata_json or {}
        if not event.raw_data_id:
            metadata_raw_data_id = metadata.get('raw_data_id')
            if metadata_raw_data_id:
                if RawSsafyData.objects.filter(pk=metadata_raw_data_id).exists():
                    mismatches.append(f'id={event.id}:unlinked_raw_data_id={metadata_raw_data_id}')
                else:
                    mismatches.append(f'id={event.id}:stale_raw_data_id={metadata_raw_data_id}')
            continue

        raw_data = event.raw_data
        metadata_raw_data_id = metadata.get('raw_data_id')
        metadata_source_url = metadata.get('source_url')
        metadata_source_title = metadata.get('source_title')
        reasons = []
        if metadata_raw_data_id and str(metadata_raw_data_id) != str(raw_data.id):
            reasons.append(f'raw_data_id:{metadata_raw_data_id}!={raw_data.id}')
        if metadata_source_url and metadata_source_url != raw_data.source_url:
            reasons.append('source_url')
        if metadata_source_title and metadata_source_title != raw_data.title:
            reasons.append('source_title')
        if reasons:
            mismatches.append(f'id={event.id}:{",".join(reasons)}')
    return '|'.join(mismatches) or 'none'


def _meaningless_titles(events):
    values = []
    for event in events:
        metadata = event.metadata_json or {}
        title = metadata.get('display_title') or event.title
        if is_meaningless_schedule_title(title):
            values.append(f'id={event.id}:{_safe(title)}')
    return '|'.join(values) or 'none'


def _duplicate_candidates(events):
    groups = defaultdict(list)
    for event in events:
        normalized = normalize_event_title_for_dedupe(event.title)
        if not normalized:
            continue
        key = (
            timezone.localdate(event.start_at),
            event.start_at,
            event.end_at,
            event.event_type,
            _event_track_key(event),
            normalized,
        )
        groups[key].append(event)

    candidates = []
    for group in groups.values():
        if len(group) <= 1:
            continue
        candidates.append(
            ','.join(
                f'{event.id}:{_safe(event.title)}:{_event_track_key(event) or "common"}'
                for event in group
            )
        )
    return '|'.join(candidates) or 'none'


def _is_evaluation_event(event):
    title = str(event.title or '')
    metadata = event.metadata_json or {}
    return (
        event.event_type == 'exam'
        and (
            '월말평가' in title
            or '과목평가' in title
            or metadata.get('parser_type') == 'evaluation_notice'
            or metadata.get('parser') == 'evaluation_notice_ocr'
        )
    )


def _event_track_key(event):
    metadata = event.metadata_json or {}
    audience = metadata.get('audience') or {}
    track = normalize_track_key(
        metadata.get('track_key')
        or metadata.get('track')
        or audience.get('track_key')
        or audience.get('track')
        or ''
    )
    if track == COMMON_TRACK_KEY:
        return COMMON_TRACK_KEY
    return track if track in set(canonical_track_keys()) else ''


def _is_common_event(event):
    metadata = event.metadata_json or {}
    if metadata.get('is_common') is True:
        return True
    return _event_track_key(event) == COMMON_TRACK_KEY


def _is_common_candidate(event):
    title = f'{event.title or ""} {(event.metadata_json or {}).get("source_title") or ""}'
    keywords = ['온라인 위크', '?⑤씪???꾪겕', 'common', '공통', '전체 교육생', '전체']
    return any(keyword in title for keyword in keywords) or (_event_track_key(event) in {'', COMMON_TRACK_KEY})


def _event_debug_label(event):
    return (
        f'id={event.id}:date={timezone.localdate(event.start_at).isoformat()}:'
        f'track={_event_track_key(event) or "-"}:title={_safe(event.title)}'
    )


def _print_events_for_date(stdout, target_date):
    start_at = timezone.make_aware(datetime.combine(target_date, datetime.min.time()), timezone.get_current_timezone())
    end_at = timezone.make_aware(datetime.combine(target_date, datetime.max.time()), timezone.get_current_timezone())
    events = ScheduleEvent.objects.filter(start_at__lte=end_at, end_at__gte=start_at).order_by('start_at', 'id')
    stdout.write(f'date={target_date.isoformat()}')
    stdout.write(f'event_count={events.count()}')
    for event in events:
        metadata = event.metadata_json or {}
        stdout.write(
            'event '
            f'id={event.id} '
            f'title={_safe(event.title)} '
            f'display_title={_safe(metadata.get("display_title"))} '
            f'event_type={_safe(event.event_type)} '
            f'start_at={timezone.localtime(event.start_at).isoformat()} '
            f'end_at={timezone.localtime(event.end_at).isoformat()} '
            f'raw_data_id={event.raw_data_id or ""} '
            f'source_type={_safe(event.source_type)} '
            f'source_url={_safe(_event_source_url(event))} '
            f'source_title={_safe((event.raw_data.title if event.raw_data_id and event.raw_data else "") or metadata.get("source_title"))} '
            f'raw_title={_safe(metadata.get("raw_title"))} '
            f'parser_type={_safe(metadata.get("parser_type"))} '
            f'date_mapping_source={_safe(metadata.get("date_mapping_source"))} '
            f'original_header_date={_safe(metadata.get("original_header_date"))} '
            f'fallback_date={_safe(metadata.get("fallback_date"))}'
        )


def _print_event_source_mapping(stdout, event_id):
    event = ScheduleEvent.objects.select_related('raw_data').filter(id=event_id).first()
    stdout.write(f'event_id={event_id}')
    if not event:
        stdout.write('event_found=false')
        return
    metadata = event.metadata_json or {}
    raw_data = event.raw_data
    stdout.write('event_found=true')
    stdout.write(f'title={_safe(event.title)}')
    stdout.write(f'start_at={timezone.localtime(event.start_at).isoformat()}')
    stdout.write(f'end_at={timezone.localtime(event.end_at).isoformat()}')
    stdout.write(f'raw_data_id={event.raw_data_id or metadata.get("raw_data_id") or ""}')
    stdout.write(f'source_type={_safe(event.source_type)}')
    stdout.write(f'source_url={_safe(_event_source_url(event))}')
    stdout.write(f'source_title={_safe((raw_data.title if raw_data else "") or metadata.get("source_title"))}')
    stdout.write(f'raw_title={_safe(metadata.get("raw_title"))}')
    stdout.write(f'parser_type={_safe(metadata.get("parser_type"))}')
    stdout.write(f'date_mapping_source={_safe(metadata.get("date_mapping_source"))}')
    stdout.write(f'track={_safe(metadata.get("track"))}')


def _event_source_url(event):
    if event.raw_data_id and event.raw_data:
        return event.raw_data.source_url
    return (event.metadata_json or {}).get('source_url') or ''


def _safe(value):
    text = str(value if value is not None else '').replace('\n', ' ').replace('\r', ' ')
    return text.encode('cp949', errors='replace').decode('cp949')
