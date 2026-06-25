from django.core.management.base import BaseCommand

from schedules.models import ScheduleEvent


AUTO_DESCRIPTION_PREFIXES = (
    'SSAFY OCR 시간표 셀에서 추출한 일정',
    'SSAFY OCR ?쒓컙',
)
AUTO_DESCRIPTION_MARKERS = (
    '\n원본 공지:',
    '\n?먮낯 怨듭?:',
)


class Command(BaseCommand):
    help = 'Clear only automatic OCR timetable descriptions while preserving source metadata.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Print counts without updating rows.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        targets = [
            event
            for event in ScheduleEvent.objects.filter(description__gt='').order_by('id')
            if _is_auto_ocr_timetable_description(event)
        ]

        changed_count = 0
        for event in targets:
            if dry_run:
                continue
            event.description = ''
            event.save(update_fields=['description', 'updated_at'])
            changed_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                'OCR timetable description cleanup completed.\n'
                f'target_count={len(targets)}\n'
                f'changed_count={changed_count}\n'
                f'dry_run={str(dry_run).lower()}'
            )
        )


def _is_auto_ocr_timetable_description(event):
    description = str(event.description or '').strip()
    if not description:
        return False

    metadata = event.metadata_json or {}
    parser = metadata.get('parser') or metadata.get('parser_type')
    looks_like_timetable = parser in {'ocr_timetable_grid', 'timetable_grid'} or bool(event.raw_data_id)
    if not looks_like_timetable:
        return False

    return description.startswith(AUTO_DESCRIPTION_PREFIXES) or any(
        marker in description for marker in AUTO_DESCRIPTION_MARKERS
    )
