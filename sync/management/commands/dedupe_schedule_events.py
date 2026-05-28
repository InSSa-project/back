from django.core.management.base import BaseCommand

from schedules.models import ScheduleEvent
from schedules.utils import normalize_event_title_for_dedupe


class Command(BaseCommand):
    help = 'Remove duplicate schedule events while keeping the oldest row in each duplicate group.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show how many duplicate schedule events would be deleted without deleting them.',
        )

    def handle(self, *args, **options):
        groups = _duplicate_groups()
        delete_ids = [event.id for group in groups for event in group[1:]]

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


def _duplicate_groups():
    grouped = {}
    for event in ScheduleEvent.objects.filter(raw_data__isnull=False).order_by('id'):
        normalized_title = normalize_event_title_for_dedupe(event.title)
        if not normalized_title:
            continue
        key = (
            normalized_title,
            event.start_at,
            event.end_at,
            event.event_type,
            _event_track(event),
            event.source_type,
        )
        grouped.setdefault(key, []).append(event)
    return [events for events in grouped.values() if len(events) > 1]


def _event_track(event):
    metadata = event.metadata_json or {}
    audience = metadata.get('audience') or {}
    return str(metadata.get('track') or audience.get('track') or '').strip().lower()
