from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from schedules.models import ScheduleEvent
from sync.models import RawSsafyData


@override_settings(SECURE_SSL_REDIRECT=False)
class CalendarApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='calendar-user',
            email='calendar-user@example.com',
            password='password',
        )
        self.client.force_login(self.user)

    def test_calendar_events_include_schedule_event_without_raw_data(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        event = ScheduleEvent.objects.create(
            title='Rawless public schedule',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
            source_type='notice',
        )

        response = self.client.get(reverse('calendar-events'), {'start': '2026-06-01', 'end': '2026-06-30'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()['data']
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]['id'], event.id)
        self.assertIsNone(payload[0]['raw_data_id'])
        self.assertIsNone(payload[0]['source_url'])

    def test_calendar_events_do_not_apply_user_visible_notice_source_policy(self):
        hidden_raw = RawSsafyData.objects.create(source_type='geeknews', title='Hidden reference', raw_text='body')
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        rawless_event = ScheduleEvent.objects.create(
            title='Rawless notice schedule',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
            source_type='notice',
        )
        hidden_source_event = ScheduleEvent.objects.create(
            raw_data=hidden_raw,
            title='Hidden source schedule still visible on calendar',
            start_at=start_at + timedelta(hours=1),
            end_at=start_at + timedelta(hours=2),
            event_type='notice',
            source_type='geeknews',
        )

        response = self.client.get(reverse('calendar-events'), {'start': '2026-06-01', 'end': '2026-06-30'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {item['id'] for item in response.json()['data']},
            {rawless_event.id, hidden_source_event.id},
        )

    def test_calendar_events_return_schedules_when_raw_data_table_is_empty(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        ScheduleEvent.objects.create(
            title='Only schedule row',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            source_type='notice',
        )

        response = self.client.get(reverse('calendar-events'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(RawSsafyData.objects.count(), 0)
        self.assertEqual([item['title'] for item in response.json()['data']], ['Only schedule row'])

    def test_delete_calendar_event_removes_owned_personal_event(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        event = ScheduleEvent.objects.create(
            owner=self.user,
            title='Personal schedule',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='personal',
            source_type='manual',
        )

        response = self.client.delete(reverse('calendar-event-detail', args=[event.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'Schedule event deleted.')
        self.assertFalse(ScheduleEvent.objects.filter(pk=event.id).exists())

    def test_delete_calendar_event_rejects_other_users_event(self):
        other_user = get_user_model().objects.create_user(
            username='calendar-other-user',
            email='calendar-other-user@example.com',
            password='password',
        )
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        event = ScheduleEvent.objects.create(
            owner=other_user,
            title='Other user schedule',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='personal',
            source_type='manual',
        )

        response = self.client.delete(reverse('calendar-event-detail', args=[event.id]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(ScheduleEvent.objects.filter(pk=event.id).exists())

    def test_delete_calendar_event_rejects_public_event_for_regular_user(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        event = ScheduleEvent.objects.create(
            title='Public SSAFY schedule',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
            source_type='notice',
        )

        response = self.client.delete(reverse('calendar-event-detail', args=[event.id]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(ScheduleEvent.objects.filter(pk=event.id).exists())
