from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.calendar.models import HiddenCalendarEvent
from apps.users.models import UserProfile
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

    def test_calendar_events_include_generated_ssafy_event_with_required_fields(self):
        UserProfile.objects.create(user=self.user, track='Python')
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/real',
            title='SSAFY source notice',
            raw_text='body',
        )
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 11, 9, 0))
        event = ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='Generated SSAFY real schedule',
            description='parsed schedule',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            source_type='notice',
            metadata_json={
                'display_title': 'SSAFY real schedule',
                'deadline_at': '2026-06-11T18:00:00+09:00',
                'audience': {'track': 'python', 'track_key': 'python'},
                'track_key': 'python',
                'is_common': False,
                'is_global': True,
            },
        )

        response = self.client.get(
            reverse('calendar-events'),
            {'start': '2026-06-01', 'end': '2026-06-30', 'track': 'python'},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()['data']
        self.assertEqual([item['id'] for item in payload], [event.id])
        item = payload[0]
        for field in [
            'id',
            'title',
            'display_title',
            'start_at',
            'end_at',
            'deadline_at',
            'event_type',
            'track_key',
            'is_common',
            'is_global',
            'source_url',
            'user_friendly_description',
            'display_memo',
        ]:
            self.assertIn(field, item)
        self.assertEqual(item['display_title'], 'SSAFY real schedule')
        self.assertEqual(item['track_key'], 'python')
        self.assertEqual(item['source_url'], 'https://edu.ssafy.com/notices/real')
        self.assertEqual(item['display_memo'], 'parsed schedule')
        self.assertTrue(item['is_global'])
        self.assertFalse(item['is_common'])

    def test_calendar_track_filter_keeps_common_events(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        python_event = ScheduleEvent.objects.create(
            title='Python schedule',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            source_type='notice',
            metadata_json={'track_key': 'python'},
        )
        common_event = ScheduleEvent.objects.create(
            title='Common SSAFY schedule',
            start_at=start_at + timedelta(hours=1),
            end_at=start_at + timedelta(hours=2),
            event_type='notice',
            source_type='notice',
            metadata_json={'track_key': 'all', 'is_common': True},
        )
        ScheduleEvent.objects.create(
            title='Java schedule',
            start_at=start_at + timedelta(hours=2),
            end_at=start_at + timedelta(hours=3),
            event_type='study',
            source_type='notice',
            metadata_json={'track_key': 'java_major'},
        )

        response = self.client.get(reverse('calendar-events'), {'track': 'python'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual({item['id'] for item in response.json()['data']}, {python_event.id, common_event.id})

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

    def test_delete_calendar_event_hides_public_event_for_user(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        event = ScheduleEvent.objects.create(
            title='Public SSAFY schedule',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
            source_type='notice',
        )

        response = self.client.delete(reverse('calendar-event-detail', args=[event.id]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(ScheduleEvent.objects.filter(pk=event.id).exists())
        self.assertTrue(HiddenCalendarEvent.objects.filter(user=self.user, schedule_event=event).exists())

        list_response = self.client.get(reverse('calendar-events'))
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()['data'], [])

    def test_hidden_public_event_stays_visible_for_other_users(self):
        other_user = get_user_model().objects.create_user(
            username='calendar-visible-user',
            email='calendar-visible-user@example.com',
            password='password',
        )
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        event = ScheduleEvent.objects.create(
            title='Shared SSAFY schedule',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
            source_type='notice',
        )
        HiddenCalendarEvent.objects.create(user=self.user, schedule_event=event)

        self.client.force_login(other_user)
        response = self.client.get(reverse('calendar-events'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.json()['data']], [event.id])
