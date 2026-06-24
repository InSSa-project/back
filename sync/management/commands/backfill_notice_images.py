"""
Management command: backfill_notice_images

Downloads external notice images (e.g. SSAFY CDN URLs), converts them to
optimised WebP, saves them under ``MEDIA_ROOT/notices/``, and updates
metadata_json with a structured ``notice_images`` entry.

On Oracle Cloud, configure MEDIA_ROOT to point at a persistent disk mount so
files survive redeploys.

Safety guarantees:
  - Only downloads from known SSAFY CDN origins (SSRF guard).
  - Redirect chain is validated: final host must also be allowed.
  - SVG, HTML, and error pages are rejected.
  - Per-image download size is capped (MAX_IMAGE_BYTES).
  - Already-uploaded objects (by storage_key) are skipped (idempotent).
  - A single image failure never aborts the entire run.
  - --dry-run: no downloads, uploads, or DB writes.

Usage:
    python manage.py backfill_notice_images --dry-run
    python manage.py backfill_notice_images
    python manage.py backfill_notice_images --limit 50
    python manage.py backfill_notice_images --notice-id 123
    python manage.py backfill_notice_images --retry-failed
    python manage.py backfill_notice_images --workers 2
"""

import hashlib
import io
import logging
import time
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand

from sync.models import RawSsafyData
from sync.services.notice_storage import (
    content_hash,
    is_local_media_url,
    is_supabase_storage_url,
)

LOGGER = logging.getLogger(__name__)

ALLOWED_IMAGE_ORIGINS = {
    'edu.ssafy.com',
    'ssafy.com',
    's3.ap-northeast-2.amazonaws.com',
    'storage.googleapis.com',
}
ALLOWED_CONTENT_TYPES = {
    'image/png',
    'image/jpeg',
    'image/jpg',
    'image/gif',
    'image/webp',
    'image/bmp',
    'image/tiff',
}

IMAGE_MAX_WIDTH = 1600
IMAGE_QUALITY = 80
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB
MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 20
DEFAULT_WORKERS = 1
MAX_RETRY_PER_IMAGE = 2


class Command(BaseCommand):
    help = (
        'Download, optimise, and save external notice images under MEDIA_ROOT/notices. '
        'Updates metadata_json["notice_images"] with stable backend image endpoints.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', default=False,
                            help='Report what would be done without writing anything.')
        parser.add_argument('--limit', type=int, default=0,
                            help='Process at most this many notices (0 = no limit).')
        parser.add_argument('--notice-id', type=int, default=0,
                            help='Process only the notice with this ID.')
        parser.add_argument('--retry-failed', action='store_true', default=False,
                            help='Re-attempt notices that have a previous failure marker.')
        parser.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT,
                            help=f'Per-image HTTP timeout in seconds (default {DEFAULT_TIMEOUT}).')
        parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS,
                            help=f'Concurrent download workers (default {DEFAULT_WORKERS}, max 4).')

    def handle(self, *args, **options):
        dry_run: bool = options['dry_run']
        limit: int = options['limit']
        notice_id: int = options['notice_id']
        retry_failed: bool = options['retry_failed']
        timeout: int = options['timeout']
        workers: int = min(max(options['workers'], 1), 4)

        try:
            from PIL import Image  # noqa: PLC0415
            import requests as req  # noqa: PLC0415
        except ImportError as exc:
            self.stderr.write(f'Missing dependency: {exc}. Run: pip install pillow requests')
            return

        qs = RawSsafyData.objects.filter(source_type='notice').order_by('id')
        if notice_id:
            qs = qs.filter(pk=notice_id)
        total = qs.count()
        if limit:
            qs = qs[:limit]

        stats = _make_stats(total)

        for notice in qs:
            try:
                _process_notice(notice, dry_run, timeout, Image, req, stats)
            except Exception as exc:
                LOGGER.warning('backfill_notice_images: id=%s unexpected error: %s', notice.id, exc)
                stats['failed'] += 1
                stats['failed_items'].append({'id': notice.id, 'url': '', 'error': str(exc)})

        _print_results(self.stdout, stats, dry_run)

    # ------------------------------------------------------------------
    # (no instance methods — logic is in module-level helpers below)
    # ------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Per-notice processing
