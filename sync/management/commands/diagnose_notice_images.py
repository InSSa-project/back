"""
Management command: diagnose_notice_images

Analyses the current state of notice image URLs in the database and local media
directory. Produces a structured report without modifying anything.

Usage:
    python manage.py diagnose_notice_images
    python manage.py diagnose_notice_images --json
"""

import json
import os
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand

from sync.models import RawSsafyData

SSAFY_CDN_HOSTS = {
    'edu.ssafy.com',
    'ssafy.com',
    's3.ap-northeast-2.amazonaws.com',
    'storage.googleapis.com',
}


class Command(BaseCommand):
    help = 'Analyse notice image URL state in the database without modifying anything.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', help='Output as JSON.')

    def handle(self, *args, **options):
        report = _build_report()
        if options['json']:
            self.stdout.write(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            _print_report(self.stdout, report)


def _build_report():
    media_root = Path(settings.MEDIA_ROOT)
    notices = RawSsafyData.objects.filter(source_type='notice').only('id', 'metadata_json')

    total_notices = 0
    total_urls = 0
    local_urls = 0
    ssafy_cdn_urls = 0
    supabase_storage_urls = 0
    other_external_urls = 0
    empty_urls = 0
    duplicate_urls = 0
    file_exists_count = 0
    file_missing_count = 0

    all_seen_urls: dict[str, list] = {}
    missing_file_samples = []
    unknown_host_samples = []

    for notice in notices:
        total_notices += 1
        metadata = notice.metadata_json or {}
        image_urls = _collect_image_urls(metadata)

        for url in image_urls:
            url = str(url or '').strip()
            total_urls += 1

            if not url:
                empty_urls += 1
                continue

            # Track duplicates
            all_seen_urls.setdefault(url, []).append(notice.id)

            if _is_local_url(url):
                local_urls += 1
                local_path = _local_path(url, media_root)
                if local_path and local_path.exists():
                    file_exists_count += 1
                else:
                    file_missing_count += 1
                    if len(missing_file_samples) < 10:
                        missing_file_samples.append({'notice_id': notice.id, 'url': url})
            elif _is_supabase_url(url):
                supabase_storage_urls += 1
            elif _is_ssafy_cdn_url(url):
                ssafy_cdn_urls += 1
            else:
                other_external_urls += 1
                host = _host(url)
                if host and len(unknown_host_samples) < 10:
                    unknown_host_samples.append({'notice_id': notice.id, 'url': url[:120], 'host': host})

    duplicate_urls = sum(1 for ids in all_seen_urls.values() if len(ids) > 1)

    return {
        'total_notices': total_notices,
        'total_image_urls': total_urls,
        'local_media_urls': local_urls,
        'local_file_exists': file_exists_count,
        'local_file_missing': file_missing_count,
        'supabase_storage_urls': supabase_storage_urls,
        'ssafy_cdn_urls': ssafy_cdn_urls,
        'other_external_urls': other_external_urls,
        'empty_urls': empty_urls,
        'duplicate_urls': duplicate_urls,
        'missing_file_samples': missing_file_samples,
        'unknown_host_samples': unknown_host_samples,
        'media_root': str(media_root),
        'media_root_exists': media_root.exists(),
        'debug_mode': bool(getattr(settings, 'DEBUG', False)),
        'supabase_configured': bool(
            getattr(settings, 'SUPABASE_URL', '') and getattr(settings, 'SUPABASE_SERVICE_ROLE_KEY', '')
        ),
        'supabase_bucket': getattr(settings, 'SUPABASE_STORAGE_BUCKET', '(not set)'),
    }


def _collect_image_urls(metadata):
    urls = []
    notice_images = metadata.get('notice_images')
    if isinstance(notice_images, list):
        for item in notice_images:
            if isinstance(item, dict):
                for key in ('storage_url', 'url', 'source_url'):
                    v = str(item.get(key) or '').strip()
                    if v:
                        urls.append(v)
                        break
            elif isinstance(item, str):
                urls.append(item)
        return urls

    for key in ('image_urls', 'images', 'image_url'):
        value = metadata.get(key)
        if isinstance(value, list):
            urls.extend(str(v or '') for v in value)
        elif value:
            urls.append(str(value))
    raw_json = metadata.get('raw_json') or {}
    if isinstance(raw_json, dict):
        for key in ('image_urls', 'images', 'image_url'):
            value = raw_json.get(key)
            if isinstance(value, list):
                urls.extend(str(v or '') for v in value)
    return urls


def _is_local_url(url):
    return url.lower().startswith('/media/') or url.lower().startswith('/static/')


def _is_supabase_url(url):
    host = _host(url)
    return host.endswith('.supabase.co') if host else False


def _is_ssafy_cdn_url(url):
    host = _host(url)
    return any(host == h or host.endswith(f'.{h}') for h in SSAFY_CDN_HOSTS) if host else False


def _host(url):
    try:
        return urlparse(url).hostname or ''
    except Exception:
        return ''


def _local_path(url, media_root):
    try:
        rel = url.lstrip('/')
        if rel.startswith('media/'):
            rel = rel[len('media/'):]
        return media_root / rel
    except Exception:
        return None


def _print_report(stdout, r):
    stdout.write('')
    stdout.write('=== 공지 이미지 진단 보고서 ===')
    stdout.write('')
    stdout.write(f'전체 공지:          {r["total_notices"]}')
    stdout.write(f'전체 이미지 URL:    {r["total_image_urls"]}')
    stdout.write('')
    stdout.write('[저장 위치별]')
    stdout.write(f'  로컬 /media/ URL: {r["local_media_urls"]}')
    stdout.write(f'    파일 존재:      {r["local_file_exists"]}')
    stdout.write(f'    파일 없음:      {r["local_file_missing"]}')
    stdout.write(f'  Supabase Storage: {r["supabase_storage_urls"]}')
    stdout.write(f'  SSAFY CDN:        {r["ssafy_cdn_urls"]}')
    stdout.write(f'  기타 외부 URL:    {r["other_external_urls"]}')
    stdout.write(f'  빈 URL:           {r["empty_urls"]}')
    stdout.write(f'  중복 URL:         {r["duplicate_urls"]}')
    stdout.write('')
    stdout.write('[환경 설정]')
    stdout.write(f'  MEDIA_ROOT:       {r["media_root"]}')
    stdout.write(f'  MEDIA_ROOT 존재:  {r["media_root_exists"]}')
    stdout.write(f'  DEBUG:            {r["debug_mode"]}')
    stdout.write(f'  Supabase 설정:    {r["supabase_configured"]}')
    stdout.write(f'  Supabase 버킷:    {r["supabase_bucket"]}')

    if r['local_file_missing'] > 0:
        stdout.write('')
        stdout.write(f'[파일 없는 URL 샘플 (최대 10건)]')
        for item in r['missing_file_samples']:
            stdout.write(f'  id={item["notice_id"]} url={item["url"]}')

    if r['unknown_host_samples']:
        stdout.write('')
        stdout.write('[허용 목록 외 외부 URL 샘플]')
        for item in r['unknown_host_samples']:
            stdout.write(f'  id={item["notice_id"]} host={item["host"]} url={item["url"]}')

    stdout.write('')
    if r['local_file_missing'] > 0 and not r['supabase_configured']:
        stdout.write('[경고] 로컬 파일이 없는 URL이 있고 Supabase도 미설정 상태입니다.')
        stdout.write('       SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY 설정 후 backfill_notice_images 실행 필요.')
    elif r['ssafy_cdn_urls'] > 0 and not r['supabase_configured']:
        stdout.write('[경고] SSAFY CDN URL이 아직 남아있습니다. Supabase 설정 후 backfill 실행 필요.')
    elif r['supabase_configured'] and r['ssafy_cdn_urls'] > 0:
        stdout.write('[권장] Supabase 설정됨. backfill_notice_images --dry-run 으로 이전 대상 확인 후 실행.')
    else:
        stdout.write('[정상] 모든 이미지가 영속 storage URL을 사용 중입니다.')
