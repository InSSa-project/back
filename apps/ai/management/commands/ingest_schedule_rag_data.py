from django.core.management.base import BaseCommand

from apps.ai.calendar_ingestion import ScheduleEventRagIngestionService
from schedules.models import ScheduleEvent


class Command(BaseCommand):
    help = 'Create and ingest AiDocument rows from manual/personal ScheduleEvent rows.'

    def add_arguments(self, parser):
        parser.add_argument('--id', type=int, help='Only ingest one ScheduleEvent row.')
        parser.add_argument('--limit', type=int, default=None, help='Maximum number of events to ingest.')
        parser.add_argument('--no-vectors', action='store_true', help='Create AiDocument rows without vector ingestion.')

    def handle(self, *args, **options):
        queryset = ScheduleEvent.objects.filter(raw_data__isnull=True).order_by('id')
        if options.get('id'):
            queryset = queryset.filter(id=options['id'])
        if options.get('limit'):
            queryset = queryset[: options['limit']]

        stats = ScheduleEventRagIngestionService().ingest_queryset(
            queryset,
            ingest_vectors=not options['no_vectors'],
        )
        self.stdout.write(self.style.SUCCESS(f'Schedule RAG ingestion completed: {stats}'))
