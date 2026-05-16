from django.core.management.base import BaseCommand

from apps.ai.rag_ingestion import RagIngestionService


class Command(BaseCommand):
    help = 'Chunk, embed, and upsert AiDocument rows into the RAG vector store.'

    def add_arguments(self, parser):
        parser.add_argument('--all', action='store_true', help='Re-index all documents, including successful ones.')
        parser.add_argument('--limit', type=int, default=None, help='Maximum number of documents to ingest.')

    def handle(self, *args, **options):
        service = RagIngestionService()
        stats = service.ingest_all(limit=options['limit']) if options['all'] else service.ingest_pending(limit=options['limit'])
        self.stdout.write(self.style.SUCCESS(f'RAG ingestion completed: {stats}'))
