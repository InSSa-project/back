import json
import re
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


EXPECTED_ROLES = ['system', 'user', 'assistant']
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
PHONE_RE = re.compile(r'(?<!\d)(?:010[-.\s]?\d{4}[-.\s]?\d{4}|\d{2,3}[-.\s]\d{3,4}[-.\s]\d{4})(?!\d)')
TOKEN_URL_RE = re.compile(
    r'https?://\S*[?&](?:token|access_token|refresh_token|api_key|apikey|secret|sessionid|session_id)=\S+',
    re.IGNORECASE,
)
SUSPICIOUS_MOJIBAKE_MARKERS = ('\ufffd', 'ì', 'ê', 'ë', 'í', 'ð', '硫', '嫄', '怨', '湲')


class Command(BaseCommand):
    help = 'Validate ChatML-style LoRA JSONL dataset quality.'

    def add_arguments(self, parser):
        parser.add_argument('--input', required=True, help='Input JSONL path.')
        parser.add_argument('--max-chars', type=int, default=6000, help='Warn when one record exceeds this many characters.')
        parser.add_argument('--fail-on-issues', action='store_true', help='Exit with an error when blocking issues are found.')

    def handle(self, *args, **options):
        input_path = Path(options['input'])
        if not input_path.exists():
            raise CommandError(f'Input file not found: {input_path}')

        stats = self._validate(input_path, max_chars=max(1, options['max_chars']))
        self._print_stats(stats)

        if options['fail_on_issues'] and stats['blocking_issue_count'] > 0:
            raise CommandError(f'LoRA dataset has {stats["blocking_issue_count"]} blocking issues.')

    def _validate(self, input_path: Path, max_chars: int) -> dict:
        task_types = Counter()
        duplicate_assistant = Counter()
        lengths = []
        issues = Counter()
        total = 0

        with input_path.open('r', encoding='utf-8') as reader:
            for line_number, line in enumerate(reader, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    issues['invalid_json'] += 1
                    continue

                total += 1
                metadata = record.get('metadata') or {}
                task_types[metadata.get('task_type') or 'unknown'] += 1

                messages = record.get('messages')
                if not isinstance(messages, list):
                    issues['missing_messages'] += 1
                    continue
                roles = [message.get('role') for message in messages if isinstance(message, dict)]
                if roles != EXPECTED_ROLES:
                    issues['role_error'] += 1

                contents = [str(message.get('content') or '') for message in messages if isinstance(message, dict)]
                if len(contents) != len(EXPECTED_ROLES) or any(not content.strip() for content in contents):
                    issues['empty_content'] += 1

                joined = '\n'.join(contents)
                lengths.append(len(joined))
                if len(joined) > max_chars:
                    issues['too_long'] += 1
                if EMAIL_RE.search(joined):
                    issues['email_like'] += 1
                if PHONE_RE.search(joined):
                    issues['phone_like'] += 1
                if TOKEN_URL_RE.search(joined):
                    issues['token_url_like'] += 1
                if any(marker in joined for marker in SUSPICIOUS_MOJIBAKE_MARKERS):
                    issues['mojibake_like'] += 1
                if len(contents) >= 3:
                    assistant_content = contents[2].strip()
                    if assistant_content:
                        duplicate_assistant[assistant_content[:200]] += 1

        repeated_answers = sum(count - 1 for count in duplicate_assistant.values() if count > 1)
        if repeated_answers:
            issues['repeated_assistant_prefix'] = repeated_answers

        blocking_keys = {'invalid_json', 'missing_messages', 'role_error', 'empty_content'}
        blocking_issue_count = sum(issues[key] for key in blocking_keys)
        return {
            'total': total,
            'task_types': task_types,
            'issues': issues,
            'blocking_issue_count': blocking_issue_count,
            'avg_chars': round(sum(lengths) / len(lengths), 1) if lengths else 0,
            'max_chars': max(lengths) if lengths else 0,
            'top_repeated_answers': duplicate_assistant.most_common(5),
        }

    def _print_stats(self, stats: dict) -> None:
        self.stdout.write('LoRA dataset validation completed.')
        self.stdout.write(f"total_count={stats['total']}")
        self.stdout.write(f"avg_chars={stats['avg_chars']}")
        self.stdout.write(f"max_chars={stats['max_chars']}")
        self.stdout.write('task_types=' + _format_counter(stats['task_types']))
        self.stdout.write('issues=' + _format_counter(stats['issues']))
        self.stdout.write(f"blocking_issue_count={stats['blocking_issue_count']}")
        repeated = [
            f'{count}x:{text[:80].replace(chr(10), " ")}'
            for text, count in stats['top_repeated_answers']
            if count > 1
        ]
        self.stdout.write('top_repeated_answers=' + (' | '.join(repeated) if repeated else 'none'))


def _format_counter(counter: Counter) -> str:
    if not counter:
        return 'none'
    return '|'.join(f'{key}:{counter[key]}' for key in sorted(counter))
