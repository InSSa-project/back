from django.core.management.base import BaseCommand
from django.db import transaction

from schedules.models import ScheduleEvent


class Command(BaseCommand):
    help = 'Delete OCR/crawling generated ScheduleEvent rows linked to RawSsafyData.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Print the target count without deleting rows.')

    @transaction.atomic
    def handle(self, *args, **options):
        queryset = ScheduleEvent.objects.filter(raw_data__isnull=False)
        delete_count = queryset.count()

        if not options['dry_run']:
            queryset.delete()
        else:
            transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                'Generated schedule reset completed.\n'
                f'delete_count={delete_count}\n'
                f'dry_run={str(options["dry_run"]).lower()}'
            )
        )
