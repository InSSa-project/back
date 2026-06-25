import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.calendar.models import UserScheduleEvent
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
        UserProfile.objects.create(user=self.user, track='Python')
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

    def test_calendar_events_exclude_hidden_reference_sources(self):
        hidden_raw = RawSsafyData.objects.create(
            source_type='mentoring_qna',
            source_url='https://edu.ssafy.com/edu/board/mentoQna/detail.do?id=1',
            title='Hidden reference',
            raw_text='body',
        )
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
            title='Hidden source schedule',
            start_at=start_at + timedelta(hours=1),
            end_at=start_at + timedelta(hours=2),
            event_type='notice',
            source_type='notice',
        )
        metadata_only_hidden_event = ScheduleEvent.objects.create(
            title='Metadata-only mentor schedule',
            start_at=start_at + timedelta(hours=2),
            end_at=start_at + timedelta(hours=3),
            event_type='notice',
            source_type='notice',
            metadata_json={
                'raw_data_id': hidden_raw.id,
                'source_url': hidden_raw.source_url,
            },
        )

        response = self.client.get(reverse('calendar-events'), {'start': '2026-06-01', 'end': '2026-06-30'})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(metadata_only_hidden_event.id)
        self.assertEqual(
            {item['id'] for item in response.json()['data']},
            {rawless_event.id},
        )

    def test_calendar_events_default_to_user_profile_track_and_keep_common_events(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        python_event = ScheduleEvent.objects.create(
            title='Python public notice schedule',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
            source_type='notice',
            metadata_json={'track_key': 'python', 'is_common': False},
        )
        common_event = ScheduleEvent.objects.create(
            title='Common public notice schedule',
            start_at=start_at + timedelta(hours=1),
            end_at=start_at + timedelta(hours=2),
            event_type='notice',
            source_type='notice',
            metadata_json={'track_key': 'all', 'is_common': True},
        )
        trackless_event = ScheduleEvent.objects.create(
            title='Trackless common notice schedule',
            start_at=start_at + timedelta(hours=2),
            end_at=start_at + timedelta(hours=3),
            event_type='notice',
            source_type='notice',
        )
        ScheduleEvent.objects.create(
            title='Java public notice schedule',
            start_at=start_at + timedelta(hours=3),
            end_at=start_at + timedelta(hours=4),
            event_type='notice',
            source_type='notice',
            metadata_json={'track_key': 'java_major', 'is_common': False},
        )

        response = self.client.get(reverse('calendar-events'), {'start': '2026-06-01', 'end': '2026-06-30'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {item['id'] for item in response.json()['data']},
            {python_event.id, common_event.id, trackless_event.id},
        )

    def test_calendar_events_include_generated_ssafy_event_with_required_fields(self):
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
            'can_edit',
            'can_delete',
            'is_user_override',
        ]:
            self.assertIn(field, item)
        self.assertEqual(item['display_title'], 'SSAFY real schedule')
        self.assertEqual(item['track_key'], 'python')
        self.assertEqual(item['source_url'], 'https://edu.ssafy.com/notices/real')
        self.assertEqual(item['description'], 'parsed schedule')
        self.assertTrue(item['can_edit'])
        self.assertTrue(item['can_delete'])
        self.assertFalse(item['is_user_override'])
        self.assertTrue(item['is_global'])
        self.assertFalse(item['is_common'])

    def test_calendar_monthly_query_prefetches_user_overrides_without_n_plus_one(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 1, 9, 0))
        first_event = ScheduleEvent.objects.create(
            title='Python schedule 0',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            source_type='notice',
            metadata_json={'track_key': 'python'},
        )
        UserScheduleEvent.objects.create(user=self.user, schedule_event=first_event, override_title='My Python schedule 0')

        with CaptureQueriesContext(connection) as single_event_queries:
            single_response = self.client.get(reverse('calendar-events'), {'start': '2026-06-01', 'end': '2026-06-30'})

        for index in range(1, 25):
            event = ScheduleEvent.objects.create(
                title=f'Python schedule {index}',
                start_at=start_at + timedelta(days=index % 20, hours=index % 4),
                end_at=start_at + timedelta(days=index % 20, hours=(index % 4) + 1),
                event_type='study',
                source_type='notice',
                metadata_json={'track_key': 'python'},
            )
            UserScheduleEvent.objects.create(user=self.user, schedule_event=event, override_title=f'My Python schedule {index}')

        with CaptureQueriesContext(connection) as many_event_queries:
            many_response = self.client.get(reverse('calendar-events'), {'start': '2026-06-01', 'end': '2026-06-30'})

        self.assertEqual(single_response.status_code, 200)
        self.assertEqual(many_response.status_code, 200)
        self.assertEqual(len(many_response.json()['data']), 25)
        self.assertLessEqual(len(many_event_queries), len(single_event_queries) + 3)

    def test_calendar_monthly_query_filters_range_and_user_track_in_database_scope(self):
        in_range = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        outside_range = timezone.make_aware(timezone.datetime(2026, 8, 10, 9, 0))
        expected = ScheduleEvent.objects.create(
            title='Python in range',
            start_at=in_range,
            end_at=in_range + timedelta(hours=1),
            event_type='study',
            source_type='notice',
            metadata_json={'track_key': 'python'},
        )
        ScheduleEvent.objects.create(
            title='Python outside range',
            start_at=outside_range,
            end_at=outside_range + timedelta(hours=1),
            event_type='study',
            source_type='notice',
            metadata_json={'track_key': 'python'},
        )
        ScheduleEvent.objects.create(
            title='Data in range',
            start_at=in_range,
            end_at=in_range + timedelta(hours=1),
            event_type='study',
            source_type='notice',
            metadata_json={'track_key': 'data'},
        )

        response = self.client.get(reverse('calendar-events'), {'start': '2026-06-01', 'end': '2026-06-30'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.json()['data']], [expected.id])

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

    def test_user_track_filter_ignores_other_track_query(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        python_event = ScheduleEvent.objects.create(
            title='Python schedule',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            source_type='notice',
            metadata_json={'track_key': 'python'},
        )
        ScheduleEvent.objects.create(
            title='Data schedule',
            start_at=start_at + timedelta(hours=1),
            end_at=start_at + timedelta(hours=2),
            event_type='study',
            source_type='notice',
            metadata_json={'track_key': 'data'},
        )

        response = self.client.get(reverse('calendar-events'), {'track': 'data'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.json()['data']], [python_event.id])

    def test_admin_can_read_all_and_filter_specific_track(self):
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        python_event = ScheduleEvent.objects.create(
            title='Python schedule',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            source_type='notice',
            metadata_json={'track_key': 'python'},
        )
        data_event = ScheduleEvent.objects.create(
            title='Data schedule',
            start_at=start_at + timedelta(hours=1),
            end_at=start_at + timedelta(hours=2),
            event_type='study',
            source_type='notice',
            metadata_json={'track_key': 'data'},
        )

        all_response = self.client.get(reverse('calendar-events'))
        data_response = self.client.get(reverse('calendar-events'), {'track': 'data'})

        self.assertEqual({item['id'] for item in all_response.json()['data']}, {python_event.id, data_event.id})
        self.assertEqual([item['id'] for item in data_response.json()['data']], [data_event.id])

    def test_profile_without_track_returns_explicit_error(self):
        self.user.profile.track = ''
        self.user.profile.save(update_fields=['track'])

        response = self.client.get(reverse('calendar-events'))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'PROFILE_INCOMPLETE')

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

    def test_shared_event_patch_creates_user_override_without_changing_original(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        new_start_at = timezone.make_aware(timezone.datetime(2026, 6, 12, 13, 0))
        event = ScheduleEvent.objects.create(
            title='Shared SSAFY schedule',
            description='Shared description',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
            source_type='notice',
            metadata_json={'track_key': 'all', 'is_common': True},
        )
        other_user = get_user_model().objects.create_user(
            username='override-other-user',
            email='override-other-user@example.com',
            password='password',
        )
        UserProfile.objects.create(user=other_user, track='Python')

        response = self.client.patch(
            reverse('calendar-event-detail', args=[event.id]),
            data=json.dumps(
                {
                    'title': 'My edited schedule',
                    'description': 'My edited description',
                    'event_type': 'project',
                    'start_at': new_start_at.isoformat(),
                    'end_at': (new_start_at + timedelta(hours=2)).isoformat(),
                    'is_all_day': False,
                }
            ),
            content_type='application/json',
        )

        event.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        payload = response.json()['data']
        self.assertEqual(payload['title'], 'My edited schedule')
        self.assertEqual(payload['description'], 'My edited description')
        self.assertEqual(payload['event_type'], 'project')
        self.assertFalse(payload['is_all_day'])
        self.assertTrue(payload['is_user_override'])
        self.assertNotIn('memo', payload)
        self.assertNotIn('display_memo', payload)
        self.assertNotIn('user_friendly_description', payload)
        self.assertEqual(event.title, 'Shared SSAFY schedule')
        self.assertEqual(event.description, 'Shared description')
        link = UserScheduleEvent.objects.get(user=self.user, schedule_event=event)
        self.assertEqual(link.override_title, 'My edited schedule')
        self.assertEqual(link.override_description, 'My edited description')

        self.client.force_login(other_user)
        other_response = self.client.get(reverse('calendar-event-detail', args=[event.id]))
        self.assertEqual(other_response.status_code, 200)
        self.assertEqual(other_response.json()['data']['title'], 'Shared SSAFY schedule')
        self.assertEqual(other_response.json()['data']['description'], 'Shared description')
        self.assertFalse(other_response.json()['data']['is_user_override'])

    def test_memo_only_patch_is_not_supported(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        event = ScheduleEvent.objects.create(
            title='Shared SSAFY schedule',
            description='Shared description',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
            source_type='notice',
            metadata_json={'track_key': 'all', 'is_common': True},
        )

        response = self.client.patch(
            reverse('calendar-event-detail', args=[event.id]),
            data=json.dumps({'memo': 'Not a supported field'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(UserScheduleEvent.objects.filter(user=self.user, schedule_event=event).exists())

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
        self.assertTrue(UserScheduleEvent.objects.get(user=self.user, schedule_event=event).is_hidden)

        second_response = self.client.delete(reverse('calendar-event-detail', args=[event.id]))
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(UserScheduleEvent.objects.filter(user=self.user, schedule_event=event).count(), 1)

        list_response = self.client.get(reverse('calendar-events'))
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()['data'], [])

    def test_hidden_public_event_stays_visible_for_other_users(self):
        other_user = get_user_model().objects.create_user(
            username='calendar-visible-user',
            email='calendar-visible-user@example.com',
            password='password',
        )
        UserProfile.objects.create(user=other_user, track='Python')
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        event = ScheduleEvent.objects.create(
            title='Shared SSAFY schedule',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
            source_type='notice',
        )
        UserScheduleEvent.objects.create(user=self.user, schedule_event=event, is_hidden=True)

        self.client.force_login(other_user)
        response = self.client.get(reverse('calendar-events'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.json()['data']], [event.id])

    def test_personal_event_patch_updates_owned_event(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        event = ScheduleEvent.objects.create(
            owner=self.user,
            title='Personal schedule',
            description='old',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='personal',
            source_type='manual',
        )

        response = self.client.patch(
            reverse('calendar-event-detail', args=[event.id]),
            data=json.dumps({'title': 'Updated personal schedule', 'description': 'new'}),
            content_type='application/json',
        )

        event.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(event.title, 'Updated personal schedule')
        self.assertEqual(event.description, 'new')
        self.assertFalse(UserScheduleEvent.objects.filter(user=self.user, schedule_event=event).exists())

    def test_holiday_patch_and_delete_are_read_only(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 6, 0, 0))
        event = ScheduleEvent.objects.create(
            title='현충일',
            description='',
            start_at=start_at,
            end_at=start_at + timedelta(days=1),
            is_all_day=True,
            event_type='holiday',
            source_type='national_holiday',
            metadata_json={'is_public_holiday': True, 'track_key': 'all', 'is_common': True},
        )

        patch_response = self.client.patch(
            reverse('calendar-event-detail', args=[event.id]),
            data=json.dumps({'title': 'Edited holiday'}),
            content_type='application/json',
        )
        delete_response = self.client.delete(reverse('calendar-event-detail', args=[event.id]))

        self.assertEqual(patch_response.status_code, 403)
        self.assertEqual(patch_response.json()['code'], 'HOLIDAY_READ_ONLY')
        self.assertEqual(delete_response.status_code, 403)
        self.assertEqual(delete_response.json()['code'], 'HOLIDAY_READ_ONLY')
        self.assertTrue(ScheduleEvent.objects.filter(pk=event.id).exists())
