from django.core.management.base import BaseCommand, CommandError

from schedules.models import ScheduleEvent
from sync.models import RawSsafyData
from sync.services.reparse_service import reparse_raw_data_to_events
from sync.services.schedule_parser import parse_schedule_candidates_with_debug
from sync.services.tracks import normalize_track_key, track_key_from_text


class Command(BaseCommand):
    help = 'Reparse OCR timetable RawSsafyData rows into idempotent ScheduleEvent rows.'

    def add_arguments(self, parser):
        parser.add_argument('--raw-id', type=int, help='Only reparse one RawSsafyData id.')
        parser.add_argument('--track', help='Only reparse timetable notices for one canonical track.')
        parser.add_argument('--source-title', help='Only reparse rows whose title contains this text.')
        parser.add_argument('--show-coverage', action='store_true', help='Print per-date timetable coverage.')
        parser.add_argument('--fail-on-empty-weekday', action='store_true', help='Exit with an error if a non-holiday weekday has no parsed events.')
        parser.add_argument('--dry-run', action='store_true', help='Print counts without writing changes.')
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Required to write changes. Without this option the command behaves as dry-run.',
        )

    def handle(self, *args, **options):
        queryset = RawSsafyData.objects.filter(source_type='notice')
        if options.get('raw_id'):
            queryset = queryset.filter(pk=options['raw_id'])
        if options.get('source_title'):
            queryset = queryset.filter(title__contains=options['source_title'])

        target_track = normalize_track_key(options.get('track'))
        if target_track:
            queryset = RawSsafyData.objects.filter(id__in=[
                raw_data.id
                for raw_data in queryset
                if _raw_data_track(raw_data) == target_track
            ])

        queryset = RawSsafyData.objects.filter(id__in=[
            raw_data.id
            for raw_data in queryset
            if _is_timetable_raw_data(raw_data)
        ]).order_by('id')

        target_count = queryset.count()
        if target_count == 0:
            raise CommandError('No OCR timetable RawSsafyData rows matched.')

        dry_run = options['dry_run'] or not options['confirm']
        existing_event_count = ScheduleEvent.objects.filter(raw_data__in=queryset, owner__isnull=True).count()
        preview = _preview_reparse_titles(queryset)

        summary = reparse_raw_data_to_events(
            queryset,
            dry_run=dry_run,
            replace_events=True,
        )

        self.stdout.write(
            self.style.SUCCESS(
                'Timetable reparse completed.\n'
                f'target_raw_count={target_count}\n'
                f'existing_generated_event_count={existing_event_count}\n'
                f'raw_checked={summary.raw_checked}\n'
                f'candidate_count={summary.candidate_count}\n'
                f'created_count={summary.created_count}\n'
                f'skipped_count={summary.skipped_count}\n'
                f'replaced_event_count={summary.replaced_event_count}\n'
                f'protected_event_count={summary.protected_event_count}\n'
                f'coverage_warning_count={summary.coverage_warning_count}\n'
                f'failed_count={summary.failed_count}\n'
                f'dry_run={str(dry_run).lower()}'
            )
        )
        for item in preview:
            self.stdout.write(f'raw_id={item["raw_id"]} source_title={item["source_title"]}')
            self.stdout.write(f'  existing_titles={item["existing_titles"]}')
            self.stdout.write(f'  parsed_titles={item["parsed_titles"]}')
            if options.get('show_coverage'):
                self.stdout.write(f'  track={item["track"]}')
                for coverage in item["coverage"]:
                    warning_suffix = f' warning={coverage["warning"]}' if coverage.get('warning') else ''
                    second_pass_suffix = ' second_pass=true' if coverage.get('second_pass_attempted') else ''
                    self.stdout.write(
                        f'  {coverage["date"]}: {coverage["event_count"]} events{second_pass_suffix}{warning_suffix}'
                    )
        if summary.coverage_warnings:
            self.stdout.write(self.style.WARNING('coverage warnings:'))
            for warning in summary.coverage_warnings:
                self.stdout.write(
                    f'- raw_id={warning["raw_data_id"]} date={warning["date"]} '
                    f'warning={warning["warning"]} source_title={warning["source_title"]}'
                )
        if dry_run:
            self.stdout.write(self.style.WARNING('Run again with --confirm to write changes.'))
        if options.get('fail_on_empty_weekday') and summary.coverage_warning_count:
            raise CommandError('Timetable coverage has non-holiday weekday gaps.')


def _is_timetable_raw_data(raw_data):
    metadata = raw_data.metadata_json or {}
    text = ' '.join([
        str(raw_data.title or ''),
        str(metadata.get('document_type') or ''),
        str(metadata.get('category') or ''),
    ]).lower()
    return '시간표' in text or '?쒓컙' in text or 'timetable' in text


def _raw_data_track(raw_data):
    metadata = raw_data.metadata_json or {}
    audience = metadata.get('audience') or {}
    return normalize_track_key(
        metadata.get('track_key')
        or audience.get('track_key')
        or metadata.get('track')
        or audience.get('track')
        or track_key_from_text(raw_data.title)
    )


def _preview_reparse_titles(queryset, limit=3):
    preview = []
    for raw_data in queryset[:limit]:
        existing_titles = list(
            ScheduleEvent.objects.filter(raw_data=raw_data, owner__isnull=True)
            .order_by('start_at', 'id')
            .values_list('title', flat=True)[:12]
        )
        parsed_schedules, grid_debug = parse_schedule_candidates_with_debug(
            raw_data.raw_text,
            default_title=raw_data.title,
            ocr_boxes=raw_data.ocr_boxes,
        )
        parsed_titles = [schedule.title for schedule in parsed_schedules[:12]]
        metadata = getattr(grid_debug, 'metadata_json', {}) or {}
        preview.append(
            {
                'raw_id': raw_data.id,
                'source_title': raw_data.title,
                'track': _raw_data_track(raw_data),
                'existing_titles': existing_titles,
                'parsed_titles': parsed_titles,
                'coverage': metadata.get('coverage') or [],
            }
        )
    return preview
