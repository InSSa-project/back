from django.test import TestCase
from django.urls import reverse

from schedules.models import ScheduleEvent
from sync.models import CrawlJobLog, RawSsafyData


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

