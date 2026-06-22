import os

from django.core.management.base import BaseCommand


REQUIRED_CRAWL_ENV_GROUPS = (
    ('SECRET_KEY',),
    ('SSAFY_ID',),
    ('SSAFY_PASSWORD',),
    ('SSAFY_LOGIN_URL',),
    ('SSAFY_NOTICE_LIST_URL',),
    ('SSAFY_RULE_LIST_URL',),
    ('SSAFY_MENTORING_DATA_LIST_URL', 'SSAFY_MENTORING_DATA_URL'),
    ('SSAFY_MENTORING_NOTICE_LIST_URL', 'SSAFY_MENTORING_LIST_URL'),
)
REQUIRED_DB_ENV_VARS = (
    'DB_ENGINE',
    'DB_NAME',
    'DB_USER',
    'DB_PASSWORD',
    'DB_HOST',
    'DB_PORT',
)
POSTGRES_ENGINES = {'postgres', 'postgresql'}


class Command(BaseCommand):
    help = 'Check required SSAFY crawl environment variables without printing secret values.'

    def handle(self, *args, **options):
        missing_groups = _missing_required_groups()
        missing_db_keys = _missing_db_env_keys()

        if missing_groups or missing_db_keys:
            self.stdout.write(self.style.WARNING('SSAFY crawl environment check failed.'))
            self.stdout.write('Missing required environment variables:')
            for group in missing_groups:
                self.stdout.write(f'- {_format_env_group(group)}')
            for key in missing_db_keys:
                self.stdout.write(f'- {key}')
            self.stdout.write('Set these keys in the deployment environment before running scheduled_ssafy_crawl.')
            return

        self.stdout.write(self.style.SUCCESS('SSAFY crawl environment check OK.'))
        self.stdout.write(f'checked_count={len(REQUIRED_CRAWL_ENV_GROUPS) + 1}')


def _missing_required_groups():
    return [group for group in REQUIRED_CRAWL_ENV_GROUPS if not _any_env_value(group)]


def _missing_db_env_keys():
    if os.getenv('DATABASE_URL'):
        return []

    db_engine = os.getenv('DB_ENGINE', '').strip().lower()
    if db_engine not in POSTGRES_ENGINES:
        return ['DATABASE_URL or DB_ENGINE=postgres with DB_*']

    return [key for key in REQUIRED_DB_ENV_VARS if not os.getenv(key)]


def _any_env_value(keys):
    return any(os.getenv(key) for key in keys)


def _format_env_group(keys):
    return ' or '.join(keys)
