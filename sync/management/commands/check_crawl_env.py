import os

from django.core.management.base import BaseCommand


REQUIRED_CRAWL_ENV_VARS = (
    'SSAFY_ID',
    'SSAFY_PASSWORD',
    'SSAFY_LOGIN_URL',
    'SSAFY_NOTICE_LIST_URL',
    'SSAFY_MENTORING_NOTICE_LIST_URL',
)


class Command(BaseCommand):
    help = 'Check required SSAFY crawl environment variables without printing secret values.'

    def handle(self, *args, **options):
        missing_keys = [key for key in REQUIRED_CRAWL_ENV_VARS if not os.getenv(key)]

        if missing_keys:
            self.stdout.write(self.style.WARNING('SSAFY crawl environment check failed.'))
            self.stdout.write('Missing required environment variables:')
            for key in missing_keys:
                self.stdout.write(f'- {key}')
            self.stdout.write('Set these keys in the deployment environment before running scheduled_ssafy_crawl.')
            return

        self.stdout.write(self.style.SUCCESS('SSAFY crawl environment check OK.'))
        self.stdout.write(f'checked_count={len(REQUIRED_CRAWL_ENV_VARS)}')
