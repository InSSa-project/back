import os
from contextlib import contextmanager

from django.core.management.base import BaseCommand
from django.db.models import Count

from sync.models import RawSsafyData
from sync.services.import_service import preview_notice_import, run_notice_import


class Command(BaseCommand):
    help = 'Collect SSAFY notices by crawler mode and convert them into schedule events.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--mode',
            choices=['sample', 'ssafy_notice'],
            help='Crawler mode. Defaults to SSAFY_CRAWLER_MODE, then sample.',
        )
        parser.add_argument('--source', help='Comma-separated source list, e.g. notice,academic_rule.')
        parser.add_argument(
            '--source-type',
            action='append',
            help='Source type to collect. Can be repeated or comma-separated, e.g. --source-type notice.',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Collect all configured sources. This clears --source/--source-type filters.',
        )
        parser.add_argument('--skip-source', help='Comma-separated source list to skip, e.g. mentoring_notice.')
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Collect and classify candidates without writing RawSsafyData, ScheduleEvent, or CrawlJobLog rows.',
        )
        parser.add_argument('--max-pages', type=int, help='Limit pages scanned per source for this run.')
        parser.add_argument('--recent-limit', type=int, help='Limit recent unique items checked per source for this run.')
        parser.add_argument('--detail-timeout', type=float, help='Per Playwright action timeout in seconds.')
        parser.add_argument('--source-timeout', type=float, help='Per source timeout in seconds.')
        parser.add_argument(
            '--timeout',
            dest='source_timeout',
            type=float,
            help='Alias for --source-timeout. Limits each source independently.',
        )

    def handle(self, *args, **options):
        with _temporary_crawler_env(options):
            if options.get('dry_run'):
                preview = preview_notice_import(mode=options.get('mode'))
                return _print_dry_run(self, preview)
            job_log = run_notice_import(mode=options.get('mode'))
        style = self.style.SUCCESS if job_log.status in {'success', 'partial_success'} else self.style.ERROR
        self.stdout.write(
            style(
                f'{job_log.message} status={job_log.status}, raw_count={job_log.raw_count}, '
                f'event_count={job_log.event_count}, failed_count={job_log.failed_count}, '
                f'skipped_count={job_log.skipped_count}, notice_count={job_log.notice_count}, '
                f'academic_rule_count={job_log.academic_rule_count}, '
                f'no_schedule_count={job_log.no_schedule_count}, image_count={job_log.image_count}, '
                f'ocr_processed_count={job_log.ocr_processed_count}, '
                f'ocr_failed_count={job_log.ocr_failed_count}, crawler_mode={job_log.crawler_mode}'
            )
        )
        source_counts = RawSsafyData.objects.values('source_type').annotate(count=Count('id')).order_by('source_type')
        self.stdout.write('RawSsafyData source counts:')
        for row in source_counts:
            self.stdout.write(f'{row["source_type"]}: {row["count"]}')
        source_run_logs = _source_run_logs_from_message(job_log.message)
        if source_run_logs:
            self.stdout.write('Source run logs:')
            for source_run_log in source_run_logs:
                self.stdout.write(source_run_log)


@contextmanager
def _temporary_crawler_env(options):
    source_filter = _source_filter_option(options)
    mapping = {
        'SSAFY_CRAWLER_SOURCES': source_filter,
        'SSAFY_CRAWLER_SKIP_SOURCES': options.get('skip_source'),
        'SSAFY_NOTICE_MAX_PAGES': options.get('max_pages'),
        'SSAFY_CRAWLER_RECENT_LIMIT': options.get('recent_limit'),
        'SSAFY_DETAIL_TIMEOUT': options.get('detail_timeout'),
        'SSAFY_SOURCE_TIMEOUT': options.get('source_timeout'),
    }
    clear_keys = set(options.get('_clear_env_keys') or [])
    original = {key: os.environ.get(key) for key in mapping}
    try:
        for key, value in mapping.items():
            if key in clear_keys:
                os.environ.pop(key, None)
            elif value is not None:
                os.environ[key] = str(value)
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _source_run_logs_from_message(message):
    marker = 'source_run_logs='
    if marker not in (message or ''):
        return []
    source_run_logs = message.split(marker, 1)[1]
    return [item for item in source_run_logs.split(';') if item]


def _source_filter_option(options):
    if options.get('all'):
        return ''
    values = []
    if options.get('source'):
        values.append(options['source'])
    for source_type in options.get('source_type') or []:
        values.append(source_type)
    return ','.join(values) if values else None


def _print_dry_run(command, preview):
    summary = preview['summary']
    style = command.style.WARNING if summary.failed_count else command.style.SUCCESS
    command.stdout.write(
        style(
            'dry_run=true, '
            f'mode={preview["mode"]}, '
            f'collected={sum(summary.collected_source_counts.values())}, '
            f'would_create_raw={summary.raw_count}, '
            f'would_update_raw={summary.updated_count}, '
            f'would_skip_duplicate={summary.duplicate_count}, '
            f'would_exclude={summary.excluded_count}, '
            f'would_process_ocr_images={summary.ocr_processed_count}, '
            f'would_create_schedule_events={summary.event_count}, '
            f'failed_count={summary.failed_count}'
        )
    )
    command.stdout.write(f'collected_by_source={_format_count_dict(summary.collected_source_counts)}')
    command.stdout.write(f'would_create_by_source={_format_count_dict(summary.source_counts)}')
    command.stdout.write(f'would_update_by_source={_format_count_dict(summary.updated_by_source)}')
    command.stdout.write(f'would_skip_by_source={_format_count_dict(summary.skipped_by_source)}')
    command.stdout.write(f'no_schedule_by_source={_format_count_dict(summary.no_schedule_by_type)}')
    source_run_logs = _source_run_logs_from_message(preview.get('message', ''))
    if source_run_logs:
        command.stdout.write('Source run logs:')
        for source_run_log in source_run_logs:
            command.stdout.write(source_run_log)


def _format_count_dict(values):
    if not values:
        return 'none'
    return '|'.join(f'{key}:{values[key]}' for key in sorted(values))

