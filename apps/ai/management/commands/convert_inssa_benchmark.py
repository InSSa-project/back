import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.ai.benchmarking import parse_inssa_benchmark, read_docx_text


class Command(BaseCommand):
    help = "Convert INSSA Benchmark DOCX into JSONL."

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            default="../INSSA Benchmark v1.0 (100 Questions).docx",
            help="Benchmark DOCX path.",
        )
        parser.add_argument(
            "--output",
            default="ai_server/finetuning/data/benchmarks/inssa_benchmark_v1.jsonl",
            help="Output JSONL path.",
        )

    def handle(self, *args, **options):
        input_path = Path(options["input"])
        output_path = Path(options["output"])
        if not input_path.exists():
            raise CommandError(f"Benchmark file not found: {input_path}")

        records = parse_inssa_benchmark(read_docx_text(input_path))
        if not records:
            raise CommandError("No benchmark questions parsed.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as writer:
            for record in records:
                writer.write(json.dumps(record, ensure_ascii=False) + "\n")

        self.stdout.write(self.style.SUCCESS(f"converted_count={len(records)}"))
        self.stdout.write(f"input={input_path}")
        self.stdout.write(f"output={output_path}")
        self.stdout.write(f"id_range={records[0]['id']}..{records[-1]['id']}")
