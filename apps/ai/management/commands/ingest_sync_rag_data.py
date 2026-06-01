from django.core.management.base import BaseCommand

from apps.ai.sync_ingestion import SyncRawDataRagIngestionService
from sync.models import RawSsafyData


class Command(BaseCommand):
    help = 'Create and ingest AiDocument rows from canonical sync.RawSsafyData rows.'

    def add_arguments(self, parser):
        parser.add_argument('--id', type=int, help='Only ingest one sync.RawSsafyData row.')
        parser.add_argument('--limit', type=int, default=None, help='Maximum number of raw rows to ingest.')
        parser.add_argument('--no-vectors', action='store_true', help='Create AiDocument rows without vector ingestion.')

    def handle(self, *args, **options):
        queryset = RawSsafyData.objects.all().order_by('id')
        if options.get('id'):
            queryset = queryset.filter(id=options['id'])
        if options.get('limit'):
            queryset = queryset[: options['limit']]

        stats = SyncRawDataRagIngestionService().ingest_queryset(
            queryset,
            ingest_vectors=not options['no_vectors'],
        )
        self.stdout.write(self.style.SUCCESS(f'Sync RAG ingestion completed: {stats}'))
