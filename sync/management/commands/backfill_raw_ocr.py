from dataclasses import dataclass

from django.core.management.base import BaseCommand

from sync.models import RawSsafyData
from sync.services.ocr_service import extract_text_from_image_urls
from sync.services.reparse_service import reparse_raw_data_to_events
from sync.services.ssafy_crawler import extract_image_urls_from_html


OCR_SECTION_MARKER = '[OCR_TEXT]'


@dataclass
class BackfillSummary:
    raw_checked: int = 0
    image_count: int = 0
    ocr_processed_count: int = 0
    ocr_failed_count: int = 0
    updated_count: int = 0
    skipped_existing_ocr_count: int = 0
    forced_count: int = 0
    no_image_count: int = 0
    reparse_created_count: int = 0
    dry_run: bool = False
    ocr_errors: list = None


class Command(BaseCommand):
    help = 'Backfill OCR text for existing RawSsafyData rows and optionally reparse schedules.'

    def add_arguments(self, parser):
        parser.add_argument('--id', type=int, help='Only process one RawSsafyData id.')
        parser.add_argument(
            '--source-type',
            action='append',
            choices=['notice', 'academic_rule'],
            help='RawSsafyData source_type to include. Can be passed multiple times.',
        )
        parser.add_argument('--category', help='Filter by metadata_json.category.')
        parser.add_argument('--document-type', help='Filter by metadata_json.document_type.')
        parser.add_argument('--dry-run', action='store_true', help='Inspect rows without OCR calls or DB writes.')
        parser.add_argument('--limit', type=int, help='Maximum number of RawSsafyData rows to inspect.')
        parser.add_argument('--reparse', action='store_true', help='Reparse updated RawSsafyData rows into ScheduleEvent rows.')
        parser.add_argument(
            '--force',
            action='store_true',
            help='Run OCR again even when successful OCR text and boxes are already stored.',
        )
        parser.add_argument(
            '--authenticated-download',
            action='store_true',
            help='Download SSAFY images through an authenticated Playwright request context.',
        )

    def handle(self, *args, **options):
        queryset = RawSsafyData.objects.all().order_by('id')
        if options.get('id'):
            queryset = queryset.filter(pk=options['id'])
        source_types = options.get('source_type')
        if source_types:
            queryset = queryset.filter(source_type__in=source_types)
        if options.get('category'):
            queryset = queryset.filter(metadata_json__category=options['category'])
        if options.get('document_type'):
            queryset = queryset.filter(metadata_json__document_type=options['document_type'])
        if options.get('limit') is not None:
            queryset = queryset[: options['limit']]

        image_downloader = None
        authenticated_context = None
        if options.get('authenticated_download') and not options['dry_run']:
            # sync_playwright uses asyncio internally which triggers Django's async safety
            # checks on all DB operations. Allow sync DB calls for this management command.
            import os as _os
            _os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', '1')
            authenticated_context = _open_authenticated_context()
            image_downloader = _build_authenticated_image_downloader(authenticated_context)

        try:
            summary = _backfill_queryset(
                queryset=queryset,
                dry_run=options['dry_run'],
                reparse=options['reparse'],
                force=options['force'],
                image_downloader=image_downloader,
            )
        finally:
            if authenticated_context:
                authenticated_context['context'].close()
                authenticated_context['browser'].close()
                authenticated_context['playwright'].stop()

        message = (
            'Backfill OCR completed.\n'
            f'raw_checked={summary.raw_checked}\n'
            f'image_count={summary.image_count}\n'
            f'ocr_processed_count={summary.ocr_processed_count}\n'
            f'ocr_failed_count={summary.ocr_failed_count}\n'
            f'updated_count={summary.updated_count}\n'
            f'skipped_existing_ocr_count={summary.skipped_existing_ocr_count}\n'
            f'forced_count={summary.forced_count}\n'
            f'no_image_count={summary.no_image_count}\n'
            f'reparse_created_count={summary.reparse_created_count}\n'
            f'dry_run={str(summary.dry_run).lower()}'
        )
        if summary.ocr_errors:
            message = f'{message}\nocr_errors={"; ".join(summary.ocr_errors[:3])}'
        self.stdout.write(self.style.SUCCESS(message))


def _backfill_queryset(queryset, dry_run=False, reparse=False, force=False, image_downloader=None):
    summary = BackfillSummary(dry_run=dry_run)
    summary.ocr_errors = []
    updated_raw_ids = []

    for raw_data in queryset:
        summary.raw_checked += 1
        image_urls = _collect_image_urls(raw_data)
        summary.image_count += len(image_urls)

        if not image_urls:
            summary.no_image_count += 1
            if not dry_run:
                _update_metadata(raw_data, image_urls=image_urls, ocr_result=None)
                raw_data.save(update_fields=['metadata_json', 'ocr_boxes'])
            continue

        if _has_existing_ocr_boxes(raw_data) and not force:
            summary.skipped_existing_ocr_count += 1
            continue
        if force:
            summary.forced_count += 1

        if dry_run:
            continue

        ocr_result = _safe_extract_text(image_urls, image_downloader=image_downloader)
        if ocr_result.get('ocr_error') or ocr_result.get('ocr_error_type'):
            summary.ocr_errors.append(_format_ocr_debug(raw_data, image_urls, ocr_result))
        summary.ocr_processed_count += len(image_urls)
        summary.ocr_failed_count += ocr_result.get(
            'ocr_failed_count',
            1 if ocr_result.get('ocr_status') == 'failed' else 0,
        )

        ocr_text = ocr_result.get('ocr_text', '')
        _update_metadata(raw_data, image_urls=image_urls, ocr_result=ocr_result)
        if ocr_text:
            raw_data.raw_text = _replace_ocr_text(raw_data.raw_text, ocr_text)
            raw_data.save(update_fields=['raw_text', 'metadata_json', 'ocr_boxes'])
            summary.updated_count += 1
            updated_raw_ids.append(raw_data.id)
        else:
            raw_data.save(update_fields=['metadata_json', 'ocr_boxes'])

    if reparse and updated_raw_ids and not dry_run:
        reparse_summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(id__in=updated_raw_ids))
        summary.reparse_created_count = reparse_summary.created_count

    return summary


