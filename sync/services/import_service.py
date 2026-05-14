from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from schedules.models import ScheduleEvent
from sync.models import CrawlJobLog, RawSsafyData
from sync.services.ocr_service import extract_text_from_image_urls
from sync.services.schedule_parser import parse_schedule_candidates
from sync.services.ssafy_crawler import (
    MODE_SAMPLE,
    SsafyCrawlerError,
    extract_image_urls_from_html,
    get_crawler_mode,
    load_notices_by_mode,
)


SUCCESS_MESSAGE = 'SSAFY notice collection and schedule import completed.'
CRAWL_FAILED_MESSAGE = 'Failed to collect SSAFY notices.'


@dataclass
class ImportSummary:
    raw_count: int = 0
    event_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    notice_count: int = 0
    academic_rule_count: int = 0
    no_schedule_count: int = 0
    image_count: int = 0
    ocr_processed_count: int = 0
    ocr_failed_count: int = 0
    event_skipped_count: int = 0
    no_schedule_by_type: dict = field(default_factory=dict)
    failed_items: list = field(default_factory=list)


def run_sample_notice_import():
    return run_notice_import(mode=MODE_SAMPLE)


def run_notice_import(mode=None):
    job_log = CrawlJobLog.objects.create(
        status=CrawlJobLog.STATUS_RUNNING,
    )

    try:
        selected_mode = get_crawler_mode(mode)
        job_log.crawler_mode = selected_mode
        raw_items = load_notices_by_mode(selected_mode)
        summary = _import_raw_items(raw_items)

        job_log.status = CrawlJobLog.STATUS_SUCCESS
        job_log.message = _build_success_message(selected_mode, summary)
        job_log.raw_count = summary.raw_count
        job_log.event_count = summary.event_count
        job_log.failed_count = summary.failed_count
        job_log.skipped_count = summary.skipped_count
        job_log.notice_count = summary.notice_count
        job_log.academic_rule_count = summary.academic_rule_count
        job_log.no_schedule_count = summary.no_schedule_count
        job_log.image_count = summary.image_count
        job_log.ocr_processed_count = summary.ocr_processed_count
        job_log.ocr_failed_count = summary.ocr_failed_count
        job_log.finished_at = timezone.now()
        job_log.save()
        return job_log
    except (SsafyCrawlerError, ValueError) as exc:
        return _mark_job_failed(job_log, f'{CRAWL_FAILED_MESSAGE} {exc}')
    except Exception as exc:
        return _mark_job_failed(job_log, str(exc))


@transaction.atomic
def _import_raw_items(raw_items):
    summary = ImportSummary()

    for item in raw_items:
        if _find_existing_raw_data(item):
            summary.skipped_count += 1
            continue

        item = _apply_ocr_pipeline(item, summary)
        raw_data = _create_raw_data(item)

        summary.raw_count += 1
        _increment_source_count(summary, raw_data.source_type)

        try:
            if raw_data.source_type != 'notice':
                _mark_no_schedule(raw_data, summary)
                continue

            parsed_schedules = parse_schedule_candidates(raw_data.raw_text, default_title=raw_data.title)
            if not parsed_schedules:
                _mark_no_schedule(raw_data, summary)
                continue

            for schedule in parsed_schedules:
                if _find_existing_schedule_event(schedule, raw_data):
                    summary.skipped_count += 1
                    summary.event_skipped_count += 1
                    continue

                ScheduleEvent.objects.create(
                    raw_data=raw_data,
                    title=schedule.title,
                    start_at=schedule.start_at,
                    description=schedule.description,
                    end_at=schedule.end_at,
                    is_all_day=schedule.is_all_day,
                    event_type=schedule.event_type,
                    source_type=raw_data.source_type,
                    source_id=str(raw_data.pk),
                )
                summary.event_count += 1

            raw_data.status = RawSsafyData.STATUS_PARSED
            raw_data.save(update_fields=['status'])
        except Exception as exc:
            summary.failed_count += 1
            raw_data.status = RawSsafyData.STATUS_FAILED
            raw_data.save(update_fields=['status'])
            summary.failed_items.append(f'{raw_data.source_type}:{exc.__class__.__name__}')

    return summary


def _increment_source_count(summary, source_type):
    if source_type == 'notice':
        summary.notice_count += 1
    elif source_type == 'academic_rule':
        summary.academic_rule_count += 1


def _mark_no_schedule(raw_data, summary):
    summary.no_schedule_count += 1
    summary.no_schedule_by_type[raw_data.source_type] = summary.no_schedule_by_type.get(raw_data.source_type, 0) + 1
    raw_data.status = RawSsafyData.STATUS_PARSED
    raw_data.save(update_fields=['status'])


