from django.core.management.base import BaseCommand
from django.db import transaction

from schedules.models import ScheduleEvent
from sync.models import RawSsafyData


SAMPLE_EVENT_TITLES = {'월말평가', '프로젝트 제출 마감', '취업 특강'}
SAMPLE_URL_PREFIX = 'https://sample.ssafy.local'


class Command(BaseCommand):
    help = 'Delete local sample RawSsafyData rows and clearly associated sample ScheduleEvent rows.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Show deletable sample rows without deleting.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        sample_raw_qs = RawSsafyData.objects.filter(source_url__startswith=SAMPLE_URL_PREFIX) | RawSsafyData.objects.filter(
            metadata_json__collected_from='sample'
        )
        sample_raw_qs = sample_raw_qs.distinct()
        sample_raw_ids = list(sample_raw_qs.values_list('id', flat=True))
        sample_raw_delete_qs = RawSsafyData.objects.filter(id__in=sample_raw_ids)

        linked_events = ScheduleEvent.objects.filter(raw_data_id__in=sample_raw_ids)
        clear_unlinked_events = ScheduleEvent.objects.filter(raw_data__isnull=True, title__in=SAMPLE_EVENT_TITLES)
        delete_event_ids = list((linked_events | clear_unlinked_events).values_list('id', flat=True).distinct())
        delete_event_qs = ScheduleEvent.objects.filter(id__in=delete_event_ids)

        raw_count = len(sample_raw_ids)
        event_count = len(delete_event_ids)

        for raw_data in sample_raw_qs.order_by('id'):
            self.stdout.write(f'raw_data id={raw_data.id} title={raw_data.title} source_url={raw_data.source_url}')
        for event in delete_event_qs.order_by('id'):
            self.stdout.write(f'event id={event.id} title={event.title} raw_data_id={event.raw_data_id}')

        with transaction.atomic():
            if not dry_run:
                delete_event_qs.delete()
                sample_raw_delete_qs.delete()
            else:
                transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                'Sample cleanup completed.\n'
                f'raw_delete_count={raw_count}\n'
                f'event_delete_count={event_count}\n'
                f'dry_run={str(dry_run).lower()}'
            )
        )
        if dry_run:
            self.stdout.write('Run without --dry-run to delete only the listed sample rows.')
