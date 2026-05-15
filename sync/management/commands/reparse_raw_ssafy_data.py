from django.core.management.base import BaseCommand

from sync.models import RawSsafyData
from sync.services.reparse_service import reparse_raw_data_to_events


class Command(BaseCommand):
    help = 'Reparse existing RawSsafyData rows into ScheduleEvent rows.'

    def add_arguments(self, parser):
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

    def handle(self, *args, **options):
        queryset = RawSsafyData.objects.all()
        source_types = options.get('source_type')
        if source_types:
            queryset = queryset.filter(source_type__in=source_types)

        summary = reparse_raw_data_to_events(
            queryset,
            dry_run=options['dry_run'],
            limit=options.get('limit'),
        )

        self.stdout.write(
            self.style.SUCCESS(
                'Reparse completed.\n'
                f'raw_checked={summary.raw_checked}\n'
                f'candidate_count={summary.candidate_count}\n'
                f'created_count={summary.created_count}\n'
                f'skipped_count={summary.skipped_count}\n'
                f'no_schedule_count={summary.no_schedule_count}\n'
                f'failed_count={summary.failed_count}\n'
                f'dry_run={str(summary.dry_run).lower()}'
            )
        )
