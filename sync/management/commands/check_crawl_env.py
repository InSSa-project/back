import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


REQUIRED_CRAWL_ENV_GROUPS = (
    ('SECRET_KEY',),
    ('SSAFY_ID',),
    ('SSAFY_PASSWORD',),
    ('SSAFY_LOGIN_URL',),
    ('SSAFY_NOTICE_LIST_URL',),
    ('SSAFY_RULE_LIST_URL',),
    ('SSAFY_MENTORING_DATA_LIST_URL', 'SSAFY_MENTORING_DATA_URL'),
    ('SSAFY_MENTORING_NOTICE_LIST_URL', 'SSAFY_MENTORING_LIST_URL'),
    ('SSAFY_MENTORING_QNA_LIST_URL',),
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
SUPPORTED_OCR_PROVIDERS = {'mock', 'google_vision', 'clova'}
GOOGLE_VISION_CREDENTIAL_REQUIRED_FIELDS = {'type', 'project_id', 'private_key', 'client_email'}
TRUE_VALUES = {'1', 'true', 'yes', 'on'}


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
            raise CommandError('Required crawl environment variables are missing.')

        ocr_errors = _validate_ocr_env(self.stdout)
        if ocr_errors:
            for error in ocr_errors:
                self.stdout.write(self.style.ERROR(error))
            raise CommandError('OCR environment validation failed.')

        self.stdout.write(self.style.SUCCESS('SSAFY crawl environment check OK.'))
        self.stdout.write(f'checked_count={len(REQUIRED_CRAWL_ENV_GROUPS) + 1}')


def _validate_ocr_env(stdout):
    provider = os.getenv('OCR_PROVIDER', '').strip().lower()
    google_vision_enabled = os.getenv('GOOGLE_VISION_ENABLED', '').strip().lower() in TRUE_VALUES

    stdout.write(f'ocr_runtime provider_present={bool(provider)}')
    stdout.write(f'ocr_runtime provider_normalized={provider or "-"}')

    if not provider:
        stdout.write('ocr_runtime provider_supported=true')
        if google_vision_enabled:
            stdout.write('ocr_runtime google_vision_enabled=true')
            return [
                'OCR_PROVIDER is missing. GOOGLE_VISION_ENABLED is set — '
                'set OCR_PROVIDER=google_vision to enable Vision OCR.'
            ]
        return []

    if provider not in SUPPORTED_OCR_PROVIDERS:
        stdout.write('ocr_runtime provider_supported=false')
        return [f'Unsupported OCR_PROVIDER: {provider!r}. Supported values: {sorted(SUPPORTED_OCR_PROVIDERS)}.']

    stdout.write('ocr_runtime provider_supported=true')

    if provider == 'google_vision':
        return _validate_google_vision_env(stdout)

    return []


def _validate_google_vision_env(stdout):
    enabled = os.getenv('GOOGLE_VISION_ENABLED', '').strip().lower()
    enabled_bool = enabled in TRUE_VALUES
    stdout.write(f'ocr_runtime google_vision_enabled={enabled_bool}')
    if not enabled_bool:
        return ['Google Vision OCR is not enabled. Set GOOGLE_VISION_ENABLED=true.']

    credentials_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS', '').strip()
    stdout.write(f'ocr_runtime credentials_path_present={bool(credentials_path)}')
    if not credentials_path:
        return ['Google credentials path does not exist. Set GOOGLE_APPLICATION_CREDENTIALS to the credentials file path.']

    target = Path(credentials_path)
    file_exists = target.is_file()
    stdout.write(f'ocr_runtime credentials_file_exists={file_exists}')
    if not file_exists:
        return [
            'Google credentials file does not exist. '
            'GOOGLE_APPLICATION_CREDENTIALS must point to a valid JSON credentials file.'
        ]

    readable = os.access(credentials_path, os.R_OK)
    stdout.write(f'ocr_runtime credentials_file_readable={readable}')
    if not readable:
        return ['Google credentials file is not readable.']

    try:
        data = json.loads(target.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return ['Google credentials file is invalid JSON.']

    missing_fields = GOOGLE_VISION_CREDENTIAL_REQUIRED_FIELDS - set(data.keys())
    stdout.write(f'ocr_runtime credentials_required_fields_present={not missing_fields}')
    if missing_fields:
        return [f'Google credentials file is missing required fields: {sorted(missing_fields)}.']

    try:
        from google.cloud import vision  # noqa: F401
        stdout.write('ocr_runtime google_vision_importable=true')
    except ImportError:
        return ['google-cloud-vision package is not installed.']

    try:
        from google.cloud import vision
        vision.ImageAnnotatorClient()
        stdout.write('ocr_runtime google_vision_client_ok=true')
    except Exception as exc:
        stdout.write('ocr_runtime google_vision_client_ok=false')
        safe_message = _mask_credentials(str(exc), credentials_path)
        return [f'Google Vision client creation failed: {safe_message[:200]}']

    return []


def _mask_credentials(message, credentials_path):
    if credentials_path:
        message = message.replace(credentials_path, '[redacted-path]')
    return message


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
