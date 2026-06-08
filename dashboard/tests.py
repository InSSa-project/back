from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from schedules.models import ScheduleEvent
from sync.models import RawSsafyData


@override_settings(SECURE_SSL_REDIRECT=False)
class HomeDashboardApiTests(TestCase):
    def setUp(self):
        self.now = timezone.make_aware(datetime(2026, 6, 8, 10, 0))
        self.url = reverse('dashboard-home')

    def _get(self):
        with patch('dashboard.services.timezone.now', return_value=self.now):
            return self.client.get(self.url)

    def _event(self, title, start_offset_days, event_type='notice', metadata_json=None):
        start_at = self.now + timedelta(days=start_offset_days)
        return ScheduleEvent.objects.create(
            title=title,
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type=event_type,
            source_type='notice',
            metadata_json=metadata_json or {},
        )

    def test_home_dashboard_returns_200(self):
        response = self._get()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn('focus', payload)
        self.assertIn('highlights', payload)
        self.assertIn('upcoming_schedules', payload)
        self.assertIn('recent_notices', payload)

    def test_home_dashboard_returns_200_without_schedule_data(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='공지',
            raw_text='본문',
            collected_at=self.now,
        )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload['focus'])
        self.assertEqual(payload['upcoming_schedules'], [])

    def test_closest_future_deadline_is_selected_as_focus(self):
        later_event = self._event('나중 시작 일정', 2, event_type='exam')
        deadline_event = self._event(
            '마감 우선 일정',
            5,
            event_type='assignment',
            metadata_json={'deadline_at': (self.now + timedelta(days=1)).isoformat()},
        )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        focus = response.json()['focus']
        self.assertEqual(focus['source_event_id'], deadline_event.id)
        self.assertEqual(focus['remaining'], 'D-1')
        self.assertEqual(focus['time'], '10:00까지')
        self.assertNotEqual(focus['source_event_id'], later_event.id)

    def test_upcoming_schedules_are_limited_to_three(self):
        for index in range(5):
            self._event(f'다가오는 일정 {index}', index + 1)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        upcoming = response.json()['upcoming_schedules']
        self.assertEqual(len(upcoming), 3)
        self.assertEqual([item['title'] for item in upcoming], ['다가오는 일정 0', '다가오는 일정 1', '다가오는 일정 2'])

    def test_recent_notices_are_limited_to_three(self):
        for index in range(5):
            RawSsafyData.objects.create(
                source_type='notice',
                title=f'최근 공지 {index}',
                raw_text='본문',
                collected_at=self.now - timedelta(days=index),
            )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        notices = response.json()['recent_notices']
        self.assertEqual(len(notices), 3)
        self.assertEqual([item['title'] for item in notices], ['최근 공지 0', '최근 공지 1', '최근 공지 2'])