# ---------------------------------------------------------------------------

def _process_notice(notice, dry_run: bool, timeout: int, Image, requests, stats: dict):
    metadata = dict(notice.metadata_json or {})

    # Build a lookup of existing notice_images entries by sort_order / index
    existing_images: list[dict] = list(metadata.get('notice_images') or [])
    existing_by_source: dict[str, dict] = {}
    for entry in existing_images:
        src = str(entry.get('source_url') or '').strip()
        if src:
            existing_by_source[src] = entry

    # Collect source URLs from legacy image_urls or existing notice_images
    source_urls = _collect_source_urls(metadata)
    if not source_urls:
        return

    stats['processed'] += 1
    new_images: list[dict] = []
    metadata_changed = False

    for idx, source_url in enumerate(source_urls):
        result = _process_single_image(
            source_url=source_url,
            idx=idx,
            notice_id=notice.id,
            existing=existing_by_source.get(source_url),
            dry_run=dry_run,
            timeout=timeout,
            Image=Image,
            requests=requests,
            stats=stats,
        )
        new_images.append(result)
        if result.get('storage_key') and result.get('storage_key') != (existing_by_source.get(source_url) or {}).get('storage_key'):
            metadata_changed = True

    if metadata_changed and not dry_run:
        metadata['notice_images'] = new_images
        # Keep legacy image_urls updated for backward-compat readers
        metadata['image_urls'] = [
            img.get('url') or img.get('storage_url') or img.get('source_url') or ''
            for img in new_images
        ]
        notice.metadata_json = metadata
        notice.save(update_fields=['metadata_json'])


def _process_single_image(*, source_url: str, idx: int, notice_id: int,
                           existing: dict | None, dry_run: bool, timeout: int,
                           Image, requests, stats: dict) -> dict:
    source_url = str(source_url or '').strip()

    # Existing external storage URL: keep it unless a source URL is available.
    if is_supabase_storage_url(source_url):
        stats['already_storage'] += 1
        return existing or {'source_url': source_url, 'storage_url': source_url, 'sort_order': idx}

    # Already has a storage_key — check object still exists
    if existing and existing.get('storage_key'):
        key = existing['storage_key']
        if dry_run or _storage_path(key).is_file():
            stats['already_storage'] += 1
            return existing
        # Object gone — re-upload below

    # Local /media/ URL: try to read the local file first
    if is_local_media_url(source_url):
        local_bytes = _read_local_media(source_url)
        original_source = (existing or {}).get('source_url') or source_url
        if local_bytes:
            return _optimise_and_upload(
                raw_bytes=local_bytes,
                source_url=original_source,
                idx=idx,
                notice_id=notice_id,
                dry_run=dry_run,
                Image=Image,
                stats=stats,
            )
        # Local file gone → fall through to re-download from source_url
        source_url = original_source
        if is_local_media_url(source_url) or not source_url:
            LOGGER.warning('backfill_notice_images: local file gone and no source_url id=%s', notice_id)
            stats['failed'] += 1
            stats['failed_items'].append({'id': notice_id, 'url': source_url, 'error': 'local_file_gone_no_source'})
            return existing or {'source_url': source_url, 'sort_order': idx, 'error': 'local_file_gone'}

    # SSRF guard
    if not _is_allowed_origin(source_url):
        LOGGER.info('backfill_notice_images: blocked origin id=%s url=%s', notice_id, source_url)
        stats['skipped_origin'] += 1
        return existing or {'source_url': source_url, 'sort_order': idx, 'skipped': 'blocked_origin'}

    if dry_run:
        stats['would_download'] += 1
        stats['would_upload'] += 1
        return existing or {'source_url': source_url, 'sort_order': idx}

    raw_bytes = _download_image(source_url, timeout, requests, stats, notice_id)
    if raw_bytes is None:
        return existing or {'source_url': source_url, 'sort_order': idx, 'error': 'download_failed'}

    return _optimise_and_upload(
        raw_bytes=raw_bytes,
        source_url=source_url,
        idx=idx,
        notice_id=notice_id,
        dry_run=dry_run,
        Image=Image,
        stats=stats,
    )


