from sync.management.commands.backfill_raw_ocr import Command as BackfillRawOcrCommand


class Command(BackfillRawOcrCommand):
    help = 'Reprocess OCR for existing RawSsafyData rows.'
