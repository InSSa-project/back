from collections import Counter

from django.core.management.base import BaseCommand

from schedules.models import ScheduleEvent
from sync.models import RawSsafyData
from sync.services.tracks import COMMON_TRACK_KEY, canonical_track_keys, normalize_track_key, track_key_from_text


CANONICAL = set(canonical_track_keys())


class Command(BaseCommand):
    help = 'Backfill ScheduleEvent track metadata from event/source titles.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Write changes. Default is dry-run.')
        parser.add_argument('--override', action='store_true', help='Override existing non-common track metadata.')
        parser.add_argument('--limit', type=int, default=0, help='Limit scanned events for inspection.')

    def handle(self, *args, **options):
        apply = options['apply']
        override = options['override']
        limit = options['limit']

        qs = ScheduleEvent.objects.select_related('raw_data').order_by('id')
        if limit:
            qs = qs[:limit]

        raw_cache = {}
        scanned = 0
        changed = 0
        skipped_existing = 0
        inferred_counter = Counter()
        examples = []

        for event in qs:
            scanned += 1
            metadata = dict(event.metadata_json or {})
            audience = dict(metadata.get('audience') or {})
            current_track = normalize_track_key(
                metadata.get('track_key')
                or audience.get('track_key')
                or metadata.get('track')
                or audience.get('track')
            )

            if current_track in CANONICAL and not override:
                skipped_existing += 1
                continue

            inferred = _infer_event_track(event, metadata, raw_cache)
            if inferred not in CANONICAL:
                continue

            if current_track == inferred and metadata.get('track_key') == inferred and audience.get('track_key') == inferred:
                continue

            inferred_counter[inferred] += 1
            changed += 1
            if len(examples) < 20:
                examples.append((event.id, event.title, current_track or '-', inferred))

            if apply:
                metadata['track_key'] = inferred
                metadata['track'] = inferred
                metadata['is_common'] = inferred == COMMON_TRACK_KEY
                audience['track_key'] = inferred
                audience['track'] = inferred
                metadata['audience'] = audience
                event.metadata_json = metadata
                event.save(update_fields=['metadata_json', 'updated_at'])

        mode = 'APPLY' if apply else 'DRY-RUN'
        self.stdout.write(f'mode={mode}')
        self.stdout.write(f'scanned={scanned}')
        self.stdout.write(f'changed={changed}')
        self.stdout.write(f'skipped_existing={skipped_existing}')
        self.stdout.write('by_track=' + ', '.join(f'{key}:{value}' for key, value in sorted(inferred_counter.items())))
        for event_id, title, before, after in examples:
            safe_title = str(title or '').encode('unicode_escape').decode('ascii')
            self.stdout.write(f'example id={event_id} before={before} after={after} title={safe_title}')


def _infer_event_track(event, metadata, raw_cache):
    candidates = [
        getattr(event, 'title', ''),
        metadata.get('source_title', ''),
        metadata.get('raw_title', ''),
    ]
    raw_data = getattr(event, 'raw_data', None) or _raw_data_from_metadata(metadata, raw_cache)
    if raw_data is not None:
        candidates.extend([
            getattr(raw_data, 'title', ''),
            (getattr(raw_data, 'metadata_json', None) or {}).get('title', ''),
        ])

    for candidate in candidates:
        inferred = track_key_from_text(candidate)
        if inferred in CANONICAL:
            return inferred
    return ''


def _raw_data_from_metadata(metadata, raw_cache):
    raw_data_id = metadata.get('raw_data_id')
    if not raw_data_id:
        return None
    try:
        raw_data_id = int(raw_data_id)
    except (TypeError, ValueError):
        return None
    if raw_data_id not in raw_cache:
        raw_cache[raw_data_id] = RawSsafyData.objects.filter(pk=raw_data_id).first()
    return raw_cache[raw_data_id]