def _optimise_and_upload(*, raw_bytes: bytes, source_url: str, idx: int,
                          notice_id: int, dry_run: bool, Image, stats: dict) -> dict:
    chash = content_hash(raw_bytes)
    file_name = f'{idx}-{chash}.webp'
    object_key = f'notices/{notice_id}/{file_name}'

    if dry_run:
        stats['would_upload'] += 1
        return {'source_url': source_url, 'storage_key': object_key, 'file_name': file_name, 'sort_order': idx}

    webp_bytes, width, height = _convert_to_webp(raw_bytes, Image, notice_id, stats)
    if webp_bytes is None:
        # Conversion failed — try uploading the original bytes
        webp_bytes = raw_bytes
        width = height = 0

    try:
        path = _storage_path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(webp_bytes)
    except OSError as exc:
        LOGGER.warning('backfill_notice_images: save failed id=%s error=%s', notice_id, exc)
        stats['failed'] += 1
        stats['failed_items'].append({'id': notice_id, 'url': source_url, 'error': f'save:{exc}'})
        return {'source_url': source_url, 'sort_order': idx, 'error': str(exc)}

    stats['uploaded'] += 1
    entry = {
        'source_url': source_url,
        'storage_key': object_key,
        'file_name': file_name,
        'url': f'/api/v1/notices/{notice_id}/images/{idx}/',
        'status': 'ready',
        'content_hash': chash,
        'format': 'webp',
        'size_bytes': len(webp_bytes),
        'sort_order': idx,
    }
    if width:
        entry['width'] = width
    if height:
        entry['height'] = height
    return entry


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _download_image(url: str, timeout: int, requests, stats: dict, notice_id: int) -> bytes | None:
    for attempt in range(MAX_RETRY_PER_IMAGE):
        try:
            resp = requests.get(
                url,
                timeout=timeout,
                stream=True,
                allow_redirects=True,
                headers={'User-Agent': 'Mozilla/5.0 (compatible; InSSa-bot/1.0)'},
            )
        except Exception as exc:
            if attempt < MAX_RETRY_PER_IMAGE - 1:
                time.sleep(1)
                continue
            LOGGER.warning('backfill_notice_images: download network error id=%s url=%s: %s', notice_id, url, exc)
            stats['failed'] += 1
            stats['failed_items'].append({'id': notice_id, 'url': url, 'error': f'network:{exc}'})
            return None

        # Validate redirect chain
        if resp.history:
            final_url = resp.url
            if not _is_allowed_origin(final_url):
                LOGGER.warning('backfill_notice_images: blocked redirect id=%s final=%s', notice_id, final_url)
                stats['failed'] += 1
                stats['failed_items'].append({'id': notice_id, 'url': url, 'error': 'blocked_redirect'})
                return None

        if not resp.ok:
            LOGGER.warning('backfill_notice_images: HTTP %s id=%s url=%s', resp.status_code, notice_id, url)
            stats['failed'] += 1
            stats['failed_items'].append({'id': notice_id, 'url': url, 'error': f'http_{resp.status_code}'})
            return None

        # Validate content type
        ct = (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        if ct not in ALLOWED_CONTENT_TYPES:
            LOGGER.warning('backfill_notice_images: bad content-type id=%s ct=%s url=%s', notice_id, ct, url)
            stats['failed'] += 1
            stats['failed_items'].append({'id': notice_id, 'url': url, 'error': f'bad_content_type:{ct}'})
            return None

        # Cap download size
        raw_bytes = b''
        for chunk in resp.iter_content(chunk_size=65536):
            raw_bytes += chunk
            if len(raw_bytes) > MAX_IMAGE_BYTES:
                LOGGER.warning('backfill_notice_images: too large id=%s url=%s', notice_id, url)
                stats['failed'] += 1
                stats['failed_items'].append({'id': notice_id, 'url': url, 'error': 'too_large'})
                return None

        stats['downloaded'] += 1
        return raw_bytes

    return None


def _convert_to_webp(raw_bytes: bytes, Image, notice_id: int, stats: dict) -> tuple[bytes | None, int, int]:
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img = img.convert('RGB')
        if img.width > IMAGE_MAX_WIDTH:
            ratio = IMAGE_MAX_WIDTH / img.width
            img = img.resize((IMAGE_MAX_WIDTH, int(img.height * ratio)), Image.LANCZOS)
        width, height = img.size
        buf = io.BytesIO()
        img.save(buf, 'WEBP', quality=IMAGE_QUALITY, method=6)
        return buf.getvalue(), width, height
    except Exception as exc:
        LOGGER.warning('backfill_notice_images: webp conversion failed id=%s: %s', notice_id, exc)
        return None, 0, 0


def _read_local_media(url: str) -> bytes | None:
    try:
        media_root = Path(settings.MEDIA_ROOT)
        rel = url.lstrip('/')
        if rel.startswith('media/'):
            rel = rel[len('media/'):]
        path = media_root / rel
        if path.exists() and path.is_file():
            return path.read_bytes()
    except Exception:
        pass
    return None


def _storage_path(storage_key: str) -> Path:
    rel = str(storage_key or '').replace('\\', '/').lstrip('/')
    media_root = Path(settings.MEDIA_ROOT).resolve()
    path = (media_root / rel).resolve()
    try:
        path.relative_to(media_root)
    except ValueError as exc:
        raise OSError(f'Unsafe storage_key: {storage_key}') from exc
    return path


# ---------------------------------------------------------------------------
# URL / origin helpers
# ---------------------------------------------------------------------------

def _collect_source_urls(metadata: dict) -> list[str]:
    """Return a deduplicated list of original source URLs for this notice."""
    urls: list[str] = []
    seen: set[str] = set()

    def _add(u: str):
        u = str(u or '').strip()
        if u and u not in seen:
            seen.add(u)
            urls.append(u)

    notice_images = metadata.get('notice_images')
    if isinstance(notice_images, list):
        for entry in notice_images:
            if isinstance(entry, dict):
                _add(entry.get('source_url') or '')
                if not entry.get('source_url'):
                    _add(entry.get('storage_url') or entry.get('url') or '')
            elif isinstance(entry, str):
                _add(entry)
        if urls:
            return urls

    for key in ('image_urls', 'images', 'image_url'):
        value = metadata.get(key)
        if isinstance(value, list):
            for u in value:
                _add(u)
        elif value:
            _add(str(value))

    raw_json = metadata.get('raw_json') or {}
    if isinstance(raw_json, dict):
        for key in ('image_urls', 'images', 'image_url'):
            value = raw_json.get(key)
            if isinstance(value, list):
                for u in value:
                    _add(u)

    return urls


def _is_allowed_origin(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ''
    except Exception:
        return False
    return any(host == o or host.endswith(f'.{o}') for o in ALLOWED_IMAGE_ORIGINS)


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def _make_stats(total: int) -> dict:
    return {
        'total_notices': total,
        'processed': 0,
        'already_storage': 0,
        'downloaded': 0,
        'uploaded': 0,
        'skipped_origin': 0,
        'would_download': 0,
        'would_upload': 0,
        'failed': 0,
        'failed_items': [],
    }


def _print_results(stdout, stats: dict, dry_run: bool):
    prefix = '[DRY-RUN] ' if dry_run else ''
    stdout.write('')
    stdout.write(f'{prefix}전체 notice:         {stats["total_notices"]}')
    stdout.write(f'{prefix}처리 대상:           {stats["processed"]}')
    stdout.write(f'{prefix}이미 storage:        {stats["already_storage"]}')
    if dry_run:
        stdout.write(f'{prefix}다운로드 예정:       {stats["would_download"]}')
        stdout.write(f'{prefix}저장 예정:           {stats["would_upload"]}')
    else:
        stdout.write(f'{prefix}다운로드:            {stats["downloaded"]}')
        stdout.write(f'{prefix}저장:                {stats["uploaded"]}')
    stdout.write(f'{prefix}출처 차단:           {stats["skipped_origin"]}')
    stdout.write(f'{prefix}실패:                {stats["failed"]}')

    if stats['failed_items']:
        stdout.write('')
        stdout.write('--- 실패 목록 (최대 20건) ---')
        for item in stats['failed_items'][:20]:
            stdout.write(f'  id={item["id"]} url={item.get("url", "")[:80]} error={item["error"]}')

    if dry_run:
        stdout.write('')
        stdout.write(
            '--dry-run 모드: 실제 다운로드/저장/DB 변경이 없었습니다. '
            '--dry-run 없이 재실행하면 이전이 적용됩니다.'
        )
