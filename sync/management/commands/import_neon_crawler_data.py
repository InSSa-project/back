from __future__ import annotations

from pathlib import Path

import psycopg
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from psycopg.rows import dict_row

from apps.ai.sync_ingestion import SyncRawDataRagIngestionService
from sync.models import RawSsafyData
from sync.services.reparse_service import reparse_raw_data_to_events


DEFAULT_SOURCE_TABLE = 'sync_rawssafydata'
LOCAL_URL_FILE = 'Neon PostgreSQL URL'


class Command(BaseCommand):
    help = 'Import externally crawled SSAFY raw data from Neon into the active Django database.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Write imported rows. Default is dry-run.')
        parser.add_argument('--limit', type=int, help='Maximum Neon rows to inspect.')
        parser.add_argument('--source-type', action='append', help='Only import matching source_type. Can be repeated.')
        parser.add_argument('--source-table', default=DEFAULT_SOURCE_TABLE, help='Neon source table name.')
        parser.add_argument('--database-url-file', default=LOCAL_URL_FILE, help='Local file containing Neon URL.')
        parser.add_argument('--reparse', action='store_true', help='Run schedule parser for imported/updated rows.')
        parser.add_argument('--ingest-rag', action='store_true', help='Create/update AiDocument and vector index for imported/updated rows.')
        parser.add_argument('--replace-events', action='store_true', help='When reparsing, replace events linked to imported rows.')

    def handle(self, *args, **options):
        neon_url = _neon_database_url(options['database_url_file'])
        source_table = _safe_table_name(options['source_table'])
        source_types = set(options.get('source_type') or [])
        apply = bool(options['apply'])

        rows = _fetch_neon_rows(neon_url, source_table, limit=options.get('limit'), source_types=source_types)
        stats = {
            'neon_rows': len(rows),
            'created': 0,
            'updated': 0,
            'skipped_existing': 0,
            'missing_identity': 0,
            'imported_ids': [],
        }

        for row in rows:
            identity = _row_identity(row)
            if not identity:
                stats['missing_identity'] += 1
                continue

            existing = _find_existing_raw_data(row)
            if existing is None:
                if apply:
                    raw_data = RawSsafyData.objects.create(**_raw_data_payload(row))
                    stats['imported_ids'].append(raw_data.id)
                stats['created'] += 1
                continue

            if _should_update(existing, row):
                if apply:
                    for key, value in _raw_data_payload(row).items():
                        setattr(existing, key, value)
                    existing.save(update_fields=[
                        'source_type',
                        'source_url',
                        'title',
                        'raw_text',
                        'raw_html',
                        'ocr_boxes',
                        'collected_at',
                        'status',
                        'metadata_json',
                    ])
                    stats['imported_ids'].append(existing.id)
                stats['updated'] += 1
            else:
                stats['skipped_existing'] += 1

        reparse_summary = None
        rag_stats = None
        if apply and stats['imported_ids']:
            queryset = RawSsafyData.objects.filter(id__in=stats['imported_ids']).order_by('id')
            if options['reparse']:
                reparse_summary = reparse_raw_data_to_events(
                    queryset,
                    dry_run=False,
                    replace_events=options['replace_events'],
                )
            if options['ingest_rag']:
                rag_stats = SyncRawDataRagIngestionService().ingest_queryset(queryset, ingest_vectors=True)

        self.stdout.write(self.style.SUCCESS('Neon crawler import completed.'))
        self.stdout.write(f"mode={'apply' if apply else 'dry_run'}")
        self.stdout.write(f"source_table={source_table}")
        self.stdout.write(f"neon_rows={stats['neon_rows']}")
        self.stdout.write(f"created_count={stats['created']}")
        self.stdout.write(f"updated_count={stats['updated']}")
        self.stdout.write(f"skipped_existing_count={stats['skipped_existing']}")
        self.stdout.write(f"missing_identity_count={stats['missing_identity']}")
        self.stdout.write(f"affected_raw_ids={','.join(map(str, stats['imported_ids'])) or 'none'}")
        if reparse_summary:
            self.stdout.write(
                'reparse='
                f'raw_checked:{reparse_summary.raw_checked}|'
                f'candidate:{reparse_summary.candidate_count}|'
                f'created:{reparse_summary.created_count}|'
                f'skipped:{reparse_summary.skipped_count}|'
                f'no_schedule:{reparse_summary.no_schedule_count}|'
                f'failed:{reparse_summary.failed_count}'
            )
        if rag_stats:
            self.stdout.write(
                'rag='
                f"raw_total:{rag_stats.get('raw_total', 0)}|"
                f"documents:{rag_stats.get('documents', 0)}|"
                f"vectors_success:{rag_stats.get('vectors_success', 0)}|"
                f"vectors_failed:{rag_stats.get('vectors_failed', 0)}|"
                f"chunks:{rag_stats.get('chunks', 0)}"
            )


