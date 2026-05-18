from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from schedules.models import ScheduleEvent
from sync.models import RawSsafyData


class NoticeApiTests(TestCase):
    def test_notice_list_filters_by_source_type(self):
        RawSsafyData.objects.create(source_type='notice', title='공지', raw_text='본문')
        RawSsafyData.objects.create(source_type='academic_rule', title='학사 규정', raw_text='본문')

        response = self.client.get(reverse('notice-list'), {'source_type': 'notice'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(response.json()['results'][0]['source_type'], 'notice')

    def test_notice_list_filters_by_category(self):
        RawSsafyData.objects.create(source_type='notice', title='월말평가 안내', raw_text='Python 시험')
        RawSsafyData.objects.create(source_type='notice', title='멘토링 안내', raw_text='멘토링')

        response = self.client.get(reverse('notice-list'), {'category': 'exam'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(response.json()['results'][0]['category'], 'exam')

    def test_notice_list_filters_by_search(self):
        RawSsafyData.objects.create(source_type='notice', title='Python 보충 학습', raw_text='자료')
        RawSsafyData.objects.create(source_type='notice', title='Java 라이브', raw_text='자료')

        response = self.client.get(reverse('notice-list'), {'search': 'python'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(response.json()['results'][0]['track'], 'python')

    def test_notice_list_filters_by_track_with_common(self):
        RawSsafyData.objects.create(source_type='notice', title='Python 보충 학습', raw_text='파이썬')
        RawSsafyData.objects.create(source_type='notice', title='공통 안내', raw_text='전체 공통')
        RawSsafyData.objects.create(source_type='notice', title='Java 안내', raw_text='자바')

        response = self.client.get(reverse('notice-list'), {'track': 'python'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 2)
        self.assertEqual({item['track'] for item in response.json()['results']}, {'python', 'common'})

    def test_notice_detail_returns_linked_schedule_events(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/notices/1',
            title='Python 월말평가',
            raw_text='OCR text',
            metadata_json={'source_id': 'n1'},
        )
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='월말평가',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='exam',
            source_type='notice',
            metadata_json={'audience': {'track': 'python'}},
        )

        response = self.client.get(reverse('notice-detail', args=[raw_data.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['id'], raw_data.id)
        self.assertEqual(payload['ocr_text'], 'OCR text')
        self.assertEqual(payload['schedule_events'][0]['event_type'], 'exam')
        self.assertEqual(payload['schedule_events'][0]['source_url'], raw_data.source_url)
