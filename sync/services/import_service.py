from django.db import transaction
from django.utils import timezone

from schedules.models import ScheduleEvent
from sync.models import CrawlJobLog, RawSsafyData
from sync.services.schedule_parser import parse_schedule_candidates
from sync.services.ssafy_crawler import (
    MODE_SAMPLE,
    SsafyCrawlerError,
    get_crawler_mode,
    load_notices_by_mode,
)


SUCCESS_MESSAGE = 'SSAFY notice collection and schedule import completed.'
CRAWL_FAILED_MESSAGE = 'Failed to collect SSAFY notices.'


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
        raw_count, event_count, failed_count, skipped_count = _import_raw_items(raw_items)

        job_log.status = CrawlJobLog.STATUS_SUCCESS
        job_log.message = f'{SUCCESS_MESSAGE} mode={selected_mode}'
        job_log.raw_count = raw_count
        job_log.event_count = event_count
        job_log.failed_count = failed_count
        job_log.skipped_count = skipped_count
        job_log.finished_at = timezone.now()
        job_log.save()
        return job_log
    except (SsafyCrawlerError, ValueError) as exc:
        return _mark_job_failed(job_log, f'{CRAWL_FAILED_MESSAGE} {exc}')
    except Exception as exc:
        return _mark_job_failed(job_log, str(exc))


@transaction.atomic
def _import_raw_items(raw_items):
    raw_count = 0
    event_count = 0
    failed_count = 0
    skipped_count = 0

    for item in raw_items:
        raw_data, created = _create_raw_data_if_new(item)
        if not created:
            skipped_count += 1
            continue

        raw_count += 1
        if raw_data.source_type != 'notice':
            raw_data.status = RawSsafyData.STATUS_PARSED
            raw_data.save(update_fields=['status'])
            continue

        parsed_schedules = parse_schedule_candidates(raw_data.raw_text, default_title=raw_data.title)
        if not parsed_schedules:
            failed_count += 1
            raw_data.status = RawSsafyData.STATUS_FAILED
            raw_data.save(update_fields=['status'])
            continue

        for schedule in parsed_schedules:
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
            event_count += 1

        raw_data.status = RawSsafyData.STATUS_PARSED
        raw_data.save(update_fields=['status'])

    return raw_count, event_count, failed_count, skipped_count


def _create_raw_data_if_new(item):
    existing = _find_existing_raw_data(item)
    if existing:
        return existing, False

    return RawSsafyData.objects.create(
        source_type=item.get('source_type', 'notice'),
        source_url=item.get('source_url', ''),
        title=item.get('title', ''),
        raw_text=item.get('raw_text', ''),
        raw_html=item.get('raw_html', ''),
        status=RawSsafyData.STATUS_COLLECTED,
        metadata_json=item.get('metadata_json', {}),
        collected_at=timezone.now(),
    ), True


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
    job_log.finished_at = timezone.now()
    job_log.save()
    return job_log
