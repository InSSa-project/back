from django.core.management.base import BaseCommand, CommandError

from apps.notices.services import NoticeImportService
from apps.users.models import User


class Command(BaseCommand):
    help = 'Create one sample SSAFY notice and ingest it into the RAG vector store.'

    def add_arguments(self, parser):
        parser.add_argument('--user-id', type=int, default=None)

    def handle(self, *args, **options):
        user = User.objects.filter(id=options['user_id']).first() if options['user_id'] else User.objects.first()
        if not user:
            raise CommandError('No user exists. Create a user before loading sample RAG data.')

        NoticeImportService().create_import_log(
            user=user,
            import_type='MANUAL_INPUT',
            items=[
                {
                    'source_type': 'EXAM',
                    'title': '5월 월말평가 일정 안내',
                    'content': (
                        '5월 월말평가는 2026년 5월 24일 금요일 09:00부터 12:00까지 진행됩니다.\n\n'
                        '응시 대상은 해당 월 교육 과정 참여 교육생이며, 세부 고사실과 준비물은 캠퍼스별 공지를 확인해야 합니다.\n\n'
                        '시험 일정은 운영 상황에 따라 변경될 수 있으므로 최종 공지를 반드시 확인하세요.'
                    ),
                }
            ],
            ingest=True,
        )
        self.stdout.write(self.style.SUCCESS('Sample SSAFY RAG data created and ingested.'))
