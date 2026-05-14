import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from schedules.models import ScheduleEvent
from sync.models import CrawlJobLog, RawSsafyData
from sync.services.import_service import run_notice_import, run_sample_notice_import
from sync.services.ssafy_crawler import SsafyCrawlerError, _login_ssafy, load_ssafy_authenticated_documents


class SampleNoticeImportTests(TestCase):
    def test_run_crawl_api_imports_raw_data_and_schedule_events(self):
        response = self.client.post(reverse('sync-crawl-run'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], CrawlJobLog.STATUS_SUCCESS)
        self.assertEqual(payload['raw_count'], 3)
        self.assertEqual(payload['event_count'], 3)
        self.assertEqual(RawSsafyData.objects.count(), 3)
        self.assertEqual(ScheduleEvent.objects.count(), 3)

    def test_run_crawl_api_accepts_sample_mode(self):
        response = self.client.post(
            reverse('sync-crawl-run'),
            data=json.dumps({'mode': 'sample'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], CrawlJobLog.STATUS_SUCCESS)
        self.assertEqual(payload['raw_count'], 3)
        self.assertEqual(ScheduleEvent.objects.count(), 3)

    def test_invalid_mode_returns_error(self):
        response = self.client.post(
            reverse('sync-crawl-run'),
            data=json.dumps({'mode': 'unknown'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], 'error')
        self.assertEqual(payload['raw_count'], 0)
        self.assertEqual(payload['event_count'], 0)
        self.assertEqual(payload['failed_count'], 1)

    def test_duplicate_import_does_not_create_unbounded_events(self):
        first_log = run_sample_notice_import()
        second_log = run_sample_notice_import()

        self.assertEqual(first_log.event_count, 3)
        self.assertEqual(second_log.event_count, 0)
        self.assertEqual(second_log.skipped_count, 3)
        self.assertEqual(RawSsafyData.objects.count(), 3)
        self.assertEqual(ScheduleEvent.objects.count(), 3)

    def test_crawler_failure_keeps_existing_events(self):
        run_sample_notice_import()
        original_raw_count = RawSsafyData.objects.count()
        original_event_count = ScheduleEvent.objects.count()

        with patch(
            'sync.services.import_service.load_notices_by_mode',
            side_effect=SsafyCrawlerError('network failed'),
        ):
            response = self.client.post(
                reverse('sync-crawl-run'),
                data=json.dumps({'mode': 'ssafy_notice'}),
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], 'error')
        self.assertEqual(payload['raw_count'], 0)
        self.assertEqual(payload['event_count'], 0)
        self.assertEqual(payload['failed_count'], 1)
        self.assertEqual(RawSsafyData.objects.count(), original_raw_count)
        self.assertEqual(ScheduleEvent.objects.count(), original_event_count)

    def test_preflight_request_allows_local_frontend_origin(self):
        response = self.client.options(
            reverse('sync-crawl-run'),
            HTTP_ORIGIN='http://localhost:5173',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Access-Control-Allow-Origin'], 'http://localhost:5173')
        self.assertIn('POST', response['Access-Control-Allow-Methods'])

    def test_duplicate_notice_is_skipped_by_notice_id(self):
        items = [
            _notice_item('https://example.com/notices/1', 'notice-1'),
            _notice_item('https://example.com/notices/1-copy', 'notice-1'),
        ]

        with patch('sync.services.import_service.load_notices_by_mode', return_value=items):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(job_log.raw_count, 1)
        self.assertEqual(job_log.event_count, 1)
        self.assertEqual(job_log.skipped_count, 1)
        self.assertEqual(RawSsafyData.objects.count(), 1)
        self.assertEqual(ScheduleEvent.objects.count(), 1)

    def test_existing_data_is_not_deleted_when_duplicate_is_skipped(self):
        existing = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/notices/2',
            title='Existing notice',
            raw_text='기존 일정 2026.05.20',
            metadata_json={'notice_id': 'notice-2'},
            status=RawSsafyData.STATUS_PARSED,
        )
        ScheduleEvent.objects.create(
            raw_data=existing,
            title='Existing schedule',
            start_at='2026-05-20T00:00:00+09:00',
            end_at='2026-05-21T00:00:00+09:00',
            is_all_day=True,
            event_type='notice',
            source_type='notice',
            source_id=str(existing.pk),
        )

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[_notice_item(existing.source_url, 'notice-2')]):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(job_log.raw_count, 0)
        self.assertEqual(job_log.event_count, 0)
        self.assertEqual(job_log.skipped_count, 1)
        self.assertEqual(RawSsafyData.objects.count(), 1)
        self.assertEqual(ScheduleEvent.objects.count(), 1)

    def test_academic_rule_is_saved_without_schedule_event(self):
        items = [
            _notice_item('https://example.com/notices/3', 'notice-3'),
            _academic_rule_item('https://example.com/rules/1', 'rule-1'),
        ]

        with patch('sync.services.import_service.load_notices_by_mode', return_value=items):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(job_log.raw_count, 2)
        self.assertEqual(job_log.event_count, 1)
        self.assertEqual(job_log.skipped_count, 0)
        self.assertEqual(RawSsafyData.objects.filter(source_type='notice').count(), 1)
        self.assertEqual(RawSsafyData.objects.filter(source_type='academic_rule').count(), 1)
        self.assertEqual(ScheduleEvent.objects.count(), 1)

    def test_login_configuration_failure_returns_clear_error(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(SsafyCrawlerError) as error:
                load_ssafy_authenticated_documents()

        self.assertIn('Missing required SSAFY crawler environment variables', str(error.exception))

    def test_login_failure_raises_clear_error(self):
        page = _FailedLoginPage()

        with self.assertRaises(SsafyCrawlerError) as error:
            _login_ssafy(page, 'https://example.com/login', 'admin', 'secret')

        self.assertIn('SSAFY login failed', str(error.exception))


def _notice_item(source_url, notice_id):
    return {
        'source_type': 'notice',
        'source_url': source_url,
        'title': 'SSAFY notice',
        'raw_text': 'SSAFY 일정 2026.05.20',
        'raw_html': '<main>SSAFY 일정 2026.05.20</main>',
        'metadata_json': {
            'notice_id': notice_id,
            'published_at': '2026-05-14',
            'collected_from': 'ssafy_notice',
        },
    }


def _academic_rule_item(source_url, notice_id):
    return {
        'source_type': 'academic_rule',
        'source_url': source_url,
        'title': 'SSAFY academic rule',
        'raw_text': '학사규정 본문',
        'raw_html': '<main>학사규정 본문</main>',
        'metadata_json': {
            'notice_id': notice_id,
            'published_at': '2026-05-14',
            'collected_from': 'ssafy_notice',
        },
    }


class _FailedLoginPage:
    def goto(self, *args, **kwargs):
        return None

    def fill(self, *args, **kwargs):
        return None

    def click(self, *args, **kwargs):
        return None

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def locator(self, *args, **kwargs):
        return self

    def count(self):
        return 1
