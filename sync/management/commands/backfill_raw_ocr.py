from dataclasses import dataclass

from django.core.management.base import BaseCommand
from django.db import transaction

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
        parser.add_argument('--dry-run', action='store_true', help='Inspect rows without OCR calls or DB writes.')
        parser.add_argument('--limit', type=int, help='Maximum number of RawSsafyData rows to inspect.')
        parser.add_argument('--reparse', action='store_true', help='Reparse updated RawSsafyData rows into ScheduleEvent rows.')

    def handle(self, *args, **options):
        queryset = RawSsafyData.objects.all().order_by('id')
        if options.get('id'):
            queryset = queryset.filter(pk=options['id'])
        source_types = options.get('source_type')
        if source_types:
            queryset = queryset.filter(source_type__in=source_types)
        if options.get('limit') is not None:
            queryset = queryset[: options['limit']]

        summary = _backfill_queryset(
            queryset=queryset,
            dry_run=options['dry_run'],
            reparse=options['reparse'],
        )

        message = (
            'Backfill OCR completed.\n'
            f'raw_checked={summary.raw_checked}\n'
            f'image_count={summary.image_count}\n'
            f'ocr_processed_count={summary.ocr_processed_count}\n'
            f'ocr_failed_count={summary.ocr_failed_count}\n'
            f'updated_count={summary.updated_count}\n'
            f'no_image_count={summary.no_image_count}\n'
            f'reparse_created_count={summary.reparse_created_count}\n'
            f'dry_run={str(summary.dry_run).lower()}'
        )
        if summary.ocr_errors:
            message = f'{message}\nocr_errors={"; ".join(summary.ocr_errors[:3])}'
        self.stdout.write(self.style.SUCCESS(message))


def _backfill_queryset(queryset, dry_run=False, reparse=False):
    summary = BackfillSummary(dry_run=dry_run)
    summary.ocr_errors = []
    updated_raw_ids = []

    with transaction.atomic():
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

            if dry_run:
                continue

            ocr_result = _safe_extract_text(image_urls)
            if ocr_result.get('ocr_error'):
                summary.ocr_errors.append(f'raw_id={raw_data.id}: {ocr_result["ocr_error"]}')
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

        if dry_run:
            transaction.set_rollback(True)

    if reparse and updated_raw_ids and not dry_run:
        reparse_summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(id__in=updated_raw_ids))
        summary.reparse_created_count = reparse_summary.created_count

    return summary


def _collect_image_urls(raw_data):
    urls = []
    for image_url in raw_data.metadata_json.get('image_urls') or []:
        if image_url and image_url not in urls:
            urls.append(image_url)

    for image_url in extract_image_urls_from_html(raw_data.raw_html, raw_data.source_url):
        if image_url and image_url not in urls:
            urls.append(image_url)
    return urls


def _safe_extract_text(image_urls):
    try:
        return extract_text_from_image_urls(image_urls)
    except Exception as exc:
        return {
            'ocr_text': '',
            'ocr_provider': 'unknown',
            'ocr_status': 'failed',
            'ocr_error': str(exc)[:300],
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
            'ocr_text_length': len(ocr_text),
            'ocr_failed_count': ocr_result.get('ocr_failed_count', 0),
            'ocr_box_count': len(ocr_boxes),
        }
    )
    raw_data.ocr_boxes = ocr_boxes
    raw_data.metadata_json = metadata


def _replace_ocr_text(raw_text, ocr_text):
    base_text = raw_text
    marker_index = base_text.find(OCR_SECTION_MARKER)
    if marker_index >= 0:
        base_text = base_text[:marker_index].rstrip()
    if base_text:
        return f'{base_text}\n\n{OCR_SECTION_MARKER}\n{ocr_text}'
    return f'{OCR_SECTION_MARKER}\n{ocr_text}'
