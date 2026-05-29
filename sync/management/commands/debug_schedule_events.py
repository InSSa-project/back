from datetime import datetime

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils.dateparse import parse_date
from django.utils import timezone

from schedules.models import ScheduleEvent
from sync.models import RawSsafyData
from sync.services.calendar_quality import (
    build_month_quality_report,
    format_count_dict,
    format_daily_counts,
    format_empty_weekdays,
    format_suspicious_events,
    parse_month_option,
)
from sync.services.reparse_service import reparse_raw_data_to_events

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
        self.stdout.write(f'generated_schedule_count={ScheduleEvent.objects.filter(raw_data__isnull=False).count()}')
        self.stdout.write(f'manual_schedule_count={ScheduleEvent.objects.filter(raw_data__isnull=True).count()}')
        self.stdout.write(f'monthly_schedule_counts={_monthly_schedule_counts()}')
        self.stdout.write(f'raw_total={RawSsafyData.objects.count()}')
        self.stdout.write(f'raw_source_type_counts={_source_type_counts()}')
        self.stdout.write(f'daily_event_counts={format_daily_counts(quality_report)}')
        self.stdout.write(f'month_generated_count={quality_report.generated_count}')
        self.stdout.write(f'month_manual_count={quality_report.manual_count}')
        self.stdout.write(f'month_holiday_count={quality_report.holiday_count}')
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
    return ''


def _safe(value):
    return str(value if value is not None else '').replace('\n', ' ').replace('\r', ' ')
