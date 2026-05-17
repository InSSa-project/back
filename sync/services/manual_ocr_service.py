from dataclasses import dataclass

from django.db import transaction

from sync.models import RawSsafyData
from sync.services.reparse_service import reparse_raw_data_to_events


OCR_SECTION_MARKER = '[OCR_TEXT]'
MANUAL_OCR_PROVIDER = 'manual'


@dataclass
class ManualOcrResult:
    raw_data_id: int
    ocr_provider: str
    ocr_status: str
    ocr_text_length: int
    updated: bool
    dry_run: bool
    reparse_created_count: int = 0


def apply_manual_ocr_text(raw_data, ocr_text, reparse=False, dry_run=False):
    normalized_text = (ocr_text or '').strip()
    if not normalized_text:
        raise ValueError('ocr_text is required.')

    with transaction.atomic():
        metadata = _build_manual_ocr_metadata(raw_data, normalized_text)
        if not dry_run:
            raw_data.raw_text = replace_ocr_text(raw_data.raw_text, normalized_text)
            raw_data.ocr_boxes = []
            raw_data.metadata_json = metadata
            raw_data.save(update_fields=['raw_text', 'metadata_json', 'ocr_boxes'])
        else:
            transaction.set_rollback(True)

    reparse_created_count = 0
    if reparse and not dry_run:
        reparse_summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(pk=raw_data.pk))
        reparse_created_count = reparse_summary.created_count

    return ManualOcrResult(
        raw_data_id=raw_data.pk,
        ocr_provider=MANUAL_OCR_PROVIDER,
        ocr_status='success',
        ocr_text_length=len(normalized_text),
        updated=not dry_run,
        dry_run=dry_run,
        reparse_created_count=reparse_created_count,
    )


def replace_ocr_text(raw_text, ocr_text):
    base_text = raw_text or ''
    marker_index = base_text.find(OCR_SECTION_MARKER)
    if marker_index >= 0:
        base_text = base_text[:marker_index].rstrip()
    if base_text:
        return f'{base_text}\n\n{OCR_SECTION_MARKER}\n{ocr_text}'
    return f'{OCR_SECTION_MARKER}\n{ocr_text}'


def _build_manual_ocr_metadata(raw_data, ocr_text):
    metadata = dict(raw_data.metadata_json or {})
    metadata.update(
        {
            'ocr_provider': MANUAL_OCR_PROVIDER,
            'ocr_status': 'success',
            'ocr_error': '',
            'ocr_text_length': len(ocr_text),
            'ocr_failed_count': 0,
            'ocr_box_count': 0,
        }
    )
    return metadata
