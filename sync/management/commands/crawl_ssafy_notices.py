import os
from contextlib import contextmanager

from django.core.management.base import BaseCommand

from sync.services.import_service import run_notice_import


class Command(BaseCommand):
    help = 'Collect SSAFY notices by crawler mode and convert them into schedule events.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--mode',
            choices=['sample', 'ssafy_notice'],
            help='Crawler mode. Defaults to SSAFY_CRAWLER_MODE, then sample.',
        )
        parser.add_argument('--source', help='Comma-separated source list, e.g. notice,academic_rule.')
        parser.add_argument('--skip-source', help='Comma-separated source list to skip, e.g. mentoring_notice.')
        parser.add_argument('--max-pages', type=int, help='Override SSAFY_NOTICE_MAX_PAGES for this run.')
        parser.add_argument('--detail-timeout', type=float, help='Per Playwright action timeout in seconds.')
        parser.add_argument('--source-timeout', type=float, help='Per source timeout in seconds.')

    def handle(self, *args, **options):
        with _temporary_crawler_env(options):
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


@contextmanager
def _temporary_crawler_env(options):
    mapping = {
        'SSAFY_CRAWLER_SOURCES': options.get('source'),
        'SSAFY_CRAWLER_SKIP_SOURCES': options.get('skip_source'),
        'SSAFY_NOTICE_MAX_PAGES': options.get('max_pages'),
        'SSAFY_DETAIL_TIMEOUT': options.get('detail_timeout'),
        'SSAFY_SOURCE_TIMEOUT': options.get('source_timeout'),
    }
    original = {key: os.environ.get(key) for key in mapping}
    try:
        for key, value in mapping.items():
            if value is not None:
                os.environ[key] = str(value)
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

