from dataclasses import dataclass

from django.db import transaction

from schedules.models import ScheduleEvent
from schedules.services import (
    build_generated_event_metadata,
    is_blocking_generated_schedule_warning,
    validate_generated_schedule,
)
from sync.models import RawSsafyData
from sync.services.import_service import find_existing_schedule_event, merge_schedule_candidate_into_event
from sync.services.schedule_parser import parse_schedule_candidates_with_debug
from sync.services.schedule_identity import ensure_raw_identity_metadata


@dataclass
class ReparseSummary:
    raw_checked: int = 0
    candidate_count: int = 0
    created_count: int = 0
    skipped_count: int = 0
    duplicate_skip_count: int = 0
    wrapper_skip_count: int = 0
    validation_skip_count: int = 0
    empty_title_skip_count: int = 0
    no_schedule_count: int = 0
    failed_count: int = 0
    replaced_event_count: int = 0
    protected_event_count: int = 0
    coverage_warning_count: int = 0
    coverage_warnings: list = None
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
        ensure_raw_identity_metadata(raw_data, save=not dry_run)

        try:
            parsed_schedules, grid_debug = parse_schedule_candidates_with_debug(
                raw_data.raw_text,
                default_title=raw_data.title,
                ocr_boxes=raw_data.ocr_boxes,
            )
            _store_review_required_candidates(raw_data, grid_debug)
            coverage_warnings = (getattr(grid_debug, 'metadata_json', {}) or {}).get('coverage_warnings') or []
            summary.coverage_warning_count += len(coverage_warnings)
            if coverage_warnings:
                if summary.coverage_warnings is None:
                    summary.coverage_warnings = []
                summary.coverage_warnings.extend(
                    {
                        'raw_data_id': raw_data.id,
                        'source_title': raw_data.title,
                        **warning,
                    }
                    for warning in coverage_warnings
                )
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
            existing_events = ScheduleEvent.objects.filter(raw_data=raw_data, owner__isnull=True)
            protected_events = existing_events.filter(user_schedule_events__isnull=False).distinct()
            protected_count = protected_events.count()
            summary.protected_event_count += protected_count
            if protected_count:
                _store_parser_warning(raw_data, ['user_override_linked_reparse_skipped'])
                if not dry_run:
                    raw_data.save(update_fields=['metadata_json'])
                summary.skipped_count += len(parsed_schedules)
                continue
            replace_count = existing_events.count()
            summary.replaced_event_count += replace_count
            if not dry_run and replace_count:
                existing_events.delete()

        for schedule in parsed_schedules:
            title = str(getattr(schedule, 'title', '') or '').strip()
            if not title:
                summary.skipped_count += 1
                summary.empty_title_skip_count += 1
                _store_parser_warning(raw_data, ['empty_schedule_title'])
                continue
            if _should_skip_fallback_week_timetable(schedule):
                summary.skipped_count += 1
                summary.validation_skip_count += 1
                _store_parser_warning(raw_data, ['fallback_week_timetable_blocked'])
                continue
            warnings = validate_generated_schedule(raw_data, schedule)
            if warnings:
                _store_parser_warning(raw_data, warnings)
            blocking_warnings = [warning for warning in warnings if is_blocking_generated_schedule_warning(warning)]
            if 'timetable_title_equals_source_title' in blocking_warnings:
                summary.skipped_count += 1
                summary.wrapper_skip_count += 1
                continue
            if blocking_warnings:
                summary.skipped_count += 1
                summary.validation_skip_count += 1
                continue
            existing_event = None if replace_events else find_existing_schedule_event(schedule, raw_data)
            if existing_event:
                if not dry_run:
                    merge_schedule_candidate_into_event(existing_event, schedule, raw_data)
                summary.skipped_count += 1
                summary.duplicate_skip_count += 1
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
                metadata_json=build_generated_event_metadata(raw_data, schedule),
            )

        if not dry_run:
            raw_data.status = RawSsafyData.STATUS_PARSED
            raw_data.save(update_fields=['status', 'metadata_json'])

    if dry_run:
        transaction.set_rollback(True)
    return summary


def _should_skip_fallback_week_timetable(schedule):
    metadata = getattr(schedule, 'metadata_json', None) or {}
    if metadata.get('allow_fallback_week'):
        return False
    parser_type = metadata.get('parser_type') or metadata.get('parser')
    return parser_type in {'timetable_grid', 'ocr_timetable_grid'} and metadata.get('date_mapping_source') == 'fallback_week'


def _store_review_required_candidates(raw_data, grid_debug):
    metadata = dict(raw_data.metadata_json or {})
    metadata.update(getattr(grid_debug, 'metadata_json', {}) or {})
    metadata['ocr_parse_debug'] = getattr(grid_debug, 'as_dict', lambda: {})()
    review_required_candidates = grid_debug.review_required_candidates or []
    metadata['review_required_candidate_count'] = len(review_required_candidates)
    metadata['review_required_candidates'] = review_required_candidates
    raw_data.metadata_json = metadata


def _store_parser_warning(raw_data, warnings):
    metadata = dict(raw_data.metadata_json or {})
    metadata['parser_warnings'] = list(dict.fromkeys([*(metadata.get('parser_warnings') or []), *warnings]))
    raw_data.metadata_json = metadata
