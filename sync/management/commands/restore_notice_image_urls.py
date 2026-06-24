"""
Management command: restore_notice_image_urls

Restores notice image_urls that were overwritten to /media/ paths by the
previous backfill_notice_images command (which stored files on the local disk).

Because Oracle Cloud uses an ephemeral filesystem, those /media/ files are gone after
every redeploy. This command recovers the original SSAFY CDN URLs by
re-extracting them from raw_html, then replaces the /media/ entries.

Priority for recovery:
  1. notice_images[].source_url (set by the new backfill)
  2. raw_json.image_urls (original crawled data)
  3. raw_html re-extraction via BeautifulSoup

Usage:
    python manage.py restore_notice_image_urls --dry-run
    python manage.py restore_notice_image_urls
    python manage.py restore_notice_image_urls --limit 50
    python manage.py restore_notice_image_urls --notice-id 123
"""

import logging
from urllib.parse import urlparse

from django.core.management.base import BaseCommand

from sync.models import RawSsafyData

LOGGER = logging.getLogger(__name__)

ALLOWED_ORIGINS = {
    'edu.ssafy.com',
    'ssafy.com',
    's3.ap-northeast-2.amazonaws.com',
    'storage.googleapis.com',
}


class Command(BaseCommand):
    help = (
        'Restore notice image_urls that were overwritten with /media/ paths. '
        'Recovers original SSAFY CDN URLs from notice_images.source_url, '
        'raw_json, or raw_html re-extraction.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', default=False,
                            help='Show what would change without writing to the DB.')
        parser.add_argument('--limit', type=int, default=0,
                            help='Process at most N notices (0 = no limit).')
        parser.add_argument('--notice-id', type=int, default=0,
                            help='Process only the notice with this ID.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        notice_id = options['notice_id']

        qs = RawSsafyData.objects.filter(source_type='notice').order_by('id')
        if notice_id:
            qs = qs.filter(pk=notice_id)
        total = qs.count()
        if limit:
            qs = qs[:limit]

        stats = {
            'total': total,
            'scanned': 0,
            'has_local_url': 0,
            'restored': 0,
            'no_source_found': 0,
            'already_ok': 0,
        }

        for notice in qs:
            stats['scanned'] += 1
            metadata = dict(notice.metadata_json or {})
            image_urls = list(metadata.get('image_urls') or [])

            if not image_urls:
                stats['already_ok'] += 1
                continue

            has_local = any(_is_local_url(u) for u in image_urls)
            if not has_local:
                stats['already_ok'] += 1
                continue

            stats['has_local_url'] += 1
            restored_urls = _recover_urls(notice, metadata, image_urls)

            if not any(restored_urls):
                stats['no_source_found'] += 1
                LOGGER.warning('restore_notice_image_urls: no source found id=%s', notice.id)
                continue

            if restored_urls == image_urls:
                stats['already_ok'] += 1
                continue

            self.stdout.write(
                f'  id={notice.id}: {len(image_urls)} url(s) → {len(restored_urls)} cdn url(s)'
            )

            if not dry_run:
                metadata['image_urls'] = restored_urls
                notice.metadata_json = metadata
                notice.save(update_fields=['metadata_json'])

            stats['restored'] += 1

        self._print_results(stats, dry_run)

    def _print_results(self, stats, dry_run):
        prefix = '[DRY-RUN] ' if dry_run else ''
        self.stdout.write('')
        self.stdout.write(f'{prefix}전체 notice:       {stats["total"]}')
        self.stdout.write(f'{prefix}스캔:              {stats["scanned"]}')
        self.stdout.write(f'{prefix}/media/ URL 있음:  {stats["has_local_url"]}')
        self.stdout.write(f'{prefix}복원 완료:         {stats["restored"]}')
        self.stdout.write(f'{prefix}이미 정상:         {stats["already_ok"]}')
        self.stdout.write(f'{prefix}소스 찾기 실패:    {stats["no_source_found"]}')

        if dry_run:
            self.stdout.write('')
            self.stdout.write('--dry-run 모드: DB를 변경하지 않았습니다.')


def _recover_urls(notice, metadata, current_image_urls):
    """
    Try to recover original CDN URLs for a notice.
    Returns a list of CDN URLs (may be shorter if some could not be recovered).
    """
    recovered = []

    # Strategy 1: notice_images[].source_url (from new backfill)
    notice_images = metadata.get('notice_images') or []
    if isinstance(notice_images, list) and notice_images:
        for entry in notice_images:
            if isinstance(entry, dict):
                src = str(entry.get('source_url') or '').strip()
                if src and _is_external_url(src):
                    recovered.append(src)
        if recovered:
            return recovered

    # Strategy 2: raw_json.image_urls (original crawled JSON)
    raw_json = metadata.get('raw_json') or {}
    if isinstance(raw_json, dict):
        raw_json_urls = raw_json.get('image_urls') or []
        if isinstance(raw_json_urls, list):
            for u in raw_json_urls:
                u = str(u or '').strip()
                if u and _is_external_url(u):
                    recovered.append(u)
        if recovered:
            return recovered

    # Strategy 3: re-extract from raw_html
    raw_html = notice.raw_html or ''
    source_url = notice.source_url or ''
    if raw_html:
        try:
            from sync.services.ssafy_crawler import extract_image_urls_from_html
            extracted = extract_image_urls_from_html(raw_html, source_url)
            for u in extracted:
                u = str(u or '').strip()
                if u and _is_external_url(u) and _is_allowed_origin(u):
                    recovered.append(u)
        except Exception as exc:
            LOGGER.warning('restore_notice_image_urls: html extraction failed id=%s: %s', notice.id, exc)

    return recovered


def _is_local_url(url):
    return str(url or '').lower().startswith('/media/')


def _is_external_url(url):
    lowered = str(url or '').lower()
    return lowered.startswith('http://') or lowered.startswith('https://')


def _is_allowed_origin(url):
    try:
        host = urlparse(url).hostname or ''
    except Exception:
        return False
    return any(host == o or host.endswith(f'.{o}') for o in ALLOWED_ORIGINS)
