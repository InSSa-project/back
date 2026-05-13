import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from schedules.models import ScheduleEvent
from sync.models import CrawlJobLog, RawSsafyData
from sync.services.import_service import run_sample_notice_import
from sync.services.ssafy_crawler import SsafyCrawlerError


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
        self.assertEqual(second_log.event_count, 3)
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
