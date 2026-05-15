from django.core.management.base import BaseCommand, CommandError

from sync.models import RawSsafyData
from sync.services.schedule_parser import parse_schedule_candidates


class Command(BaseCommand):
    help = 'Preview schedule candidates parsed from one RawSsafyData row.'

    def add_arguments(self, parser):
        parser.add_argument('--id', type=int, required=True, help='RawSsafyData id to preview.')

    def handle(self, *args, **options):
        raw_data = RawSsafyData.objects.filter(pk=options['id']).first()
        if raw_data is None:
            raise CommandError(f'RawSsafyData id={options["id"]} was not found.')

        candidates = parse_schedule_candidates(raw_data.raw_text, default_title=raw_data.title)
        preview_text = raw_data.raw_text[:500].replace('\r', '')

        self.stdout.write(f'raw_id={raw_data.id}')
        self.stdout.write(f'raw_title={raw_data.title}')
        self.stdout.write(f'source_url={raw_data.source_url}')
        self.stdout.write('raw_text_preview=')
        self.stdout.write(preview_text)
        self.stdout.write(f'candidate_count={len(candidates)}')
        self.stdout.write('candidates=')
        for index, schedule in enumerate(candidates, start=1):
            self.stdout.write(
                f'{index}. title={schedule.title} '
                f'start_at={schedule.start_at.isoformat()} '
                f'end_at={schedule.end_at.isoformat()} '
                f'is_all_day={str(schedule.is_all_day).lower()} '
                f'event_type={schedule.event_type}'
            )
