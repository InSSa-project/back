from collections import Counter

from django.core.management.base import BaseCommand

from schedules.models import ScheduleEvent
from schedules.services import infer_schedule_event_track_metadata


class Command(BaseCommand):
    help = 'Backfill ScheduleEvent track metadata from event/source titles and common schedule signals.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Write changes. Default is dry-run.')
        parser.add_argument('--override', action='store_true', help='Override existing canonical track metadata.')
        parser.add_argument('--limit', type=int, default=0, help='Limit scanned events for inspection.')

    def handle(self, *args, **options):
        apply_changes = options['apply']
        override = options['override']
        limit = options['limit']

        queryset = ScheduleEvent.objects.select_related('raw_data').order_by('id')
        if limit:
            queryset = queryset[:limit]

        scanned = 0
        changed = 0
        skipped_existing = 0
        unresolved = 0
        inferred_counter = Counter()
        reason_counter = Counter()
        examples = []

        for event in queryset:
            scanned += 1
            before = dict(event.metadata_json or {})
            metadata, should_update, info = infer_schedule_event_track_metadata(event, override=override)

            reason = info.get('reason') or 'unknown'
            track_key = info.get('track_key') or '-'
            reason_counter[reason] += 1
            if reason == 'existing_canonical':
                skipped_existing += 1
            if reason == 'unresolved':
                unresolved += 1

            if not should_update or metadata == before:
                continue

            changed += 1
            inferred_counter[track_key] += 1
            if len(examples) < 20:
                examples.append((event.id, event.title, _metadata_track(before) or '-', track_key, reason))

            if apply_changes:
                event.metadata_json = metadata
                event.save(update_fields=['metadata_json', 'updated_at'])

        mode = 'APPLY' if apply_changes else 'DRY-RUN'
        self.stdout.write(f'mode={mode}')
        self.stdout.write(f'scanned={scanned}')
        self.stdout.write(f'changed={changed}')
        self.stdout.write(f'skipped_existing={skipped_existing}')
        self.stdout.write(f'unresolved={unresolved}')
        self.stdout.write('by_track=' + _format_counter(inferred_counter))
        self.stdout.write('by_reason=' + _format_counter(reason_counter))
        for event_id, title, before, after, reason in examples:
            safe_title = str(title or '').encode('unicode_escape').decode('ascii')
            self.stdout.write(f'example id={event_id} before={before} after={after} reason={reason} title={safe_title}')


def _metadata_track(metadata):
    audience = metadata.get('audience') or {}
    return (
        metadata.get('track_key')
        or audience.get('track_key')
        or metadata.get('track')
        or audience.get('track')
    )


def _format_counter(counter):
    if not counter:
        return ''
    return ', '.join(f'{key}:{value}' for key, value in sorted(counter.items()))
