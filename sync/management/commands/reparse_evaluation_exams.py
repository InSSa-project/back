from dataclasses import dataclass, field

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from schedules.models import ScheduleEvent
from schedules.services import build_event_metadata_from_raw_data
from sync.models import RawSsafyData
from sync.services.schedule_parser import parse_schedule_candidates_with_debug


@dataclass
class EvaluationReparseSummary:
    evaluation_raw_count: int = 0
    delete_plan_count: int = 0
    raw_checked: int = 0
    deleted_count: int = 0
    created_count: int = 0
    review_required_count: int = 0
    abort_reason: str = ''
    march_exams: list = field(default_factory=list)


class Command(BaseCommand):
    help = 'Safely reparse exam ScheduleEvent rows from SSAFY evaluation OCR notices.'

    def add_arguments(self, parser):
        parser.add_argument('--id', type=int, help='Only reparse one RawSsafyData id.')
        parser.add_argument('--dry-run', action='store_true', help='Print changes without writing them.')
        parser.add_argument('--limit', type=int, help='Maximum number of RawSsafyData rows to inspect.')

    def handle(self, *args, **options):
        queryset = _evaluation_queryset()
        if options.get('id'):
            queryset = queryset.filter(pk=options['id'])
        if options.get('limit') is not None:
            queryset = queryset[: options['limit']]

        summary = _reparse(queryset, dry_run=options['dry_run'])
        self.stdout.write(
            self.style.SUCCESS(
                'Evaluation exam reparse completed.\n'
                f'evaluation_raw_count={summary.evaluation_raw_count}\n'
                f'delete_plan_count={summary.delete_plan_count}\n'
                f'raw_checked={summary.raw_checked}\n'
                f'deleted_count={summary.deleted_count}\n'
                f'created_count={summary.created_count}\n'
                f'review_required_count={summary.review_required_count}\n'
                f'abort_reason={summary.abort_reason or "none"}\n'
                f'march_exams={"; ".join(summary.march_exams) or "none"}\n'
                f'dry_run={str(options["dry_run"]).lower()}'
            )
        )


def _evaluation_queryset():
    return RawSsafyData.objects.filter(source_type='notice').filter(
        Q(title__contains='평가 안내')
        | Q(title__contains='과목평가')
        | Q(title__contains='월말평가')
        | Q(raw_text__contains='평가 안내')
        | Q(raw_text__contains='과목평가')
        | Q(raw_text__contains='월말평가')
    ).order_by('id')


@transaction.atomic
def _reparse(queryset, dry_run=False):
    summary = EvaluationReparseSummary()
    prepared = []
    for raw_data in queryset:
        parsed_schedules, grid_debug = parse_schedule_candidates_with_debug(
            raw_data.raw_text,
            default_title=raw_data.title,
            ocr_boxes=raw_data.ocr_boxes,
        )
        is_evaluation_raw = getattr(grid_debug, 'metadata_json', {}).get('parser') == 'evaluation_notice_ocr'
        if is_evaluation_raw:
            summary.evaluation_raw_count += 1
            prepared.append((raw_data, parsed_schedules, grid_debug))
        elif grid_debug.review_required_candidate_count:
            prepared.append((raw_data, [], grid_debug))

    if summary.evaluation_raw_count == 0:
        summary.abort_reason = 'no_evaluation_ocr_raw_data'
        summary.review_required_count = sum(
            1 for _raw_data, _parsed_schedules, grid_debug in prepared
            if grid_debug.review_required_candidate_count
        )
        return summary

    raw_ids = [raw_data.id for raw_data, _parsed_schedules, _grid_debug in prepared]
    summary.delete_plan_count = ScheduleEvent.objects.filter(
        raw_data_id__in=raw_ids,
        event_type='exam',
    ).count()

    for raw_data, parsed_schedules, grid_debug in prepared:
        summary.raw_checked += 1
        _store_review_metadata(raw_data, grid_debug)
        if grid_debug.review_required_candidate_count:
            summary.review_required_count += 1

        delete_qs = ScheduleEvent.objects.filter(raw_data=raw_data, event_type='exam')
        delete_count = delete_qs.count()
        summary.deleted_count += delete_count
        if not dry_run and delete_count:
            delete_qs.delete()

        for schedule in parsed_schedules:
            if schedule.event_type != 'exam':
                continue
            summary.created_count += 1
            if not dry_run:
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
            raw_data.save(update_fields=['metadata_json'])

    march_qs = ScheduleEvent.objects.filter(event_type='exam', start_at__month=3)
    if raw_ids:
        march_qs = march_qs.filter(raw_data_id__in=raw_ids)
    summary.march_exams = [
        f'{event.start_at.date().isoformat()} {event.title}'
        for event in march_qs.order_by('start_at', 'title')
    ]
    if dry_run:
        transaction.set_rollback(True)
    return summary


def _store_review_metadata(raw_data, grid_debug):
    metadata = dict(raw_data.metadata_json or {})
    metadata.update(getattr(grid_debug, 'metadata_json', {}) or {})
    review_required_candidates = grid_debug.review_required_candidates or []
    metadata['review_required_candidate_count'] = len(review_required_candidates)
    metadata['review_required_candidates'] = review_required_candidates
    raw_data.metadata_json = metadata
