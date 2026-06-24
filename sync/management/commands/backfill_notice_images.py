"""
Management command: backfill_notice_images

Downloads external notice images (e.g. SSAFY CDN URLs) to local media storage,
creates optimised WebP versions (max 1600 px wide, quality 80), and updates
metadata_json so subsequent API responses return the local URLs.

Safety guarantees:
  - Only processes URLs from known SSAFY CDN origins (ALLOWED_IMAGE_ORIGINS).
  - Already-downloaded images (local /media/ paths) are not re-downloaded.
  - A single image failure never aborts the entire run.
  - Designed to be idempotent: re-running produces no duplicate work.

Usage:
    python manage.py backfill_notice_images --dry-run
    python manage.py backfill_notice_images
    python manage.py backfill_notice_images --limit 50
    python manage.py backfill_notice_images --timeout 15

Expected run time: ~2–5 s per image (network + disk + Pillow conversion).
"""

import hashlib
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand

from sync.models import RawSsafyData

LOGGER = logging.getLogger(__name__)

# Only download from these origins to prevent SSRF.
ALLOWED_IMAGE_ORIGINS = {
    'edu.ssafy.com',
    'ssafy.com',
    's3.ap-northeast-2.amazonaws.com',
    'storage.googleapis.com',
}

IMAGE_MAX_WIDTH = 1600
IMAGE_QUALITY = 80
OPTIMIZED_SUFFIX = '_opt'
MEDIA_NOTICE_DIR = 'notices'
DEFAULT_TIMEOUT = 20  # seconds per image request


