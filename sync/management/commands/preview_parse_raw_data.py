import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from sync.models import RawSsafyData
from sync.services.ocr_grid_parser import parse_grid_schedule_candidates
from sync.services.schedule_parser import parse_schedule_candidates


class Command(BaseCommand):
    help = 'Preview schedule candidates parsed from one RawSsafyData row.'

    def add_arguments(self, parser):
        parser.add_argument('--id', type=int, required=True, help='RawSsafyData id to preview.')
        parser.add_argument('--export-json', help='Write parser debug output to a JSON file.')

    def handle(self, *args, **options):
        raw_data = RawSsafyData.objects.filter(pk=options['id']).first()
        if raw_data is None:
            raise CommandError(f'RawSsafyData id={options["id"]} was not found.')

        candidates = parse_schedule_candidates(
            raw_data.raw_text,
            default_title=raw_data.title,
            ocr_boxes=raw_data.ocr_boxes,
        )
        text_candidates = parse_schedule_candidates(raw_data.raw_text, default_title=raw_data.title)
        _grid_candidates, grid_debug = parse_grid_schedule_candidates(raw_data.ocr_boxes)
        metadata = raw_data.metadata_json or {}
        image_urls = metadata.get('image_urls') or []
        preview_text = raw_data.raw_text[:500].replace('\r', '')

        self.stdout.write(f'raw_id={raw_data.id}')
        self.stdout.write(f'raw_title={raw_data.title}')
        self.stdout.write(f'source_url={raw_data.source_url}')
        self.stdout.write(f'image_urls_count={len(image_urls)}')
        for index, image_url in enumerate(image_urls[:5], start=1):
            self.stdout.write(f'image_url_{index}={image_url}')
        if len(image_urls) > 5:
            self.stdout.write(f'image_urls_omitted_count={len(image_urls) - 5}')
        self.stdout.write(f'ocr_status={metadata.get("ocr_status", "")}')
        self.stdout.write(f'ocr_text_length={metadata.get("ocr_text_length", 0)}')
        self.stdout.write(f'ocr_box_count={len(raw_data.ocr_boxes or [])}')
        self.stdout.write(f'grid_parser_used={str(grid_debug.used_grid_parser).lower()}')
        self.stdout.write(f'grid_date_cell_count={grid_debug.date_cell_count}')
        self.stdout.write(f'grid_candidate_count={grid_debug.candidate_count}')
        self.stdout.write(f'text_candidate_count={len(text_candidates)}')
        self.stdout.write(f'grid_parser_reason={grid_debug.reason}')
        self.stdout.write('raw_text_preview=')
        self.stdout.write(preview_text)
        self.stdout.write(f'candidate_count={len(candidates)}')
        self.stdout.write('grid_candidates=')
        for index, candidate in enumerate(grid_debug.candidates or [], start=1):
            self.stdout.write(
                f'{index}. inferred_date={candidate["inferred_date"]} '
                f'title={candidate["title"]} '
                f'event_type={candidate["event_type"]} '
                f'source_box_count={candidate["source_box_count"]} '
                f'row_index={candidate["row_index"]} '
                f'col_index={candidate["col_index"]} '
                f'confidence={candidate["confidence"]} '
                f'reason={candidate["reason"]}'
            )
        self.stdout.write('grid_unmatched_texts=')
        for index, unmatched in enumerate((grid_debug.unmatched_texts or [])[:80], start=1):
            self.stdout.write(
                f'{index}. inferred_date={unmatched["inferred_date"]} '
                f'text={unmatched["text"]} '
                f'source_box_count={unmatched["source_box_count"]} '
                f'row_index={unmatched["row_index"]} '
                f'col_index={unmatched["col_index"]} '
                f'confidence={unmatched["confidence"]} '
                f'reason={unmatched["reason"]}'
            )
        self.stdout.write('candidates=')
        for index, schedule in enumerate(candidates, start=1):
            self.stdout.write(
                f'{index}. title={schedule.title} '
                f'start_at={schedule.start_at.isoformat()} '
                f'end_at={schedule.end_at.isoformat()} '
                f'is_all_day={str(schedule.is_all_day).lower()} '
                f'event_type={schedule.event_type}'
            )

        if options.get('export_json'):
            export_path = Path(options['export_json'])
            export_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                'raw_id': raw_data.id,
                'raw_title': raw_data.title,
                'source_url': raw_data.source_url,
                'ocr_status': metadata.get('ocr_status', ''),
                'ocr_text_length': metadata.get('ocr_text_length', 0),
                'ocr_box_count': len(raw_data.ocr_boxes or []),
                'candidate_count': len(candidates),
                'text_candidate_count': len(text_candidates),
                'grid_debug': grid_debug.as_dict(),
            }
            export_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            self.stdout.write(f'export_json={export_path}')
