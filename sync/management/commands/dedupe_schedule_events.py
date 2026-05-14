from django.core.management.base import BaseCommand
from django.db.models import Count, Min

from schedules.models import ScheduleEvent


DEDUP_FIELDS = ['title', 'start_at', 'end_at', 'event_type', 'source_type']


class Command(BaseCommand):
    help = 'Remove duplicate schedule events while keeping the oldest row in each duplicate group.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show how many duplicate schedule events would be deleted without deleting them.',
        )

    def handle(self, *args, **options):
        duplicate_groups = (
            ScheduleEvent.objects.values(*DEDUP_FIELDS)
            .annotate(keep_id=Min('id'), duplicate_count=Count('id'))
            .filter(duplicate_count__gt=1)
        )

        groups = list(duplicate_groups)
        delete_ids = []
        for group in groups:
            group_filter = {field: group[field] for field in DEDUP_FIELDS}
            ids = list(
                ScheduleEvent.objects.filter(**group_filter)
                .exclude(id=group['keep_id'])
                .values_list('id', flat=True)
            )
            delete_ids.extend(ids)

        if options['dry_run']:
            self.stdout.write(
                self.style.WARNING(
                    f'dry_run=true, duplicate_groups={len(groups)}, delete_count={len(delete_ids)}'
                )
            )
            return

        deleted_count = 0
        if delete_ids:
            deleted_count, _ = ScheduleEvent.objects.filter(id__in=delete_ids).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f'duplicate_groups={len(groups)}, deleted_count={deleted_count}'
            )
        )
