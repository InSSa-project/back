import json
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from schedules.models import ScheduleEvent
from schedules.services import filter_events_for_user_profile
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
        self.assertIn('audience', payload[0])
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

    def test_post_event_creates_manual_event_without_raw_data_or_metadata(self):
        response = self.client.post(
            reverse('schedule-event-list'),
            data=json.dumps(
                {
                    'title': 'Manual study session',
                    'description': 'Review project notes',
                    'start_at': '2026-05-20T19:00:00+09:00',
                    'end_at': '2026-05-20T20:00:00+09:00',
                    'is_all_day': False,
                    'event_type': 'personal',
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        event = ScheduleEvent.objects.get()
        payload = response.json()
        self.assertEqual(event.title, 'Manual study session')
        self.assertEqual(event.source_type, 'manual')
        self.assertIsNone(event.raw_data)
        self.assertEqual(event.metadata_json, {'is_important': False})
        self.assertEqual(payload['event_type'], 'personal')
        self.assertIsNone(payload['source_url'])
        self.assertIsNone(payload['source_title'])

    def test_post_event_accepts_frontend_personal_event_payload(self):
        response = self.client.post(
            reverse('schedule-event-list'),
            data=json.dumps(
                {
                    'title': 'Calendar payload event',
                    'description': 'Created from frontend',
                    'event_type': 'personal',
                    'start_at': '2026-05-22 09:30:00',
                    'end_at': '2026-05-22 10:30:00',
                    'deadline_at': '2026-05-22T10:30',
                    'is_global': False,
                    'metadata_json': {'color': 'green'},
                    'source_type': 'manual',
                    'track': 'python',
                    'is_important': True,
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload['event_type'], 'personal')
        self.assertEqual(payload['source_type'], 'manual')
        self.assertEqual(payload['metadata_json']['track'], 'python')
        self.assertFalse(payload['metadata_json']['is_global'])
        self.assertTrue(payload['metadata_json']['is_important'])
        self.assertTrue(payload['is_important'])
        self.assertIn('deadline_at', payload['metadata_json'])

    def test_post_event_accepts_all_day_frontend_datetimes(self):
        response = self.client.post(
            reverse('schedule-event-list'),
            data=json.dumps(
                {
                    'title': 'All day personal event',
                    'start_at': '2026-05-22T00:00:00',
                    'end_at': '2026-05-22T23:59:59',
                    'is_all_day': True,
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertTrue(payload['is_all_day'])
        self.assertEqual(payload['event_type'], 'personal')
        self.assertFalse(payload['metadata_json']['is_important'])
        self.assertFalse(payload['is_important'])
        self.assertIn('2026-05-22T00:00:00', payload['start_at'])
        self.assertIn('2026-05-22T23:59:59', payload['end_at'])

    def test_post_event_rejects_end_before_start(self):
        response = self.client.post(
            reverse('schedule-event-list'),
            data=json.dumps(
                {
                    'title': 'Invalid personal event',
                    'start_at': '2026-05-22T11:00:00',
                    'end_at': '2026-05-22T10:00:00',
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['detail'], 'End time must be after start time.')

    def test_post_event_rejects_unsupported_event_type(self):
        response = self.client.post(
            reverse('schedule-event-list'),
            data=json.dumps(
                {
                    'title': 'Invalid event type',
                    'start_at': '2026-05-22T10:00:00',
                    'end_at': '2026-05-22T11:00:00',
                    'event_type': 'surprise',
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['detail'], 'Unsupported event_type: surprise')

    def test_created_manual_event_is_returned_by_event_list(self):
        self.client.post(
            reverse('schedule-event-list'),
            data=json.dumps(
                {
                    'title': 'Manual visible event',
                    'start_at': '2026-05-20T09:00:00+09:00',
                    'end_at': '2026-05-20T10:00:00+09:00',
                    'event_type': 'personal',
                    'is_all_day': False,
                }
            ),
            content_type='application/json',
        )

        response = self.client.get(
            reverse('schedule-event-list'),
            {'start': '2026-05-01', 'end': '2026-05-31'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['title'] for item in response.json()], ['Manual visible event'])

        personal_response = self.client.get(reverse('schedule-event-list'), {'event_type': 'personal'})
        self.assertEqual([item['title'] for item in personal_response.json()], ['Manual visible event'])

    def test_post_event_allows_personal_event_on_korean_holiday(self):
        response = self.client.post(
            reverse('schedule-event-list'),
            data=json.dumps(
                {
                    'title': 'Holiday personal plan',
                    'start_at': '2026-05-05T09:00:00+09:00',
                    'end_at': '2026-05-05T10:00:00+09:00',
                    'event_type': 'personal',
                    'is_all_day': False,
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(ScheduleEvent.objects.get().title, 'Holiday personal plan')

    def test_event_list_returns_important_event_first_on_same_date(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 22, 9, 0))
        ScheduleEvent.objects.create(
            title='Normal early event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='personal',
            metadata_json={'is_important': False},
        )
        ScheduleEvent.objects.create(
            title='Important later event',
            start_at=start_at + timedelta(hours=2),
            end_at=start_at + timedelta(hours=3),
            event_type='personal',
            metadata_json={'is_important': True},
        )

        response = self.client.get(reverse('schedule-event-list'), {'event_type': 'personal'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['title'] for item in response.json()], ['Important later event', 'Normal early event'])

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

    def test_event_list_filters_by_event_type(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        ScheduleEvent.objects.create(
            title='Exam event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='exam',
        )
        ScheduleEvent.objects.create(
            title='Notice event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
        )

        response = self.client.get(reverse('schedule-event-list'), {'event_type': 'exam'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]['event_type'], 'exam')

    def test_event_list_filters_by_track_and_keeps_common_events(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        ScheduleEvent.objects.create(
            title='Python event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='exam',
            metadata_json={'audience': {'track': 'python'}},
        )
        ScheduleEvent.objects.create(
            title='Common event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
            metadata_json={'audience': {'track': None}},
        )
        ScheduleEvent.objects.create(
            title='Java event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
            metadata_json={'audience': {'track': 'java'}},
        )

        response = self.client.get(reverse('schedule-event-list'), {'track': 'python'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual({item['title'] for item in response.json()}, {'Python event', 'Common event'})

    def test_event_list_filters_by_top_level_metadata_track(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        ScheduleEvent.objects.create(
            title='Meister event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            metadata_json={'track': 'meister'},
        )
        ScheduleEvent.objects.create(
            title='Python event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            metadata_json={'track': 'python'},
        )

        response = self.client.get(reverse('schedule-event-list'), {'track': 'meister'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]['title'], 'Meister event')
        self.assertEqual(payload[0]['metadata']['track'], 'meister')

    def test_event_list_track_filter_keeps_common_title_events(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        ScheduleEvent.objects.create(
            title='Meister event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            metadata_json={'track': 'meister'},
        )
        ScheduleEvent.objects.create(
            title='SSAFY DAY',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='etc',
            metadata_json={'track': 'python'},
        )
        ScheduleEvent.objects.create(
            title='Python only event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            metadata_json={'track': 'python'},
        )

        response = self.client.get(reverse('schedule-event-list'), {'track': 'meister'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual({item['title'] for item in response.json()}, {'Meister event', 'SSAFY DAY'})

    def test_event_list_other_event_type_includes_frontend_other_group(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        for index, event_type in enumerate(
            ['study', 'assignment', 'lecture', 'deadline', 'notice', 'mentoring', 'unknown', 'other', '기타'],
            start=1,
        ):
            ScheduleEvent.objects.create(
                title=f'{event_type} event',
                start_at=start_at + timedelta(minutes=index),
                end_at=start_at + timedelta(minutes=index + 1),
                event_type=event_type,
            )
        ScheduleEvent.objects.create(
            title='Exam event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='exam',
        )
        ScheduleEvent.objects.create(
            title='Project event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='project',
        )

        response = self.client.get(reverse('schedule-event-list'), {'event_type': 'other'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {item['event_type'] for item in response.json()},
            {'study', 'assignment', 'lecture', 'deadline', 'notice', 'mentoring', 'unknown', 'other', '기타'},
        )

    def test_event_list_exam_event_type_returns_only_exam_events(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        ScheduleEvent.objects.create(
            title='Exam event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='exam',
        )
        ScheduleEvent.objects.create(
            title='Study event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
        )

        response = self.client.get(reverse('schedule-event-list'), {'event_type': 'exam'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['event_type'] for item in response.json()], ['exam'])

    def test_event_list_combines_track_and_other_event_type_filters(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        ScheduleEvent.objects.create(
            title='Meister study',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            metadata_json={'track': 'meister'},
        )
        ScheduleEvent.objects.create(
            title='온라인 위크',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            metadata_json={'track': 'python'},
        )
        ScheduleEvent.objects.create(
            title='Meister exam',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='exam',
            metadata_json={'track': 'meister'},
        )
        ScheduleEvent.objects.create(
            title='Python study',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            metadata_json={'track': 'python'},
        )

        response = self.client.get(reverse('schedule-event-list'), {'track': 'meister', 'event_type': 'other'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual({item['title'] for item in payload}, {'Meister study', '온라인 위크'})
        self.assertEqual({item['event_type'] for item in payload}, {'study'})

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

    def test_delete_event_removes_manual_personal_event(self):
        event = ScheduleEvent.objects.create(
            title='Manual personal event',
            start_at=timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0)),
            end_at=timezone.make_aware(timezone.datetime(2026, 5, 20, 10, 0)),
            event_type='personal',
            source_type='manual',
        )

        response = self.client.delete(reverse('schedule-event-detail', args=[event.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ScheduleEvent.objects.count(), 0)

    def test_delete_event_rejects_generated_event(self):
        raw_data = RawSsafyData.objects.create(source_type='notice', title='Generated source', raw_text='body')
        event = ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='Generated event',
            start_at=timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0)),
            end_at=timezone.make_aware(timezone.datetime(2026, 5, 20, 10, 0)),
            event_type='study',
            source_type='notice',
        )

        response = self.client.delete(reverse('schedule-event-detail', args=[event.id]))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(ScheduleEvent.objects.count(), 1)

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

    def test_imported_events_store_audience_metadata_from_raw_data(self):
        run_sample_notice_import()

        event = ScheduleEvent.objects.first()

        self.assertIn('audience', event.metadata_json)
        self.assertIn('generation', event.metadata_json['audience'])
        self.assertIn('track', event.metadata_json['audience'])
        self.assertIn('class_number', event.metadata_json['audience'])
        self.assertIn('campus', event.metadata_json['audience'])

    def test_profile_filter_keeps_unrestricted_events_and_matching_audience(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        unrestricted = ScheduleEvent.objects.create(
            title='Common event',
            description='all',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
            metadata_json={'audience': {'track': None, 'generation': None, 'class_number': None, 'campus': None}},
        )
        matching = ScheduleEvent.objects.create(
            title='15기 SW event',
            description='match',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
            metadata_json={'audience': {'track': 'SW', 'generation': 15, 'class_number': None, 'campus': None}},
        )
        other_track = ScheduleEvent.objects.create(
            title='AI event',
            description='other',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
            metadata_json={'audience': {'track': 'AI', 'generation': 15, 'class_number': None, 'campus': None}},
        )
        profile = type('Profile', (), {'track': 'SW', 'generation': 15, 'class_number': None, 'campus': None})()

        filtered = filter_events_for_user_profile([unrestricted, matching, other_track], profile)

        self.assertEqual(filtered, [unrestricted, matching])

    def test_event_list_allows_local_frontend_origin(self):
        response = self.client.get(
            reverse('schedule-event-list'),
            HTTP_ORIGIN='http://localhost:5173',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Access-Control-Allow-Origin'], 'http://localhost:5173')
        self.assertIn('GET', response['Access-Control-Allow-Methods'])

    def test_event_detail_preflight_allows_patch_from_local_frontend(self):
        response = self.client.options(
            reverse('schedule-event-detail', args=[1]),
            HTTP_ORIGIN='http://localhost:5173',
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='PATCH',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Access-Control-Allow-Origin'], 'http://localhost:5173')
        self.assertIn('PATCH', response['Access-Control-Allow-Methods'])
        self.assertIn('PUT', response['Access-Control-Allow-Methods'])
        self.assertIn('DELETE', response['Access-Control-Allow-Methods'])
