import json
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.test import override_settings

from apps.ai.management.commands.run_ai_quality_eval import InProcessAIClient
from apps.ai.services import AIService


SCENARIOS = [
    {
        'name': 'monthly_exam_followups',
        'turns': [
            {'q': '\uc774\ubc88\ub2ec \ud3c9\uac00 \uc77c\uc815 \uc54c\ub824\uc918', 'expected_policy': 'SCHEDULE_DB_DIRECT'},
            {'q': '\uadf8\uc911 \uc81c\uc77c \uac00\uae4c\uc6b4\uac70', 'expected_policy': 'SCHEDULE_MEMORY_DIRECT'},
            {'q': '\uadf8 \ub2e4\uc74c\uac70', 'expected_policy': 'SCHEDULE_MEMORY_NO_MATCH'},
            {'q': '\uadf8\uac74 \uc5b8\uc81c \ub05d\ub098?', 'expected_policy': 'SCHEDULE_MEMORY_DIRECT'},
            {'q': '\uacf5\uc6a9\ub9cc \ub2e4\uc2dc \ubcf4\uc5ec\uc918', 'expected_policy': 'SCHEDULE_MEMORY_DIRECT'},
        ],
    },
]


class Command(BaseCommand):
    help = 'Run multi-turn AI memory evaluation and save JSONL results.'

    def add_arguments(self, parser):
        parser.add_argument('--username', type=str, default='ai-memory-eval-user')

    def handle(self, *args, **options):
        user = self._get_user(options['username'])
        service = AIService(ai_server_client=InProcessAIClient())
        rows = []
        with override_settings(AI_SERVER_ENABLED=True):
            for scenario in SCENARIOS:
                session_id = None
                for turn_index, turn in enumerate(scenario['turns'], start=1):
                    payload = service.answer(user=user, message=turn['q'], session_id=session_id)
                    session_id = payload.get('session_id') or session_id
                    row = self._row(scenario['name'], turn_index, turn, payload, session_id)
                    rows.append(row)
                    self.stdout.write(f"{row['result']} {scenario['name']} T{turn_index} {row['answer_policy']} {turn['q']}")
        run_path = self._write_jsonl(rows)
        success_count = sum(1 for row in rows if row['success'])
        self.stdout.write(self.style.SUCCESS(f'Memory run saved: {run_path}'))
        self.stdout.write(self.style.SUCCESS(f'Success rate: {success_count}/{len(rows)} ({success_count / len(rows) * 100:.1f}%)'))

    def _get_user(self, username):
        User = get_user_model()
        user, _created = User.objects.get_or_create(
            username=username,
            defaults={'email': f'{username}@example.com'},
        )
        return user

    def _row(self, scenario, turn_index, turn, payload, session_id):
        usage = payload.get('usage') or {}
        answer_policy = payload.get('answer_policy', '')
        expected_policy = turn.get('expected_policy', '')
        success = answer_policy == expected_policy
        answer = payload.get('answer') or ''
        return {
            'scenario': scenario,
            'turn': turn_index,
            'question': turn['q'],
            'expected_policy': expected_policy,
            'answer_policy': answer_policy,
            'success': success,
            'result': 'PASS' if success else 'FAIL',
            'session_id': session_id,
            'route_stage': usage.get('route_stage', 'unknown'),
            'followup_type': usage.get('followup_type', ''),
            'selected_schedule_index': usage.get('selected_schedule_index', 0),
            'retrieved_count': usage.get('retrieved_count'),
            'answer': answer,
            'answer_preview': answer.replace('\n', ' ')[:180],
        }

    def _write_jsonl(self, rows):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        directory = self._docs_dir() / 'qa_runs'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'MEMORY_RUN_{timestamp}.jsonl'
        with path.open('w', encoding='utf-8') as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
        return path

    def _project_root(self):
        return settings.BASE_DIR.parent

    def _docs_dir(self):
        return self._project_root() / 'docs' / 'ai_chatbot_refactor'
