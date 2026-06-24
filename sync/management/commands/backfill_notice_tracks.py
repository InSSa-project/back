"""
Management command: backfill_notice_tracks

Classifies all notice records that are missing track_key / is_common
metadata and writes the result back into metadata_json.

Usage examples:
    python manage.py backfill_notice_tracks --dry-run
    python manage.py backfill_notice_tracks
    python manage.py backfill_notice_tracks --force   # overwrite existing values
"""

import logging

from django.core.management.base import BaseCommand

from sync.models import RawSsafyData
from sync.services.notice_policy import notice_title
from sync.services.tracks import COMMON_TRACK_KEY, classify_notice_track, canonical_track_keys

LOGGER = logging.getLogger(__name__)

# Order matters for display: specific tracks first, common last.
RESULT_TRACK_ORDER = canonical_track_keys() + [COMMON_TRACK_KEY]


class Command(BaseCommand):
    help = (
        'Backfill track_key and is_common into metadata_json for notice records '
        'that currently lack this information.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Show what would be changed without writing to the database.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            default=False,
            help='Overwrite existing track_key values (default: preserve them).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']

        notices = RawSsafyData.objects.filter(source_type='notice').order_by('id')
        total = notices.count()
        self.stdout.write(f'전체 notice: {total}')

        counts_by_track = {key: 0 for key in RESULT_TRACK_ORDER}
        unresolved = []
        changed = 0
        skipped = 0

        for notice in notices:
            try:
                metadata = dict(notice.metadata_json or {})
                has_track_key = 'track_key' in metadata

                if has_track_key and not force:
                    skipped += 1
                    existing = metadata.get('track_key', COMMON_TRACK_KEY)
                    _increment(counts_by_track, existing)
                    continue

                title = notice_title(notice)
                # When forcing, strip existing track fields so that classify_notice_track
                # performs fresh inference instead of returning the stored value.
                inference_metadata = {
                    k: v for k, v in metadata.items()
                    if k not in ('track_key', 'is_common', 'track', 'track_display', 'track_name')
                } if force else metadata
                result = classify_notice_track(title, inference_metadata)
                new_track_key = result['track_key']
                new_is_common = result['is_common']

                if not dry_run:
                    metadata['track_key'] = new_track_key
                    metadata['is_common'] = new_is_common
                    notice.metadata_json = metadata
                    notice.save(update_fields=['metadata_json'])

                changed += 1
                _increment(counts_by_track, new_track_key)

            except Exception as exc:
                LOGGER.warning('backfill_notice_tracks: id=%s error=%s', notice.id, exc)
                unresolved.append({'id': notice.id, 'title': getattr(notice, 'title', ''), 'error': str(exc)})

        self._print_results(total, changed, skipped, counts_by_track, unresolved, dry_run, force)

    def _print_results(self, total, changed, skipped, counts_by_track, unresolved, dry_run, force):
        prefix = '[DRY-RUN] ' if dry_run else ''

        self.stdout.write('')
        self.stdout.write(f'{prefix}변경{"(예정)" if dry_run else ""}: {changed}건  보존(기존 값): {skipped}건')
        self.stdout.write('')
        self.stdout.write('--- 트랙별 집계 ---')
        for track_key in RESULT_TRACK_ORDER:
            count = counts_by_track.get(track_key, 0)
            self.stdout.write(f'  {track_key}: {count}')

        others = {k: v for k, v in counts_by_track.items() if k not in RESULT_TRACK_ORDER}
        for track_key, count in sorted(others.items()):
            self.stdout.write(f'  {track_key} (비표준): {count}')

        self.stdout.write(f'  unresolved: {len(unresolved)}')

        if unresolved:
            self.stdout.write('')
            self.stdout.write('--- 처리 실패 항목 ---')
            for item in unresolved[:20]:
                self.stdout.write(f'  id={item["id"]} title={item["title"]!r} error={item["error"]}')

        if not dry_run and not force:
            self.stdout.write('')
            self.stdout.write(
                '(이미 track_key가 있는 레코드는 보존됐습니다. '
                '--force 옵션을 추가하면 덮어씁니다.)'
            )


def _increment(counts, track_key):
    if track_key not in counts:
        counts[track_key] = 0
    counts[track_key] += 1