def _neon_database_url(database_url_file: str) -> str:
    value = getattr(settings, 'NEON_CRAWLER_DATABASE_URL', '') or ''
    if value:
        return value.strip()

    file_path = Path(settings.BASE_DIR) / database_url_file
    if file_path.exists():
        return file_path.read_text(encoding='utf-8').strip()

    raise CommandError('Set NEON_CRAWLER_DATABASE_URL or provide --database-url-file.')


def _safe_table_name(value: str) -> str:
    if not value.replace('_', '').isalnum():
        raise CommandError('source table must contain only letters, numbers, and underscores.')
    return value


def _fetch_neon_rows(neon_url: str, table_name: str, limit: int | None, source_types: set[str]):
    where = []
    params = []
    if source_types:
        where.append('source_type = any(%s)')
        params.append(list(source_types))
    where_sql = f"where {' and '.join(where)}" if where else ''
    limit_sql = 'limit %s' if limit else ''
    if limit:
        params.append(limit)
    query = f'''
        select id, source_type, source_url, title, raw_text, raw_html,
               collected_at, status, metadata_json, ocr_boxes
        from {table_name}
        {where_sql}
        order by collected_at asc nulls last, id asc
        {limit_sql}
    '''
    with psycopg.connect(neon_url, connect_timeout=10, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return list(cur.fetchall())


def _row_identity(row) -> str:
    metadata = row.get('metadata_json') or {}
    return (
        str(row.get('source_url') or '').strip()
        or str(metadata.get('notice_id') or '').strip()
        or str(metadata.get('original_id') or '').strip()
        or str(row.get('id') or '').strip()
    )


def _find_existing_raw_data(row):
    source_type = row.get('source_type') or ''
    source_url = row.get('source_url') or ''
    metadata = row.get('metadata_json') or {}
    neon_id = row.get('id')

    queryset = RawSsafyData.objects.filter(source_type=source_type)
    if source_url:
        existing = queryset.filter(source_url=source_url).first()
        if existing:
            return existing
    if neon_id is not None:
        existing = queryset.filter(metadata_json__external_neon_raw_id=neon_id).first()
        if existing:
            return existing
    notice_id = metadata.get('notice_id') or metadata.get('original_id')
    if notice_id:
        return queryset.filter(metadata_json__notice_id=notice_id).first()
    return None


def _raw_data_payload(row):
    metadata = dict(row.get('metadata_json') or {})
    metadata['external_source'] = 'neon_crawler'
    metadata['external_neon_raw_id'] = row.get('id')
    if row.get('collected_at'):
        metadata['external_collected_at'] = row['collected_at'].isoformat()

    return {
        'source_type': row.get('source_type') or 'notice',
        'source_url': row.get('source_url') or '',
        'title': row.get('title') or 'Untitled SSAFY data',
        'raw_text': row.get('raw_text') or '',
        'raw_html': row.get('raw_html') or '',
        'ocr_boxes': row.get('ocr_boxes') or [],
        'collected_at': row.get('collected_at') or timezone.now(),
        'status': row.get('status') or RawSsafyData.STATUS_COLLECTED,
        'metadata_json': metadata,
    }


def _should_update(existing: RawSsafyData, row) -> bool:
    payload = _raw_data_payload(row)
    return any(getattr(existing, key) != value for key, value in payload.items())
