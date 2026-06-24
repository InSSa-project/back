from pathlib import Path

from django.test import SimpleTestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / '.github' / 'workflows' / 'manual-ocr-backfill.yml'


class ManualOcrBackfillWorkflowTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow_text = WORKFLOW_PATH.read_text(encoding='utf-8')

    def test_workflow_dispatch_inputs_are_single_raw_data_id_and_optional_force(self):
        text = self.workflow_text

        self.assertIn('name: Manual OCR Backfill', text)
        self.assertIn('workflow_dispatch:', text)
        self.assertIn('raw_data_id:', text)
        self.assertIn('required: true', text)
        self.assertIn('force:', text)
        self.assertIn('required: false', text)
        self.assertIn('type: boolean', text)
        self.assertIn('default: false', text)
        self.assertNotIn('schedule:', text)

    def test_workflow_reuses_operational_database_login_and_google_vision_env(self):
        text = self.workflow_text
        expected_env_names = [
            'SECRET_KEY',
            'DATABASE_URL',
            'DATABASE_SSL_REQUIRE',
            'DB_ENGINE',
            'DB_NAME',
            'DB_USER',
            'DB_PASSWORD',
            'DB_HOST',
            'DB_PORT',
            'DB_CONN_MAX_AGE',
            'SSAFY_ID',
            'SSAFY_PASSWORD',
            'SSAFY_LOGIN_URL',
            'OCR_PROVIDER',
            'GOOGLE_VISION_ENABLED',
            '_GOOGLE_VISION_CREDENTIALS_JSON',
        ]

        for env_name in expected_env_names:
            self.assertIn(env_name, text)

        self.assertIn('secrets.GOOGLE_VISION_CREDENTIALS_JSON', text)
        self.assertIn('google-vision-credentials.json', text)
        self.assertIn('GOOGLE_APPLICATION_CREDENTIALS=', text)
        self.assertIn('GITHUB_ENV', text)

    def test_backfill_command_targets_only_requested_raw_data_and_force_is_conditional(self):
        text = self.workflow_text

        self.assertIn('args=(--id "$RAW_DATA_ID" --authenticated-download --reparse)', text)
        self.assertIn('if [ "$FORCE" = "true" ]; then', text)
        self.assertIn('args+=(--force)', text)
        self.assertIn('python manage.py backfill_raw_ocr "${args[@]}"', text)
        self.assertEqual(text.count('python manage.py backfill_raw_ocr'), 1)
        self.assertNotIn('--limit', text)
        self.assertNotIn('--source-type', text)
