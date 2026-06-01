from django.core.management.base import BaseCommand
from django.db import transaction

from schedules.models import ScheduleEvent


class Command(BaseCommand):
    help = 'Delete OCR/crawling generated ScheduleEvent rows linked to RawSsafyData.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Print the target count without deleting rows.')
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Actually delete generated ScheduleEvent rows. Required unless --dry-run is used.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        queryset = ScheduleEvent.objects.filter(raw_data__isnull=False)
        delete_count = queryset.count()
        self.stdout.write(f'target_count={delete_count}')

        if not options['dry_run'] and options['confirm']:
            queryset.delete()
        elif not options['dry_run']:
            self.stdout.write(
                self.style.WARNING(
                    'Reset aborted. Pass --confirm to delete generated schedule events, '
                    'or --dry-run to inspect the target count.'
                )
            )
            transaction.set_rollback(True)
        else:
            transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                'Generated schedule reset completed.\n'
                f'delete_count={delete_count}\n'
                f'dry_run={str(options["dry_run"]).lower()}\n'
                f'confirmed={str(options["confirm"]).lower()}'
            )
        )
