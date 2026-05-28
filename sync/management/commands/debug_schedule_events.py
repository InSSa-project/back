from datetime import datetime

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from schedules.models import ScheduleEvent
from sync.models import RawSsafyData
from sync.services.reparse_service import reparse_raw_data_to_events


class Command(BaseCommand):
    help = 'Print ScheduleEvent and RawSsafyData counts useful for calendar import debugging.'

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, default=2026, help='Calendar year to inspect.')
        parser.add_argument('--month', type=int, default=5, help='Calendar month to inspect.')
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
        year = options['year']
        month = options['month']
        start_at = _aware(datetime(year, month, 1))
        end_at = _next_month(start_at)

        self.stdout.write(f'schedule_total={ScheduleEvent.objects.count()}')
        self.stdout.write(
            f'schedule_{year}_{month:02d}_count='
            f'{ScheduleEvent.objects.filter(start_at__lt=end_at, end_at__gt=start_at).count()}'
        )
        self.stdout.write(f'generated_schedule_count={ScheduleEvent.objects.filter(raw_data__isnull=False).count()}')
        self.stdout.write(f'raw_source_type_counts={_source_type_counts()}')

        if options['no_reparse']:
            return

        queryset = RawSsafyData.objects.filter(source_type=options['reparse_source_type'])
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


def _aware(value):
    return timezone.make_aware(value, timezone.get_current_timezone())


def _next_month(value):
    if value.month == 12:
        return _aware(datetime(value.year + 1, 1, 1))
    return _aware(datetime(value.year, value.month + 1, 1))


def _source_type_counts():
    rows = RawSsafyData.objects.values('source_type').order_by('source_type').annotate(count=Count('id'))
    return '|'.join(f'{row["source_type"]}:{row["count"]}' for row in rows) or 'none'
