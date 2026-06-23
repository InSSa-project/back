import json
import random
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Split LoRA JSONL records into train/eval files while preserving task_type mix.'

    def add_arguments(self, parser):
        parser.add_argument('--input', required=True, help='Input JSONL path.')
        parser.add_argument('--train-output', required=True, help='Output train JSONL path.')
        parser.add_argument('--eval-output', required=True, help='Output eval JSONL path.')
        parser.add_argument('--eval-ratio', type=float, default=0.1, help='Evaluation ratio between 0 and 0.5.')
        parser.add_argument('--seed', type=int, default=42, help='Deterministic shuffle seed.')

    def handle(self, *args, **options):
        input_path = Path(options['input'])
        if not input_path.exists():
            raise CommandError(f'Input file not found: {input_path}')

        eval_ratio = float(options['eval_ratio'])
        if eval_ratio <= 0 or eval_ratio >= 0.5:
            raise CommandError('--eval-ratio must be greater than 0 and less than 0.5.')

        records = self._load_records(input_path)
        train_records, eval_records = self._split(records, eval_ratio=eval_ratio, seed=int(options['seed']))

        train_output = Path(options['train_output'])
        eval_output = Path(options['eval_output'])
        self._write_jsonl(train_output, train_records)
        self._write_jsonl(eval_output, eval_records)

        self.stdout.write(self.style.SUCCESS('LoRA dataset split completed.'))
        self.stdout.write(f'total_count={len(records)}')
        self.stdout.write(f'train_count={len(train_records)}')
        self.stdout.write(f'eval_count={len(eval_records)}')
        self.stdout.write(f'train_output={train_output}')
        self.stdout.write(f'eval_output={eval_output}')

    def _load_records(self, input_path: Path) -> list[dict]:
        records = []
        with input_path.open('r', encoding='utf-8') as reader:
            for line_number, line in enumerate(reader, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise CommandError(f'Invalid JSON on line {line_number}: {exc}') from exc
        if not records:
            raise CommandError('Input dataset is empty.')
        return records

    def _split(self, records: list[dict], eval_ratio: float, seed: int) -> tuple[list[dict], list[dict]]:
        grouped = defaultdict(list)
        for record in records:
            task_type = (record.get('metadata') or {}).get('task_type') or 'unknown'
            grouped[task_type].append(record)

        rng = random.Random(seed)
        train_records = []
        eval_records = []
        for task_type in sorted(grouped):
            bucket = grouped[task_type]
            rng.shuffle(bucket)
            eval_count = int(round(len(bucket) * eval_ratio))
            if len(bucket) > 1:
                eval_count = max(1, min(eval_count, len(bucket) - 1))
            else:
                eval_count = 0
            eval_records.extend(bucket[:eval_count])
            train_records.extend(bucket[eval_count:])

        rng.shuffle(train_records)
        rng.shuffle(eval_records)
        return train_records, eval_records

    def _write_jsonl(self, output_path: Path, records: list[dict]) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open('w', encoding='utf-8') as writer:
            for record in records:
                writer.write(json.dumps(record, ensure_ascii=False) + '\n')
