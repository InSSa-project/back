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

    def handle(self, *args, **options):
        job_log = run_notice_import(mode=options.get('mode'))
        style = self.style.SUCCESS if job_log.status == 'success' else self.style.ERROR
        self.stdout.write(
            style(
                f'{job_log.message} status={job_log.status}, raw_count={job_log.raw_count}, '
                f'event_count={job_log.event_count}, failed_count={job_log.failed_count}, '
                f'skipped_count={job_log.skipped_count}, notice_count={job_log.notice_count}, '
                f'academic_rule_count={job_log.academic_rule_count}, '
                f'no_schedule_count={job_log.no_schedule_count}, crawler_mode={job_log.crawler_mode}'
            )
        )

