import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ai_server.core.config import get_settings
from apps.ai.models import AiDocument
from apps.ai.rag_ingestion import RagIngestionService
from apps.notices.services import NoticeImportService
from apps.notices.models import RawSsafyData, SsafyDataImportLog
from apps.users.models import User


class Command(BaseCommand):
    help = 'Ingest SSAFY RAG documents from a JSON file or a directory of JSON files.'

    def add_arguments(self, parser):
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument('--path', type=str, help='Path to one JSON file.')
        source.add_argument('--dir', type=str, help='Directory containing JSON files.')
        parser.add_argument('--user-id', type=int, default=None, help='User id to own the import log.')
        parser.add_argument('--type', type=str, default='JSON_UPLOAD', help='Import type stored in SsafyDataImportLog.')
        parser.add_argument('--no-ingest', action='store_true', help='Create DB rows only, without vector ingestion.')
        parser.add_argument(
            '--replace',
            action='store_true',
            help='Delete existing rows for this import type and rebuild the local vectorstore before ingesting.',
        )

    def handle(self, *args, **options):
        user = self._resolve_user(options['user_id'])
        paths = self._resolve_paths(options.get('path'), options.get('dir'))
        total_items = 0
        import_types = []

        for path in paths:
            payload = self._load_json(path)
            import_type, items = self._normalize_payload(payload, options['type'])
            import_types.append(import_type)
            if not items:
                self.stdout.write(self.style.WARNING(f'Skipped empty file: {path}'))
                continue
            if options['replace']:
                self._delete_existing_import_type(import_type)
            NoticeImportService().create_import_log(
                user=user,
                import_type=import_type,
                items=items,
                ingest=not options['no_ingest'],
            )
            total_items += len(items)
            self.stdout.write(self.style.SUCCESS(f'Ingested {len(items)} item(s) from {path}'))

        if options['replace'] and not options['no_ingest']:
            self._rebuild_vectorstore()

        self.stdout.write(self.style.SUCCESS(f'RAG JSON ingestion completed. files={len(paths)}, items={total_items}'))

    def _resolve_user(self, user_id):
        if user_id:
            user = User.objects.filter(id=user_id).first()
        else:
            user = User.objects.first()
        if not user:
            raise CommandError('No user exists. Create a user or pass --user-id.')
        return user

    def _resolve_paths(self, path_value, dir_value):
        if path_value:
            path = self._project_path(path_value)
            if not path.exists() or not path.is_file():
                raise CommandError(f'JSON file not found: {path}')
            return [path]

        directory = self._project_path(dir_value)
        if not directory.exists() or not directory.is_dir():
            raise CommandError(f'JSON directory not found: {directory}')
        paths = sorted(directory.glob('*.json'))
        if not paths:
            raise CommandError(f'No .json files found in: {directory}')
        return paths

    def _project_path(self, value):
        path = Path(value)
        if path.is_absolute():
            return path
        return Path(settings.BASE_DIR) / path

    def _load_json(self, path):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            raise CommandError(f'Invalid JSON in {path}: {exc}') from exc

    def _normalize_payload(self, payload, default_import_type):
        if isinstance(payload, list):
            return default_import_type, payload
        if isinstance(payload, dict):
            items = payload.get('items')
            if isinstance(items, list):
                return payload.get('type') or default_import_type, items
            if {'title', 'content'} & set(payload.keys()):
                return default_import_type, [payload]
        raise CommandError('JSON must be a list, an object with items, or one document object.')

    def _delete_existing_import_type(self, import_type):
        raw_ids = list(
            RawSsafyData.objects.filter(import_log__import_type=import_type).values_list('id', flat=True)
        )
        deleted_documents, _ = AiDocument.objects.filter(raw_data_id__in=raw_ids).delete()
        deleted_logs, _ = SsafyDataImportLog.objects.filter(import_type=import_type).delete()
        self.stdout.write(
            self.style.WARNING(
                f'Deleted existing import_type={import_type}: '
                f'ai_documents={deleted_documents}, import_logs={deleted_logs}'
            )
        )

    def _rebuild_vectorstore(self):
        settings_obj = get_settings()
        if settings_obj.vectorstore_provider != 'faiss':
            self.stdout.write(self.style.WARNING('Vectorstore rebuild only clears local faiss JSON files.'))
            return

        index_path = Path(settings_obj.vectorstore_path)
        if not index_path.is_absolute():
            index_path = Path(settings.BASE_DIR) / index_path
        if index_path.exists():
            index_path.unlink()
            self.stdout.write(self.style.WARNING(f'Deleted vectorstore file: {index_path}'))

        stats = RagIngestionService().ingest_all()
        self.stdout.write(self.style.SUCCESS(f'Rebuilt vectorstore: {stats}'))
