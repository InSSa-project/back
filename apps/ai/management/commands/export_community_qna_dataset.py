import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.community.models import CommunityComment, CommunityPost


DEFAULT_OUTPUT = Path('ai_server/finetuning/data/raw/community_qna/community_qna_raw.jsonl')
SYSTEM_PROMPT = (
    '너는 SSAFY 생활을 잘 아는 선배 AI이다. '
    '정확한 규정은 단정하지 않고, 확인된 정보와 조언을 분리해서 답한다.'
)


class Command(BaseCommand):
    help = 'Export community Q/A posts and answers as anonymized raw fine-tuning candidates.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default=str(DEFAULT_OUTPUT),
            help='Output JSONL path relative to BASE_DIR unless absolute.',
        )
        parser.add_argument(
            '--messages-output',
            default='',
            help='Optional curated messages JSONL path. One row is written per answer.',
        )
        parser.add_argument(
            '--min-answers',
            type=int,
            default=1,
            help='Minimum non-deleted top-level answers required to export a post.',
        )

    def handle(self, *args, **options):
        output_path = self._resolve_path(options['output'])
        messages_output = options.get('messages_output') or ''
        messages_path = self._resolve_path(messages_output) if messages_output else None
        min_answers = max(0, int(options['min_answers']))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if messages_path:
            messages_path.parent.mkdir(parents=True, exist_ok=True)

        posts = (
            CommunityPost.objects.filter(board_type=CommunityPost.BOARD_QNA)
            .select_related('author')
            .prefetch_related('comments')
            .order_by('created_at', 'id')
        )

        raw_count = 0
        messages_count = 0

        with output_path.open('w', encoding='utf-8') as raw_fp:
            messages_fp = messages_path.open('w', encoding='utf-8') if messages_path else None
            try:
                for post in posts:
                    answers = self._build_answers(post)
                    if len(answers) < min_answers:
                        continue

                    raw_record = {
                        'source': 'community_qna',
                        'post_id': post.id,
                        'question_author_id': post.author_id,
                        'question': {
                            'title': post.title,
                            'content': post.content,
                            'created_at': post.created_at.isoformat(),
                            'updated_at': post.updated_at.isoformat(),
                        },
                        'answers': answers,
                        'metadata': {
                            'board_type': post.board_type,
                            'requires_review': True,
                            'privacy': 'author names and emails excluded',
                        },
                    }
                    raw_fp.write(json.dumps(raw_record, ensure_ascii=False) + '\n')
                    raw_count += 1

                    if messages_fp:
                        for answer in answers:
                            messages_fp.write(
                                json.dumps(self._build_messages_record(post, answer), ensure_ascii=False) + '\n'
                            )
                            messages_count += 1
            finally:
                if messages_fp:
                    messages_fp.close()

        self.stdout.write(self.style.SUCCESS(f'Exported {raw_count} Q/A raw records to {output_path}'))
        if messages_path:
            self.stdout.write(self.style.SUCCESS(f'Exported {messages_count} message records to {messages_path}'))

    def _resolve_path(self, path_value):
        path = Path(path_value)
        if path.is_absolute():
            return path
        return Path(settings.BASE_DIR) / path

    def _build_answers(self, post):
        answers = []
        comments = (
            CommunityComment.objects.filter(post=post, is_deleted=False, parent__isnull=True)
            .order_by('created_at', 'id')
        )
        for comment in comments:
            answers.append(
                {
                    'comment_id': comment.id,
                    'answer_author_id': comment.author_id,
                    'content': comment.content,
                    'created_at': comment.created_at.isoformat(),
                    'updated_at': comment.updated_at.isoformat(),
                }
            )
        return answers

    def _build_messages_record(self, post, answer):
        question = f'{post.title}\n\n{post.content}'.strip()
        return {
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': question},
                {'role': 'assistant', 'content': answer['content']},
            ],
            'metadata': {
                'source': 'community_qna',
                'post_id': post.id,
                'comment_id': answer['comment_id'],
                'category': 'community_qna',
                'difficulty': 'level2',
                'complexity': 'qa',
                'reasoning_type': 'knowledge',
                'requires_rag': False,
                'tone': 'friendly',
                'risk_level': 'medium',
                'requires_review': True,
            },
        }
