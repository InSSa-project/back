from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from sync.models import RawSsafyData
from sync.services.manual_ocr_service import apply_manual_ocr_text


class Command(BaseCommand):
    help = 'Set manual OCR text for a RawSsafyData row and optionally reparse schedules.'

    def add_arguments(self, parser):
        parser.add_argument('--id', type=int, required=True, help='RawSsafyData id to update.')
        parser.add_argument('--text', help='Manual OCR text to store.')
        parser.add_argument('--text-file', help='UTF-8 text file containing manual OCR text.')
        parser.add_argument('--reparse', action='store_true', help='Reparse the updated RawSsafyData into ScheduleEvent rows.')
        parser.add_argument('--dry-run', action='store_true', help='Validate input without writing changes.')

    def handle(self, *args, **options):
        raw_data = RawSsafyData.objects.filter(pk=options['id']).first()
        if raw_data is None:
            raise CommandError(f'RawSsafyData not found: id={options["id"]}')

        ocr_text = _load_ocr_text(options.get('text'), options.get('text_file'))
        try:
            result = apply_manual_ocr_text(
                raw_data,
                ocr_text,
                reparse=options['reparse'],
                dry_run=options['dry_run'],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                'Manual OCR text saved.\n'
                f'raw_data_id={result.raw_data_id}\n'
                f'ocr_provider={result.ocr_provider}\n'
                f'ocr_status={result.ocr_status}\n'
                f'ocr_text_length={result.ocr_text_length}\n'
                f'updated={str(result.updated).lower()}\n'
                f'reparse_created_count={result.reparse_created_count}\n'
                f'dry_run={str(result.dry_run).lower()}'
            )
        )


def _load_ocr_text(text, text_file):
    if text and text_file:
        raise CommandError('Use only one of --text or --text-file.')
    if text_file:
        return Path(text_file).read_text(encoding='utf-8')
    if text:
        return text
    raise CommandError('Either --text or --text-file is required.')
