from django.db import transaction
from django.utils import timezone

from schedules.models import ScheduleEvent
from sync.models import CrawlJobLog, RawSsafyData
from sync.services.schedule_parser import parse_schedule_candidates
from sync.services.ssafy_crawler import load_sample_notices


SUCCESS_MESSAGE = '샘플 공지 데이터 수집 및 일정 변환이 완료되었습니다.'


@transaction.atomic
def run_sample_notice_import():
    job_log = CrawlJobLog.objects.create(status=CrawlJobLog.STATUS_RUNNING)

    try:
        raw_items = load_sample_notices()
        event_count = 0
        failed_count = 0

        for item in raw_items:
            raw_data, _ = RawSsafyData.objects.update_or_create(
                source_type=item['source_type'],
                source_url=item['source_url'],
                title=item['title'],
                defaults={
                    'raw_text': item['raw_text'],
                    'raw_html': item['raw_html'],
                    'status': RawSsafyData.STATUS_COLLECTED,
                    'metadata_json': item['metadata_json'],
                    'collected_at': timezone.now(),
                },
            )

            parsed_schedules = parse_schedule_candidates(raw_data.raw_text, default_title=raw_data.title)
            if not parsed_schedules:
                failed_count += 1
                raw_data.status = RawSsafyData.STATUS_FAILED
                raw_data.save(update_fields=['status'])
                continue

            raw_data.schedule_events.all().delete()
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

        job_log.status = CrawlJobLog.STATUS_SUCCESS
        job_log.message = SUCCESS_MESSAGE
        job_log.raw_count = len(raw_items)
        job_log.event_count = event_count
        job_log.failed_count = failed_count
        job_log.finished_at = timezone.now()
        job_log.save()
        return job_log
    except Exception as exc:
        job_log.status = CrawlJobLog.STATUS_FAILED
        job_log.message = str(exc)
        job_log.finished_at = timezone.now()
        job_log.save()
        raise
