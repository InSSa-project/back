import re

from django.core.management.base import BaseCommand
from django.utils import timezone

from sync.management.commands.crawl_ssafy_notices import (
    _format_count_dict,
    _source_run_logs_from_message,
    _temporary_crawler_env,
)
from sync.models import CrawlJobLog
from sync.services.import_service import preview_notice_import, run_notice_import


class Command(BaseCommand):
    help = 'Run the operational SSAFY notice crawl once for hourly schedulers.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--mode',
            choices=['sample', 'ssafy_notice'],
            default='ssafy_notice',
            help='Crawler mode. Defaults to ssafy_notice for scheduled operations.',
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
            help='Collect all configured sources. This is the default when no source filter is supplied.',
        )
        parser.add_argument('--skip-source', help='Comma-separated source list to skip, e.g. mentoring_notice.')
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Collect and classify candidates without writing RawSsafyData, ScheduleEvent, or CrawlJobLog rows.',
        )
        parser.add_argument('--max-pages', type=int, help='Limit pages scanned per source for this run.')
        parser.add_argument('--detail-timeout', type=float, help='Per Playwright action timeout in seconds.')
        parser.add_argument('--source-timeout', type=float, help='Per source timeout in seconds.')
        parser.add_argument(
            '--timeout',
            dest='source_timeout',
            type=float,
            help='Alias for --source-timeout. Limits each source independently.',
        )

    def handle(self, *args, **options):
        started_at = timezone.now()
        self.stdout.write(f'scheduled_ssafy_crawl started_at={started_at.isoformat()}')
        options = _scheduled_options(options)

        try:
            with _temporary_crawler_env(options):
                if options.get('dry_run'):
                    preview = preview_notice_import(mode=options.get('mode'))
                    return _print_scheduled_dry_run(self, preview, started_at)
                job_log = run_notice_import(mode=options.get('mode'))
        except Exception as exc:
            job_log = CrawlJobLog.objects.create(
                status=CrawlJobLog.STATUS_FAILED,
                crawler_mode=options.get('mode') or '',
                message=f'scheduled_ssafy_crawl failed: {exc}',
                failed_count=1,
                finished_at=timezone.now(),
            )

        return _print_scheduled_job(self, job_log, started_at)


def _scheduled_options(options):
    prepared = dict(options)
    if not prepared.get('all') and not prepared.get('source') and not prepared.get('source_type'):
        prepared['all'] = True
    return prepared


def _print_scheduled_dry_run(command, preview, started_at):
    summary = preview['summary']
    finished_at = timezone.now()
    duration = (finished_at - started_at).total_seconds()
    no_changes = summary.raw_count == 0 and summary.updated_count == 0 and summary.event_count == 0
    style = command.style.WARNING if summary.failed_count else command.style.SUCCESS
    command.stdout.write(
        style(
            'scheduled_ssafy_crawl dry_run=true, '
            f'status=success, mode={preview["mode"]}, '
            f'finished_at={finished_at.isoformat()}, duration_seconds={duration:.3f}, '
            f'fetched_by_source={_format_count_dict(summary.collected_source_counts)}, '
            f'created_count={summary.raw_count}, updated_count={summary.updated_count}, '
            f'skipped_count={summary.duplicate_count}, failed_count={summary.failed_count}, '
            f'ocr_processed_count={summary.ocr_processed_count}, '
            f'schedule_event_created_count={summary.event_count}, '
            f'schedule_event_updated_count=0, schedule_event_skipped_count={summary.event_skipped_count}, '
            f'no_changes={str(no_changes).lower()}'
        )
    )
    _print_source_run_logs(command, preview.get('message', ''))


def _print_scheduled_job(command, job_log, started_at):
    finished_at = job_log.finished_at or timezone.now()
    duration = (finished_at - started_at).total_seconds()
    updated_count = _message_int(job_log.message, 'updated_count')
    event_skipped_count = _message_int(job_log.message, 'event_skipped_count')
    duplicate_event_count = _event_skip_reason_count(job_log.message, 'duplicate')
    no_changes = (
        job_log.raw_count == 0
        and updated_count == 0
        and job_log.event_count == 0
        and job_log.failed_count == 0
    )
    style = command.style.SUCCESS if job_log.status in {'success', 'partial_success'} else command.style.ERROR
    command.stdout.write(
        style(
            'scheduled_ssafy_crawl '
            f'status={job_log.status}, mode={job_log.crawler_mode}, '
            f'started_at={started_at.isoformat()}, finished_at={finished_at.isoformat()}, '
            f'duration_seconds={duration:.3f}, fetched_by_source={_message_value(job_log.message, "collected_source_counts") or "none"}, '
            f'created_count={job_log.raw_count}, updated_count={updated_count}, '
            f'skipped_count={job_log.skipped_count}, failed_count={job_log.failed_count}, '
            f'ocr_processed_count={job_log.ocr_processed_count}, '
            f'schedule_event_created_count={job_log.event_count}, '
            f'schedule_event_updated_count={duplicate_event_count}, '
            f'schedule_event_skipped_count={event_skipped_count}, '
            f'no_changes={str(no_changes).lower()}, error_message={_error_message(job_log)}'
        )
    )
    command.stdout.write(f'job_log_id={job_log.id}')
    _print_source_run_logs(command, job_log.message)


def _print_source_run_logs(command, message):
    source_run_logs = _source_run_logs_from_message(message)
    if not source_run_logs:
        return
    command.stdout.write('Source run logs:')
    for source_run_log in source_run_logs:
        command.stdout.write(source_run_log)


def _message_int(message, key):
    value = _message_value(message, key)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _message_value(message, key):
    marker = f'{key}='
    if marker not in (message or ''):
        return ''
    return str(message).split(marker, 1)[1].split(', ', 1)[0].strip()


def _event_skip_reason_count(message, reason):
    match = re.search(rf'{reason}:(\d+)', _message_value(message, 'event_skip_reasons'))
    if not match:
        return 0
    return int(match.group(1))


def _error_message(job_log):
    if job_log.status in {'success', 'partial_success'}:
        return '-'
    return ' '.join(str(job_log.message or '').split())[:160] or '-'
