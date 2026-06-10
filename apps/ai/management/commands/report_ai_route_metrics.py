import json
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Report AI route metrics from var/logs/ai_route_metrics.jsonl.'

    def add_arguments(self, parser):
        parser.add_argument('--path', type=str, default='', help='Optional JSONL log path.')
        parser.add_argument('--limit', type=int, default=0, help='Read only the last N records after loading.')

    def handle(self, *args, **options):
        path = self._resolve_path(options.get('path'))
        if not path.exists():
            self.stdout.write(self.style.WARNING(f'No AI route metrics log found: {path}'))
            return

        records = self._read_records(path)
        if options.get('limit'):
            records = records[-options['limit']:]
        total = len(records)
        if not total:
            self.stdout.write(self.style.WARNING('AI route metrics log is empty.'))
            return

        stage_counts = Counter(record.get('route_stage') or 'unknown' for record in records)
        db_count = sum(1 for record in records if record.get('used_db'))
        rag_count = sum(1 for record in records if record.get('used_rag'))
        llm_count = sum(1 for record in records if record.get('used_llm'))
        llm_intent_count = sum(1 for record in records if record.get('llm_intent_used'))
        llm_conversion_count = sum(1 for record in records if record.get('llm_conversion'))
        token_values = [record.get('total_tokens') for record in records if isinstance(record.get('total_tokens'), int)]
        avg_tokens = round(sum(token_values) / len(token_values), 1) if token_values else 0

        self.stdout.write(self.style.SUCCESS(f'AI route metrics: {path}'))
        self.stdout.write(f'Total questions: {total}')
        self.stdout.write(f'DB handled: {db_count} ({self._pct(db_count, total)})')
        self.stdout.write(f'RAG used: {rag_count} ({self._pct(rag_count, total)})')
        self.stdout.write(f'LLM used: {llm_count} ({self._pct(llm_count, total)})')
        self.stdout.write(f'LLM intent fallback: {llm_intent_count} ({self._pct(llm_intent_count, total)})')
        self.stdout.write(f'LLM conversion: {llm_conversion_count} ({self._pct(llm_conversion_count, total)})')
        self.stdout.write(f'Average total tokens: {avg_tokens}')
        self.stdout.write('Route stages:')
        for stage, count in stage_counts.most_common():
            self.stdout.write(f'- {stage}: {count} ({self._pct(count, total)})')

    def _resolve_path(self, value: str | None) -> Path:
        if value:
            return Path(value)
        configured = getattr(settings, 'AI_ROUTE_LOG_PATH', '')
        if configured:
            return Path(configured)
        return Path(settings.BASE_DIR) / 'var' / 'logs' / 'ai_route_metrics.jsonl'

    def _read_records(self, path: Path) -> list[dict]:
        records = []
        with path.open('r', encoding='utf-8') as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    records.append(payload)
        return records

    def _pct(self, count: int, total: int) -> str:
        return f'{(count / total * 100):.1f}%'
