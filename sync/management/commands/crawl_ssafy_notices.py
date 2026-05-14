from django.core.management.base import BaseCommand

from sync.services.import_service import run_sample_notice_import


class Command(BaseCommand):
    help = 'Load sample SSAFY notices and convert them into schedule events.'

    def handle(self, *args, **options):
        job_log = run_sample_notice_import()
        self.stdout.write(
            self.style.SUCCESS(
                f'{job_log.message} raw_count={job_log.raw_count}, '
                f'event_count={job_log.event_count}, failed_count={job_log.failed_count}'
            )
        )