def _has_existing_ocr_boxes(raw_data):
    metadata = raw_data.metadata_json or {}
    return (
        metadata.get('ocr_status') == 'success'
        and bool(raw_data.ocr_boxes)
    )


def _collect_image_urls(raw_data):
    urls = []
    for image_url in raw_data.metadata_json.get('image_urls') or []:
        if image_url and image_url not in urls:
            urls.append(image_url)

    for image_url in extract_image_urls_from_html(raw_data.raw_html, raw_data.source_url):
        if image_url and image_url not in urls:
            urls.append(image_url)
    return urls


def _safe_extract_text(image_urls, image_downloader=None):
    try:
        if image_downloader is None:
            return extract_text_from_image_urls(image_urls)
        return extract_text_from_image_urls(image_urls, image_downloader=image_downloader)
    except Exception as exc:
        return {
            'ocr_text': '',
            'ocr_provider': 'unknown',
            'ocr_status': 'failed',
            'ocr_error': str(exc)[:300],
            'ocr_error_type': 'unknown',
            'ocr_failed_count': len(image_urls),
            'ocr_boxes': [],
        }


def _update_metadata(raw_data, image_urls, ocr_result):
    metadata = dict(raw_data.metadata_json or {})
    metadata['image_urls'] = image_urls
    if ocr_result is None:
        metadata.setdefault('ocr_status', 'skipped')
        metadata['ocr_text_length'] = 0
        metadata['ocr_box_count'] = 0
        metadata['ocr_error_type'] = ''
        raw_data.ocr_boxes = []
        raw_data.metadata_json = metadata
        return

    ocr_text = ocr_result.get('ocr_text', '')
    ocr_boxes = ocr_result.get('ocr_boxes') or []
    metadata.update(
        {
            'ocr_provider': ocr_result.get('ocr_provider', 'mock'),
            'ocr_status': ocr_result.get('ocr_status', 'skipped'),
            'ocr_error': ocr_result.get('ocr_error', ''),
            'ocr_error_type': ocr_result.get('ocr_error_type', ''),
            'ocr_text_length': len(ocr_text),
            'ocr_failed_count': ocr_result.get('ocr_failed_count', 0),
            'ocr_box_count': len(ocr_boxes),
        }
    )
    raw_data.ocr_boxes = ocr_boxes
    raw_data.metadata_json = metadata


def _format_ocr_debug(raw_data, image_urls, ocr_result):
    return (
        f'raw_id={raw_data.id} title={raw_data.title} source_url={raw_data.source_url or "-"} '
        f'image_url={(image_urls or ["-"])[0]} error_type={ocr_result.get("ocr_error_type") or "unknown"} '
        f'error={ocr_result.get("ocr_error", "")}'
    )


def _replace_ocr_text(raw_text, ocr_text):
    base_text = raw_text
    marker_index = base_text.find(OCR_SECTION_MARKER)
    if marker_index >= 0:
        base_text = base_text[:marker_index].rstrip()
    if base_text:
        return f'{base_text}\n\n{OCR_SECTION_MARKER}\n{ocr_text}'
    return f'{OCR_SECTION_MARKER}\n{ocr_text}'


def _open_authenticated_context():
    import os

    from playwright.sync_api import sync_playwright

    from sync.services.ssafy_crawler import _login_ssafy

    login_url = os.getenv('SSAFY_LOGIN_URL')
    ssafy_id = os.getenv('SSAFY_ID')
    ssafy_password = os.getenv('SSAFY_PASSWORD')
    if not all([login_url, ssafy_id, ssafy_password]):
        raise ValueError('SSAFY_LOGIN_URL, SSAFY_ID, and SSAFY_PASSWORD are required for authenticated image download.')

    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(15000)
    _login_ssafy(page, login_url, ssafy_id, ssafy_password)
    return {'playwright': playwright, 'browser': browser, 'context': context}


def _build_authenticated_image_downloader(authenticated_context):
    request_context = authenticated_context['context'].request

    def download(image_url):
        response = request_context.get(image_url, timeout=10000)
        content = response.body()
        return {
            'content': content,
            'detail': {
                'image_download_status': response.status,
                'mime_type': (response.headers or {}).get('content-type', '').split(';')[0],
                'image_size': len(content or b''),
                'image_file_path': '',
            },
        }

    return download
