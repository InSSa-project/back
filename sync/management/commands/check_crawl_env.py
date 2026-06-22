import os

from django.core.management.base import BaseCommand


REQUIRED_CRAWL_ENV_GROUPS = (
    ('SECRET_KEY',),
    ('DATABASE_URL',),
    ('SSAFY_ID',),
    ('SSAFY_PASSWORD',),
    ('SSAFY_LOGIN_URL',),
    ('SSAFY_NOTICE_LIST_URL',),
    ('SSAFY_MENTORING_NOTICE_LIST_URL', 'SSAFY_MENTORING_LIST_URL'),
)


class Command(BaseCommand):
    help = 'Check required SSAFY crawl environment variables without printing secret values.'

    def handle(self, *args, **options):
        missing_groups = [group for group in REQUIRED_CRAWL_ENV_GROUPS if not _any_env_value(group)]

        if missing_groups:
            self.stdout.write(self.style.WARNING('SSAFY crawl environment check failed.'))
            self.stdout.write('Missing required environment variables:')
            for group in missing_groups:
                self.stdout.write(f'- {_format_env_group(group)}')
            self.stdout.write('Set these keys in the deployment environment before running scheduled_ssafy_crawl.')
            return

        self.stdout.write(self.style.SUCCESS('SSAFY crawl environment check OK.'))
        self.stdout.write(f'checked_count={len(REQUIRED_CRAWL_ENV_GROUPS)}')


def _any_env_value(keys):
    return any(os.getenv(key) for key in keys)


def _format_env_group(keys):
    return ' or '.join(keys)
