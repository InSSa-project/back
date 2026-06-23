from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from sync.models import RawSsafyData


DEFAULT_PATH = 'ai_server/finetuning/data/raw/imports/academic_rules_source.sqlite3'
DEFAULT_SOURCE_TABLE = 'sync_rawssafydata'


class Command(BaseCommand):
    help = 'Import externally exported crawler rows from a SQLite database into sync.RawSsafyData.'

    def add_arguments(self, parser):
        parser.add_argument('--path', default=DEFAULT_PATH, help='Source SQLite db path.')
        parser.add_argument('--source-table', default=DEFAULT_SOURCE_TABLE, help='Source table name.')
        parser.add_argument('--source-type', action='append', help='Only import matching source_type. Can be repeated.')
        parser.add_argument('--limit', type=int, help='Maximum source rows to inspect.')
        parser.add_argument('--apply', action='store_true', help='Write imported rows. Default is dry-run.')

    def handle(self, *args, **options):
        path = Path(options['path'])
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            raise CommandError(f'Source SQLite file not found: {path}')

        source_table = _safe_table_name(options['source_table'])
        source_types = set(options.get('source_type') or [])
        rows = _fetch_rows(path, source_table, source_types=source_types, limit=options.get('limit'))
        apply = bool(options['apply'])

        stats = {
            'source_rows': len(rows),
            'created': 0,
            'updated': 0,
            'skipped_existing': 0,
            'missing_identity': 0,
            'affected_ids': [],
        }

        with transaction.atomic():
            for row in rows:
                if not _row_identity(row):
                    stats['missing_identity'] += 1
                    continue

                existing = _find_existing(row)
                payload = _raw_data_payload(row)
                if existing is None:
                    stats['created'] += 1
                    if apply:
                        raw_data = RawSsafyData.objects.create(**payload)
                        stats['affected_ids'].append(raw_data.id)
                    continue

                if _should_update(existing, payload):
                    stats['updated'] += 1
                    if apply:
                        for key, value in payload.items():
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
                        stats['affected_ids'].append(existing.id)
                else:
                    stats['skipped_existing'] += 1

            if not apply:
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS('SQLite crawler import completed.'))
        self.stdout.write(f"mode={'apply' if apply else 'dry_run'}")
        self.stdout.write(f'source_db={path}')
        self.stdout.write(f'source_table={source_table}')
        self.stdout.write(f"source_types={','.join(sorted(source_types)) if source_types else 'all'}")
        self.stdout.write(f"source_rows={stats['source_rows']}")
        self.stdout.write(f"created_count={stats['created']}")
        self.stdout.write(f"updated_count={stats['updated']}")
        self.stdout.write(f"skipped_existing_count={stats['skipped_existing']}")
        self.stdout.write(f"missing_identity_count={stats['missing_identity']}")
        self.stdout.write(f"affected_raw_ids={','.join(map(str, stats['affected_ids'])) or 'none'}")


def _fetch_rows(path: Path, table_name: str, source_types: set[str], limit: int | None) -> list[dict]:
    where = ''
    params: list[object] = []
    if source_types:
        placeholders = ','.join('?' for _ in source_types)
        where = f'where source_type in ({placeholders})'
        params.extend(sorted(source_types))
    limit_sql = ''
    if limit:
        limit_sql = 'limit ?'
        params.append(limit)

    query = f'''
        select id, source_type, source_url, title, raw_text, raw_html,
               collected_at, status, metadata_json, ocr_boxes
        from {table_name}
        {where}
        order by collected_at asc, id asc
        {limit_sql}
    '''
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(query, params).fetchall()]
    finally:
        connection.close()


def _safe_table_name(value: str) -> str:
    if not value.replace('_', '').isalnum():
        raise CommandError('source table must contain only letters, numbers, and underscores.')
    return value


def _row_identity(row: dict) -> str:
    metadata = _json_value(row.get('metadata_json'), default={})
    return (
        str(row.get('source_url') or '').strip()
        or str(metadata.get('notice_id') or '').strip()
        or str(metadata.get('original_id') or '').strip()
        or str(row.get('id') or '').strip()
    )


def _find_existing(row: dict):
    source_type = row.get('source_type') or ''
    source_url = row.get('source_url') or ''
    metadata = _json_value(row.get('metadata_json'), default={})
    source_id = row.get('id')

    queryset = RawSsafyData.objects.filter(source_type=source_type)
    if source_url:
        existing = queryset.filter(source_url=source_url).first()
        if existing:
            return existing
    if source_id is not None:
        existing = queryset.filter(metadata_json__external_sqlite_raw_id=source_id).first()
        if existing:
            return existing
    notice_id = metadata.get('notice_id') or metadata.get('original_id')
    if notice_id:
        existing = queryset.filter(metadata_json__notice_id=notice_id).first()
        if existing:
            return existing
    title = row.get('title') or ''
    raw_text = row.get('raw_text') or ''
    if title and raw_text:
        return queryset.filter(title=title, raw_text=raw_text).first()
    return None


def _raw_data_payload(row: dict) -> dict:
    metadata = _json_value(row.get('metadata_json'), default={})
    metadata['external_source'] = 'sqlite_crawler_import'
    metadata['external_sqlite_raw_id'] = row.get('id')

    return {
        'source_type': row.get('source_type') or 'notice',
        'source_url': row.get('source_url') or '',
        'title': row.get('title') or 'Untitled SSAFY data',
        'raw_text': row.get('raw_text') or '',
        'raw_html': row.get('raw_html') or '',
        'ocr_boxes': _json_value(row.get('ocr_boxes'), default=[]),
        'collected_at': _datetime_value(row.get('collected_at')),
        'status': row.get('status') or RawSsafyData.STATUS_COLLECTED,
        'metadata_json': metadata,
    }


def _json_value(value, default):
    if value in (None, ''):
        return default.copy() if isinstance(default, dict) else list(default)
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default.copy() if isinstance(default, dict) else list(default)


def _datetime_value(value):
    if not value:
        return timezone.now()
    parsed = timezone.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _should_update(existing: RawSsafyData, payload: dict) -> bool:
    return any(getattr(existing, key) != value for key, value in payload.items())