def _build_success_message(selected_mode, summary):
    message = (
        f'{SUCCESS_MESSAGE} mode={selected_mode}, '
        f'notice_count={summary.notice_count}, '
        f'academic_rule_count={summary.academic_rule_count}, '
        f'no_schedule_candidates={summary.no_schedule_count}, '
        f'image_count={summary.image_count}, '
        f'ocr_processed_count={summary.ocr_processed_count}, '
        f'ocr_failed_count={summary.ocr_failed_count}, '
        f'failed_count={summary.failed_count}, '
        f'skipped_count={summary.skipped_count}'
    )
    if summary.event_skipped_count:
        message = f'{message}, event_skipped_count={summary.event_skipped_count}'
    if summary.no_schedule_by_type:
        no_schedule_detail = ','.join(
            f'{source_type}:{count}' for source_type, count in sorted(summary.no_schedule_by_type.items())
        )
        message = f'{message}, no_schedule_by_type={no_schedule_detail}'
    if summary.failed_items:
        message = f'{message}, failed_items={";".join(summary.failed_items[:5])}'
    return message


def _apply_ocr_pipeline(item, summary):
    prepared = dict(item)
    metadata = dict(prepared.get('metadata_json') or {})
    image_urls = metadata.get('image_urls')
    if image_urls is None:
        image_urls = extract_image_urls_from_html(prepared.get('raw_html', ''), prepared.get('source_url', ''))
    metadata['image_urls'] = image_urls
    summary.image_count += len(image_urls)

    ocr_result = _safe_extract_ocr_text(image_urls)
    if image_urls:
        summary.ocr_processed_count += 1
    summary.ocr_failed_count += ocr_result.get(
        'ocr_failed_count',
        1 if ocr_result.get('ocr_status') == 'failed' else 0,
    )

    ocr_text = ocr_result.get('ocr_text', '')
    metadata.update(
        {
            'ocr_provider': ocr_result.get('ocr_provider', 'mock'),
            'ocr_status': ocr_result.get('ocr_status', 'skipped'),
            'ocr_error': ocr_result.get('ocr_error', ''),
            'ocr_text_length': len(ocr_text),
            'ocr_failed_count': ocr_result.get('ocr_failed_count', 0),
        }
    )
    prepared['metadata_json'] = metadata
    prepared['raw_text'] = _merge_ocr_text(prepared.get('raw_text', ''), ocr_text)
    return prepared


def _safe_extract_ocr_text(image_urls):
    try:
        return extract_text_from_image_urls(image_urls)
    except Exception as exc:
        return {
            'ocr_text': '',
            'ocr_provider': 'mock',
            'ocr_status': 'failed',
            'ocr_error': str(exc),
            'ocr_failed_count': 1,
        }


def _merge_ocr_text(raw_text, ocr_text):
    if not ocr_text:
        return raw_text
    if raw_text:
        return f'{raw_text}\n\n[OCR_TEXT]\n{ocr_text}'
    return f'[OCR_TEXT]\n{ocr_text}'


def _create_raw_data(item):
    return RawSsafyData.objects.create(
        source_type=item.get('source_type', 'notice'),
        source_url=item.get('source_url', ''),
        title=item.get('title', ''),
        raw_text=item.get('raw_text', ''),
        raw_html=item.get('raw_html', ''),
        status=RawSsafyData.STATUS_COLLECTED,
        metadata_json=item.get('metadata_json', {}),
        collected_at=timezone.now(),
    )


def _find_existing_schedule_event(schedule, raw_data):
    event_filter = {
        'title': schedule.title,
        'start_at': schedule.start_at,
        'end_at': schedule.end_at,
        'event_type': schedule.event_type,
        'source_type': raw_data.source_type,
    }

    if raw_data.pk:
        existing_for_raw_data = ScheduleEvent.objects.filter(raw_data=raw_data, **event_filter).first()
        if existing_for_raw_data:
            return existing_for_raw_data

    return ScheduleEvent.objects.filter(**event_filter).first()


def _find_existing_raw_data(item):
    source_url = item.get('source_url')
    if source_url:
        existing = RawSsafyData.objects.filter(source_url=source_url).first()
        if existing:
            return existing

    notice_id = item.get('metadata_json', {}).get('notice_id')
    if notice_id:
        existing = RawSsafyData.objects.filter(metadata_json__notice_id=notice_id).first()
        if existing:
            return existing

    return RawSsafyData.objects.filter(
        source_type=item.get('source_type', 'notice'),
        title=item.get('title', ''),
    ).first()


def _mark_job_failed(job_log, message):
    job_log.status = CrawlJobLog.STATUS_FAILED
    job_log.message = message
    job_log.raw_count = 0
    job_log.event_count = 0
    job_log.failed_count = 1
    job_log.skipped_count = 0
    job_log.notice_count = 0
    job_log.academic_rule_count = 0
    job_log.no_schedule_count = 0
    job_log.image_count = 0
    job_log.ocr_processed_count = 0
    job_log.ocr_failed_count = 0
    job_log.finished_at = timezone.now()
    job_log.save()
    return job_log
