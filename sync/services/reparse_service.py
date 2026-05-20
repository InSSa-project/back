from dataclasses import dataclass

from django.db import transaction

from schedules.models import ScheduleEvent
from schedules.services import build_event_metadata_from_raw_data
from sync.models import RawSsafyData
from sync.services.import_service import find_existing_schedule_event
from sync.services.schedule_parser import parse_schedule_candidates_with_debug


@dataclass
class ReparseSummary:
    raw_checked: int = 0
    candidate_count: int = 0
    created_count: int = 0
    skipped_count: int = 0
    no_schedule_count: int = 0
    failed_count: int = 0
    replaced_event_count: int = 0
    dry_run: bool = False


@transaction.atomic
def reparse_raw_data_to_events(raw_data_queryset, dry_run=False, limit=None, replace_events=False):
    summary = ReparseSummary(dry_run=dry_run)
    queryset = raw_data_queryset.order_by('id')
    if limit is not None:
        queryset = queryset[:limit]

    for raw_data in queryset:
        summary.raw_checked += 1
        if raw_data.source_type != 'notice':
            summary.no_schedule_count += 1
            continue

        try:
            parsed_schedules, grid_debug = parse_schedule_candidates_with_debug(
                raw_data.raw_text,
                default_title=raw_data.title,
                ocr_boxes=raw_data.ocr_boxes,
            )
            _store_review_required_candidates(raw_data, grid_debug)
        except Exception:
            summary.failed_count += 1
            continue

        summary.candidate_count += len(parsed_schedules)
        if not parsed_schedules:
            summary.no_schedule_count += 1
            if not dry_run:
                raw_data.save(update_fields=['metadata_json'])
            continue

        if replace_events:
            existing_events = ScheduleEvent.objects.filter(raw_data=raw_data)
            replace_count = existing_events.count()
            summary.replaced_event_count += replace_count
            if not dry_run and replace_count:
                existing_events.delete()

        for schedule in parsed_schedules:
            if not replace_events and find_existing_schedule_event(schedule, raw_data):
                summary.skipped_count += 1
                continue

            summary.created_count += 1
            if dry_run:
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
                metadata_json=build_event_metadata_from_raw_data(raw_data),
            )

        if not dry_run:
            raw_data.status = RawSsafyData.STATUS_PARSED
            raw_data.save(update_fields=['status', 'metadata_json'])

    if dry_run:
        transaction.set_rollback(True)
    return summary


def _store_review_required_candidates(raw_data, grid_debug):
    metadata = dict(raw_data.metadata_json or {})
    review_required_candidates = grid_debug.review_required_candidates or []
    metadata['review_required_candidate_count'] = len(review_required_candidates)
    metadata['review_required_candidates'] = review_required_candidates
    raw_data.metadata_json = metadata
