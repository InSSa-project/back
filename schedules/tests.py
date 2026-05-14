import json
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from schedules.models import ScheduleEvent
from sync.models import RawSsafyData
from sync.services.import_service import run_sample_notice_import
from sync.services.schedule_parser import parse_schedule_candidates


class ScheduleEventApiTests(TestCase):
    def test_event_list_filters_by_start_and_end_dates(self):
        run_sample_notice_import()

        response = self.client.get(
            reverse('schedule-event-list'),
            {'start': '2026-05-01', 'end': '2026-05-31'},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 3)
        self.assertEqual(payload[0]['event_type'], 'exam')
        self.assertIn('+09:00', payload[0]['start_at'])
        self.assertIn('source_url', payload[0])
        self.assertIn('source_title', payload[0])
        self.assertTrue(payload[0]['source_url'])
        self.assertTrue(payload[0]['source_title'])

    def test_event_list_returns_null_source_fields_without_raw_data(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        ScheduleEvent.objects.create(
            title='Manual event',
            description='Manually edited schedule',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            is_all_day=False,
            event_type='notice',
            source_type='manual',
        )

        response = self.client.get(reverse('schedule-event-list'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()[0]
        self.assertIsNone(payload['source_url'])
        self.assertIsNone(payload['source_title'])

    def test_patch_event_updates_editable_fields(self):
        run_sample_notice_import()
        event = ScheduleEvent.objects.first()

        response = self.client.patch(
            reverse('schedule-event-detail', args=[event.id]),
            data=json.dumps(
                {
                    'title': 'Updated title',
                    'description': 'Updated description',
                    'start_at': '2026-05-20T10:00:00+09:00',
                    'end_at': '2026-05-20T11:00:00+09:00',
                    'is_all_day': False,
                    'event_type': 'deadline',
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        event.refresh_from_db()
        payload = response.json()
        self.assertEqual(event.title, 'Updated title')
        self.assertEqual(event.event_type, 'deadline')
        self.assertEqual(payload['title'], 'Updated title')
        self.assertIn('+09:00', payload['start_at'])

    def test_patch_event_rejects_source_fields(self):
        run_sample_notice_import()
        event = ScheduleEvent.objects.first()

        response = self.client.patch(
            reverse('schedule-event-detail', args=[event.id]),
            data=json.dumps(
                {
                    'source_url': 'https://example.com/forged',
                    'raw_data': None,
                    'title': 'Should not update',
                }
            ),
            content_type='application/json',
        )

        event.refresh_from_db()
        self.assertEqual(response.status_code, 400)
        self.assertNotEqual(event.title, 'Should not update')

    def test_patch_event_rejects_invalid_datetime(self):
        run_sample_notice_import()
        event = ScheduleEvent.objects.first()

        response = self.client.patch(
            reverse('schedule-event-detail', args=[event.id]),
            data=json.dumps({'start_at': 'not-a-datetime'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)

    def test_deadline_single_time_does_not_become_one_hour_event(self):
        schedules = parse_schedule_candidates('2026.05.20 23:59 제출 마감', default_title='과제 제출 마감')

        self.assertEqual(len(schedules), 1)
        self.assertEqual(schedules[0].end_at - schedules[0].start_at, timedelta(minutes=1))

    def test_source_fields_use_raw_data(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/1',
            title='Source notice',
            raw_text='body',
        )
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='Linked event',
            description='description',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            is_all_day=False,
            event_type='notice',
            source_type='notice',
        )

        response = self.client.get(reverse('schedule-event-list'))

        payload = response.json()[0]
        self.assertEqual(payload['source_url'], 'https://edu.ssafy.com/notices/1')
        self.assertEqual(payload['source_title'], 'Source notice')

    def test_event_list_allows_local_frontend_origin(self):
        response = self.client.get(
            reverse('schedule-event-list'),
            HTTP_ORIGIN='http://localhost:5173',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Access-Control-Allow-Origin'], 'http://localhost:5173')
