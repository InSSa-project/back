from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from schedules.models import ScheduleEvent
from sync.models import RawSsafyData


DEFAULT_MIN_RAW_COUNT = 3


class Command(BaseCommand):
    help = 'Restore generated calendar data from existing RawSsafyData with safety checks.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source-type',
            default='notice',
            choices=['notice', 'academic_rule'],
            help='RawSsafyData source_type to reparse.',
        )
        parser.add_argument('--year', type=int, default=2026, help='Holiday year to seed.')
        parser.add_argument(
            '--min-raw-count',
            type=int,
            default=DEFAULT_MIN_RAW_COUNT,
            help='Abort if matching RawSsafyData rows are fewer than this count.',
        )
        parser.add_argument('--dry-run', action='store_true', help='Print actions without deleting or creating rows.')

    def handle(self, *args, **options):
        source_type = options['source_type']
        raw_count = RawSsafyData.objects.filter(source_type=source_type).count()
        generated_count = ScheduleEvent.objects.filter(raw_data__isnull=False).count()

        self.stdout.write(
            'Calendar restore preflight.\n'
            f'source_type={source_type}\n'
            f'raw_count={raw_count}\n'
            f'generated_schedule_count={generated_count}\n'
            f'min_raw_count={options["min_raw_count"]}\n'
            f'dry_run={str(options["dry_run"]).lower()}'
        )

        if raw_count < options['min_raw_count']:
            raise CommandError(
                f'Not enough RawSsafyData rows to restore calendar data: {raw_count} < {options["min_raw_count"]}. '
                'Run python manage.py crawl_ssafy_notices first, then retry restore_calendar_data.'
            )

        reset_args = ['--dry-run'] if options['dry_run'] else ['--confirm']
        holiday_args = ['--year', str(options['year'])]
        reparse_args = ['--source-type', source_type]
        if options['dry_run']:
            holiday_args.append('--dry-run')
            reparse_args.append('--dry-run')

        call_command('reset_generated_schedule_events', *reset_args, stdout=self.stdout)
        call_command('seed_korean_holidays', *holiday_args, stdout=self.stdout)
        call_command('reparse_raw_ssafy_data', *reparse_args, stdout=self.stdout)

        self.stdout.write(
            self.style.SUCCESS(
                'Calendar restore completed.\n'
                f'raw_count={raw_count}\n'
                f'generated_schedule_count={ScheduleEvent.objects.filter(raw_data__isnull=False).count()}\n'
                f'total_schedule_count={ScheduleEvent.objects.count()}\n'
                f'dry_run={str(options["dry_run"]).lower()}'
            )
        )
