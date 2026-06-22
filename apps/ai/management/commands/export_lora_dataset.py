import json
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.ai.models import AiDocument, AiPipelineRun, ChatMessage, ChatSession


SYSTEM_PROMPT = (
    '너는 SSAFY 학생을 돕는 inSSa AI 비서다. '
    '확인된 근거를 바탕으로 한국어로 차분하고 구체적으로 답한다. '
    '근거가 부족하면 추측하지 않고 부족하다고 말한다.'
)


class Command(BaseCommand):
    help = 'Export inSSa LoRA fine-tuning samples as ChatML-style JSONL.'

    def add_arguments(self, parser):
        parser.add_argument('--output', default='ai_server/finetuning/data/train.jsonl')
        parser.add_argument('--limit', type=int, default=1000)
        parser.add_argument(
            '--include',
            default='notices,conversation,intent',
            help='Comma-separated sources: notices,conversation,intent',
        )

    def handle(self, *args, **options):
        output = Path(options['output'])
        limit = max(1, int(options['limit']))
        includes = {item.strip() for item in options['include'].split(',') if item.strip()}

        buckets = []
        if 'notices' in includes:
            buckets.append(self._notice_records(limit))
        if 'conversation' in includes:
            buckets.append(self._conversation_records(limit))
        if 'intent' in includes:
            buckets.append(self._intent_records(limit))

        records = self._interleave_records(buckets, limit)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('w', encoding='utf-8') as writer:
            for record in records:
                writer.write(json.dumps(record, ensure_ascii=False) + '\n')

        self.stdout.write(self.style.SUCCESS(f'Exported {len(records)} LoRA records to {output}'))

    def _notice_records(self, limit: int) -> list[dict]:
        records = []
        documents = (
            AiDocument.objects.exclude(content='')
            .order_by('-id')
            .only('id', 'title', 'content', 'document_type', 'metadata_json')[:limit]
        )
        for document in documents:
            context = self._trim(f'제목: {document.title}\n문서 유형: {document.document_type}\n본문:\n{document.content}', 3500)
            output = self._notice_summary_output(document)
            records.append(
                self._record(
                    user=(
                        '다음 SSAFY 관련 공지/문서를 학생이 바로 이해할 수 있게 요약해줘.\n'
                        '핵심 내용, 해야 할 일, 일정/기한, 주의사항을 확인된 내용만 바탕으로 정리해줘.\n\n'
                        f'[context]\n{context}'
                    ),
                    assistant=output,
                    task_type='notice_summary',
                    source='AiDocument',
                    source_id=document.id,
                )
            )
        return records

    def _conversation_records(self, limit: int) -> list[dict]:
        records = []
        sessions = ChatSession.objects.order_by('-id').prefetch_related('messages')[:limit]
        for session in sessions:
            messages = list(session.messages.order_by('created_at', 'id'))
            for index, message in enumerate(messages):
                if message.role != ChatMessage.ROLE_ASSISTANT or not message.content.strip():
                    continue
                previous = messages[max(0, index - 6):index]
                if not previous:
                    continue
                context_lines = []
                for item in previous:
                    speaker = '사용자' if item.role == ChatMessage.ROLE_USER else 'AI'
                    context_lines.append(f'{speaker}: {item.content}')
                records.append(
                    self._record(
                        user=(
                            '이전 대화와 현재 흐름을 바탕으로 사용자에게 자연스럽게 답해줘.\n'
                            '개인정보나 확인되지 않은 사실은 추측하지 마.\n\n'
                            f'[이전 대화]\n{self._trim(chr(10).join(context_lines), 2500)}'
                        ),
                        assistant=message.content,
                        task_type='conversation_context_answer',
                        source='ChatMessage',
                        source_id=message.id,
                    )
                )
                if len(records) >= limit:
                    return records
        return records

    def _intent_records(self, limit: int) -> list[dict]:
        records = []
        runs = (
            AiPipelineRun.objects.exclude(question='')
            .order_by('-id')
            .only('id', 'question', 'intent', 'query_type', 'answer_policy', 'usage_json', 'is_success')[:limit]
        )
        for run in runs:
            usage = run.usage_json or {}
            rule = usage.get('rule_parser') or {}
            gate = usage.get('confidence_gate') or {}
            payload = {
                'intent': run.intent or rule.get('intent') or 'unknown',
                'route': usage.get('verified_route') or rule.get('route') or usage.get('server_verified_route') or 'none',
                'data_sources': usage.get('verified_data_sources') or [],
                'query_type': run.query_type,
                'answer_policy': run.answer_policy,
                'confidence': rule.get('confidence', 0.0),
                'confidence_gate': gate.get('decision', ''),
            }
            records.append(
                self._record(
                    user=(
                        '다음 사용자 질문을 답변하지 말고 intent JSON으로만 분류해줘.\n'
                        'route는 db, rag, llm, hybrid, clarify, none 중 하나로 고르고 '
                        'data_sources는 필요한 검증 소스만 넣어줘.\n\n'
                        f'질문: {run.question}'
                    ),
                    assistant=json.dumps(payload, ensure_ascii=False),
                    task_type='intent_json',
                    source='AiPipelineRun',
                    source_id=run.id,
                )
            )
        return records

    def _notice_summary_output(self, document: AiDocument) -> str:
        title = document.title.strip() or '공지'
        metadata = document.metadata_json or {}
        date = metadata.get('published_at') or metadata.get('posted_at') or metadata.get('date') or ''
        lines = [
            f'확인해봤어요. "{title}" 공지는 아래처럼 정리할 수 있어요.',
            '',
            '- 핵심 내용: 공지 본문에서 확인되는 주요 안내를 먼저 확인하세요.',
            '- 해야 할 일: 신청, 제출, 참석, 확인이 필요한 항목이 있는지 본문 기준으로 확인하세요.',
        ]
        if date:
            lines.append(f'- 기준 일자: {date}')
        lines.append('- 주의사항: 본문에 없는 내용은 추측하지 말고 원문 또는 담당 공지를 다시 확인하세요.')
        return '\n'.join(lines)

    def _record(self, user: str, assistant: str, task_type: str, source: str, source_id: int) -> dict:
        return {
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': user.strip()},
                {'role': 'assistant', 'content': assistant.strip()},
            ],
            'metadata': {
                'task_type': task_type,
                'source': source,
                'source_id': source_id,
            },
        }

    def _interleave_records(self, buckets: list[list[dict]], limit: int) -> list[dict]:
        records = []
        index = 0
        while len(records) < limit:
            added = False
            for bucket in buckets:
                if index < len(bucket):
                    records.append(bucket[index])
                    added = True
                    if len(records) >= limit:
                        break
            if not added:
                break
            index += 1
        return records

    def _trim(self, text: str, limit: int) -> str:
        text = (text or '').strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + '\n...'
