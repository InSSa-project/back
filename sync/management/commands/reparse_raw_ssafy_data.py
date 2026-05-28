from django.core.management.base import BaseCommand

from schedules.models import ScheduleEvent
from sync.models import RawSsafyData
from sync.services.reparse_service import reparse_raw_data_to_events


class Command(BaseCommand):
    help = 'Reparse existing RawSsafyData rows into ScheduleEvent rows.'

    def add_arguments(self, parser):
        parser.add_argument('--id', type=int, help='Only reparse one RawSsafyData id.')
        parser.add_argument(
            '--source-type',
            action='append',
            choices=['notice', 'academic_rule'],
            help='RawSsafyData source_type to include. Can be passed multiple times.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Count creatable events without writing ScheduleEvent rows.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            help='Maximum number of RawSsafyData rows to inspect.',
        )
        parser.add_argument(
            '--replace-events',
            action='store_true',
            help='Delete existing ScheduleEvent rows linked to the selected RawSsafyData rows before recreating them.',
        )

    def handle(self, *args, **options):
        queryset = RawSsafyData.objects.all()
        if options.get('id'):
            queryset = queryset.filter(pk=options['id'])
        source_types = options.get('source_type')
        if source_types:
            queryset = queryset.filter(source_type__in=source_types)

        if options['replace_events']:
            existing_count = ScheduleEvent.objects.filter(raw_data__in=queryset).count()
            warning = (
                'WARNING: --replace-events will delete existing ScheduleEvent rows linked to the selected raw data '
                f'before recreating them. Existing linked events={existing_count}. '
                'If any of these were manually edited in the calendar, review before running without --dry-run.'
            )
            self.stdout.write(self.style.WARNING(warning))

        summary = reparse_raw_data_to_events(
            queryset,
            dry_run=options['dry_run'],
            limit=options.get('limit'),
            replace_events=options['replace_events'],
        )

        self.stdout.write(
            self.style.SUCCESS(
                'Reparse completed.\n'
                f'raw_checked={summary.raw_checked}\n'
                f'candidate_count={summary.candidate_count}\n'
                f'created_count={summary.created_count}\n'
                f'skipped_count={summary.skipped_count}\n'
                f'duplicate_skip_count={summary.duplicate_skip_count}\n'
                f'wrapper_skip_count={summary.wrapper_skip_count}\n'
                f'validation_skip_count={summary.validation_skip_count}\n'
                f'empty_title_skip_count={summary.empty_title_skip_count}\n'
                f'replaced_event_count={summary.replaced_event_count}\n'
                f'no_schedule_count={summary.no_schedule_count}\n'
                f'failed_count={summary.failed_count}\n'
                f'dry_run={str(summary.dry_run).lower()}'
            )
        )