class Command(BaseCommand):
    help = (
        'Download and optimise external notice images, storing them in local '
        'media/notices/ and updating metadata_json with the new URLs.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Report what would be done without writing anything.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Process at most this many notices (0 = no limit).',
        )
        parser.add_argument(
            '--timeout',
            type=int,
            default=DEFAULT_TIMEOUT,
            help=f'Per-image HTTP request timeout in seconds (default {DEFAULT_TIMEOUT}).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        timeout = options['timeout']

        try:
            from PIL import Image  # noqa: PLC0415
            import requests  # noqa: PLC0415
        except ImportError as exc:
            self.stderr.write(f'Missing dependency: {exc}. Run: pip install pillow requests')
            return

        media_dir = _ensure_media_dir(dry_run)

        notices = RawSsafyData.objects.filter(source_type='notice').order_by('id')
        total = notices.count()
        if limit:
            notices = notices[:limit]

        stats = {
            'total_notices': total,
            'processed': 0,
            'already_local': 0,
            'downloaded': 0,
            'optimised': 0,
            'skipped_origin': 0,
            'failed': 0,
            'failed_items': [],
        }

        for notice in notices:
            try:
                _process_notice(notice, media_dir, dry_run, timeout, Image, requests, stats)
            except Exception as exc:
                LOGGER.warning('backfill_notice_images: id=%s unexpected error: %s', notice.id, exc)
                stats['failed'] += 1
                stats['failed_items'].append({'id': notice.id, 'error': str(exc)})

        self._print_results(stats, dry_run)

    def _print_results(self, stats, dry_run):
        prefix = '[DRY-RUN] ' if dry_run else ''
        self.stdout.write('')
        self.stdout.write(f'{prefix}전체 notice: {stats["total_notices"]}')
        self.stdout.write(f'{prefix}처리 대상:   {stats["processed"]}')
        self.stdout.write(f'{prefix}이미 로컬:   {stats["already_local"]}')
        self.stdout.write(f'{prefix}다운로드:    {stats["downloaded"]}')
        self.stdout.write(f'{prefix}WebP 변환:   {stats["optimised"]}')
        self.stdout.write(f'{prefix}출처 스킵:   {stats["skipped_origin"]}')
        self.stdout.write(f'{prefix}실패:        {stats["failed"]}')

        if stats['failed_items']:
            self.stdout.write('')
            self.stdout.write('--- 실패 목록 (최대 20건) ---')
            for item in stats['failed_items'][:20]:
                self.stdout.write(f'  id={item["id"]} error={item["error"]}')

        if dry_run:
            self.stdout.write('')
            self.stdout.write(
                '--dry-run 모드: 실제 파일 저장/DB 변경이 없었습니다. '
                '--dry-run 없이 재실행하면 변환이 적용됩니다.'
            )


def _process_notice(notice, media_dir, dry_run, timeout, Image, requests, stats):
    metadata = dict(notice.metadata_json or {})
    image_urls = list(metadata.get('image_urls') or [])
    if not image_urls:
        return

    stats['processed'] += 1
    new_urls = []
    metadata_changed = False

    for idx, url in enumerate(image_urls):
        url = str(url).strip()
        if not url:
            new_urls.append(url)
            continue

        # Already stored locally
        if _is_local_url(url):
            stats['already_local'] += 1
            new_urls.append(url)
            continue

        # SSRF guard: only process allowed origins
        if not _is_allowed_origin(url):
            LOGGER.info('backfill_notice_images: blocked origin id=%s url=%s', notice.id, url)
            stats['skipped_origin'] += 1
            new_urls.append(url)
            continue

        if dry_run:
            stats['downloaded'] += 1
            stats['optimised'] += 1
            new_urls.append(url)
            continue

        local_url = _download_and_optimise(url, idx, notice.id, media_dir, timeout, Image, requests, stats)
        if local_url:
            new_urls.append(local_url)
            if local_url != url:
                metadata_changed = True
        else:
            new_urls.append(url)

    if metadata_changed and not dry_run:
        metadata['image_urls'] = new_urls
        notice.metadata_json = metadata
        notice.save(update_fields=['metadata_json'])


def _download_and_optimise(url, idx, notice_id, media_dir, timeout, Image, requests, stats):
    try:
        resp = requests.get(url, timeout=timeout, stream=True)
        resp.raise_for_status()
        raw_bytes = resp.content
    except Exception as exc:
        LOGGER.warning('backfill_notice_images: download failed id=%s url=%s error=%s', notice_id, url, exc)
        stats['failed'] += 1
        stats['failed_items'].append({'id': notice_id, 'error': f'download:{exc}'})
        return None

    stats['downloaded'] += 1

    # Deduplicate by content hash
    content_hash = hashlib.sha1(raw_bytes).hexdigest()[:16]
    filename_base = f'notice-{notice_id}-{idx}-{content_hash}'

    # Check if already converted
    webp_path = media_dir / f'{filename_base}{OPTIMIZED_SUFFIX}.webp'
    if webp_path.exists():
        return _media_relative_url(webp_path)

    try:
        import io
        img = Image.open(io.BytesIO(raw_bytes))
        img = img.convert('RGB')

        # Resize if wider than max
        if img.width > IMAGE_MAX_WIDTH:
            ratio = IMAGE_MAX_WIDTH / img.width
            new_height = int(img.height * ratio)
            img = img.resize((IMAGE_MAX_WIDTH, new_height), Image.LANCZOS)

        img.save(webp_path, 'WEBP', quality=IMAGE_QUALITY, method=6)
        stats['optimised'] += 1
        return _media_relative_url(webp_path)
    except Exception as exc:
        LOGGER.warning('backfill_notice_images: optimise failed id=%s error=%s', notice_id, exc)
        # Fall back to saving original
        try:
            orig_ext = _guess_extension(url)
            orig_path = media_dir / f'{filename_base}{orig_ext}'
            orig_path.write_bytes(raw_bytes)
            return _media_relative_url(orig_path)
        except Exception as write_exc:
            LOGGER.warning('backfill_notice_images: save original failed id=%s error=%s', notice_id, write_exc)
            stats['failed'] += 1
            stats['failed_items'].append({'id': notice_id, 'error': f'optimise:{exc}'})
            return None


def _ensure_media_dir(dry_run):
    media_root = Path(settings.MEDIA_ROOT)
    target = media_root / MEDIA_NOTICE_DIR
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)
    return target


def _is_local_url(url):
    lowered = url.lower()
    return lowered.startswith('/media/') or lowered.startswith('/static/')


def _is_allowed_origin(url):
    try:
        host = urlparse(url).hostname or ''
    except Exception:
        return False
    return any(host == origin or host.endswith(f'.{origin}') for origin in ALLOWED_IMAGE_ORIGINS)


def _media_relative_url(path):
    media_root = Path(settings.MEDIA_ROOT)
    try:
        rel = path.relative_to(media_root)
        return f'/media/{rel.as_posix()}'
    except ValueError:
        return f'/media/{path.name}'


def _guess_extension(url):
    path = urlparse(url).path.lower()
    for ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
        if path.endswith(ext):
            return ext
    return '.jpg'
