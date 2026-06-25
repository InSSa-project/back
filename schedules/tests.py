import json
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ai_server.core.config import get_settings
from apps.ai.models import AiDocument
from apps.users.jwt.service import JwtService
from apps.users.models import UserProfile
from schedules.models import ScheduleEvent
from schedules.services import build_generated_event_metadata, filter_events_for_user_profile
from schedules.utils import is_meaningless_schedule_title, normalize_schedule_display_title
from sync.models import RawSsafyData
from sync.services.import_service import run_sample_notice_import
from sync.services.schedule_parser import parse_schedule_candidates


class ScheduleDisplayTitleTests(TestCase):
    def test_normalize_schedule_display_title_examples(self):
        cases = {
            '[학습] 9:00 10:00 [Live 방송] JS Basic Syntax1': 'Basic Syntax',
            '[학습] 10:00 11:00 [실습 및 Q&A]': '실습 Q&A',
            '[학습] 9:00 10:00 과목 평가': '과목 평가',
            '[학습] Django: DRF 1': 'Django: DRF 1',
        }

        for raw_title, expected in cases.items():
            with self.subTest(raw_title=raw_title):
                self.assertEqual(normalize_schedule_display_title(raw_title), expected)

    def test_meaningless_title_filter_blocks_fragments_but_keeps_meaningful_titles(self):
        meaningless_titles = ['00-12', 'DB', 'JS', '&A', '5/12', '5월', '12일', 'SAMSUNG', 'AI ACADEMY', 'FOR YOUTH']
        meaningful_titles = ['DB 설계', 'JS DOM', 'Django DRF', '실습 Q&A']

        for title in meaningless_titles:
            with self.subTest(title=title):
                self.assertTrue(is_meaningless_schedule_title(title))

        for title in meaningful_titles:
            with self.subTest(title=title):
                self.assertFalse(is_meaningless_schedule_title(title))


@override_settings(
    SECURE_SSL_REDIRECT=False,
    CORS_ALLOWED_ORIGINS=['http://localhost:5173', 'http://127.0.0.1:5173'],
)
class ScheduleEventApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='user-a',
            email='user-a@example.com',
            password='password',
        )
        self.other_user = get_user_model().objects.create_user(
            username='user-b',
            email='user-b@example.com',
            password='password',
        )
        self.client.force_login(self.user)

    def _bearer(self, user):
        return f"Bearer {JwtService().issue_token(user, token_type='access')}"

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
        self.assertIn('display_title', payload[0])
        self.assertTrue(payload[0]['source_url'])
        self.assertTrue(payload[0]['source_title'])

    def test_event_list_defaults_to_user_profile_track(self):
        UserProfile.objects.create(user=self.user, track='python')
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 25, 9, 0))
        ScheduleEvent.objects.create(
            title='Python track event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            source_type='notice',
            metadata_json={'audience': {'track': 'python', 'track_key': 'python'}, 'track_key': 'python'},
        )
        ScheduleEvent.objects.create(
            title='Embedded track event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            source_type='notice',
            metadata_json={'audience': {'track': 'embedded', 'track_key': 'embedded'}, 'track_key': 'embedded'},
        )
        ScheduleEvent.objects.create(
            title='Common event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            source_type='notice',
            metadata_json={'audience': {'track': 'all', 'track_key': 'all'}, 'track_key': 'all', 'is_common': True},
        )

        response = self.client.get(
            reverse('schedule-event-list'),
            {'start': '2026-06-25', 'end': '2026-06-25'},
        )

        self.assertEqual(response.status_code, 200)
        titles = [item['title'] for item in response.json()]
        self.assertIn('Python track event', titles)
        self.assertIn('Common event', titles)
        self.assertNotIn('Embedded track event', titles)

    def test_event_list_all_track_param_disables_user_profile_track_filter(self):
        UserProfile.objects.create(user=self.user, track='python')
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 25, 9, 0))
        for title, track in [('Python track event', 'python'), ('Embedded track event', 'embedded')]:
            ScheduleEvent.objects.create(
                title=title,
                start_at=start_at,
                end_at=start_at + timedelta(hours=1),
                event_type='study',
                source_type='notice',
                metadata_json={'audience': {'track': track, 'track_key': track}, 'track_key': track},
            )

        response = self.client.get(
            reverse('schedule-event-list'),
            {'start': '2026-06-25', 'end': '2026-06-25', 'track': 'all'},
        )

        self.assertEqual(response.status_code, 200)
        titles = [item['title'] for item in response.json()]
        self.assertIn('Python track event', titles)
        self.assertIn('Embedded track event', titles)

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
        self.assertEqual(event.owner, self.user)
        self.assertEqual(event.metadata_json, {'user_id': self.user.id, 'is_global': False, 'is_important': False})
        self.assertEqual(payload['event_type'], 'personal')
        self.assertIsNone(payload['source_url'])
        self.assertIsNone(payload['source_title'])
        self.assertEqual(payload['display_title'], 'Manual study session')

    def test_event_list_prefers_metadata_display_title(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        ScheduleEvent.objects.create(
            title='[학습] 9:00 10:00 [Live 방송] JS Basic Syntax1',
            description='OCR source text',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            is_all_day=False,
            event_type='study',
            metadata_json={'display_title': 'Basic Syntax', 'raw_title': '[학습] 9:00 10:00 [Live 방송] JS Basic Syntax1'},
        )

        response = self.client.get(reverse('schedule-event-list'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()[0]
        self.assertEqual(payload['title'], '[학습] 9:00 10:00 [Live 방송] JS Basic Syntax1')
        self.assertEqual(payload['display_title'], 'Basic Syntax')
        self.assertEqual(payload['metadata_json']['raw_title'], '[학습] 9:00 10:00 [Live 방송] JS Basic Syntax1')

    def test_event_list_normalizes_display_title_without_metadata(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        ScheduleEvent.objects.create(
            title='[학습] 9:00 10:00 [Live 방송] JS Basic Syntax1',
            description='OCR source text',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            is_all_day=False,
            event_type='study',
        )

        response = self.client.get(reverse('schedule-event-list'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()[0]
        self.assertEqual(payload['display_title'], 'Basic Syntax')

    def test_post_event_does_not_ingest_personal_event_to_rag(self):
        index_path = Path(settings.BASE_DIR) / 'var' / 'test' / f'{uuid4().hex}.json'
        try:
            with patch.dict(
                'os.environ',
                {
                    'EMBEDDING_PROVIDER': 'local',
                    'VECTORSTORE_PROVIDER': 'faiss',
                    'VECTORSTORE_PATH': str(index_path),
                },
            ):
                get_settings.cache_clear()
                response = self.client.post(
                    reverse('schedule-event-list'),
                    data=json.dumps(
                        {
                            'title': 'Personal RAG study',
                            'description': 'Review dynamic programming',
                            'start_at': '2026-05-28T19:00:00+09:00',
                            'end_at': '2026-05-28T20:00:00+09:00',
                            'event_type': 'personal',
                        }
                    ),
                    content_type='application/json',
                )

            self.assertEqual(response.status_code, 201)
            event = ScheduleEvent.objects.get(title='Personal RAG study')
            self.assertFalse(AiDocument.objects.filter(schedule_event=event).exists())
            self.assertFalse(index_path.exists())
        finally:
            get_settings.cache_clear()
            if index_path.exists():
                index_path.unlink()

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
        self.assertEqual(payload['owner_id'], self.user.id)
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

    def test_event_list_includes_generated_event_from_raw_data(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/generated',
            title='Generated source title',
            raw_text='body',
        )
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='Generated SSAFY event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='generated',
            source_type='notice',
            metadata_json={
                'track_key': 'python',
                'is_common': False,
                'is_global': True,
                'is_important': False,
            },
        )

        response = self.client.get(reverse('schedule-event-list'), {'start': '2026-06-10', 'end': '2026-06-10'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([item['title'] for item in payload], ['Generated SSAFY event'])
        self.assertTrue(payload[0]['is_generated'])
        self.assertTrue(payload[0]['is_global'])
        self.assertFalse(payload[0]['is_important'])

    def test_event_list_excludes_mentoring_reference_events(self):
        notice_raw = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/edu/board/notice/detail.do?id=1',
            title='Official notice',
            raw_text='body',
        )
        mentoring_raw = RawSsafyData.objects.create(
            source_type='mentoring_qna',
            source_url='https://edu.ssafy.com/edu/board/mentoQna/detail.do?id=2',
            title='Mentor QnA',
            raw_text='body',
        )
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        public_notice = ScheduleEvent.objects.create(
            raw_data=notice_raw,
            title='Official notice schedule',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
            source_type='notice',
        )
        personal_event = ScheduleEvent.objects.create(
            owner=self.user,
            title='Personal schedule',
            start_at=start_at + timedelta(hours=1),
            end_at=start_at + timedelta(hours=2),
            event_type='personal',
            source_type='manual',
        )
        holiday_event = ScheduleEvent.objects.create(
            title='National holiday',
            start_at=start_at + timedelta(hours=2),
            end_at=start_at + timedelta(hours=3),
            event_type='holiday',
            source_type='national_holiday',
        )
        ScheduleEvent.objects.create(
            raw_data=mentoring_raw,
            title='Mentoring raw schedule',
            start_at=start_at + timedelta(hours=3),
            end_at=start_at + timedelta(hours=4),
            event_type='notice',
            source_type='notice',
        )
        ScheduleEvent.objects.create(
            title='Mentoring metadata schedule',
            start_at=start_at + timedelta(hours=4),
            end_at=start_at + timedelta(hours=5),
            event_type='notice',
            source_type='notice',
            metadata_json={
                'raw_data_id': mentoring_raw.id,
                'source_url': mentoring_raw.source_url,
            },
        )
        ScheduleEvent.objects.create(
            title='Mentoring event type schedule',
            start_at=start_at + timedelta(hours=5),
            end_at=start_at + timedelta(hours=6),
            event_type='mentoring',
            source_type='notice',
        )

        response = self.client.get(reverse('schedule-event-list'), {'start': '2026-06-10', 'end': '2026-06-10'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {item['id'] for item in response.json()},
            {public_notice.id, personal_event.id, holiday_event.id},
        )

    def test_authenticated_profile_mismatch_hides_other_track_generated_events_by_default(self):
        UserProfile.objects.create(
            user=self.user,
            track='Python',
            generation=12,
            campus='서울',
            class_number=17,
        )
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/generated-profile',
            title='Generated source title',
            raw_text='body',
        )
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='Public generated event for another audience',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            source_type='notice',
            metadata_json={
                'raw_data_id': raw_data.id,
                'source_url': raw_data.source_url,
                'source_title': raw_data.title,
                'audience': {
                    'track': 'Java',
                    'track_key': 'java_major',
                    'generation': 15,
                    'campus': '대전',
                    'class_number': 1,
                },
                'track_key': 'java_major',
                'is_common': False,
            },
        )
        ScheduleEvent.objects.create(
            owner=self.other_user,
            title='Other user private event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='personal',
            source_type='manual',
        )

        response = self.client.get(
            reverse('schedule-event-list'),
            {'start': '2026-04-26', 'end': '2026-06-06'},
        )

        self.assertEqual(response.status_code, 200)
        titles = [item['title'] for item in response.json()]
        self.assertNotIn('Public generated event for another audience', titles)
        self.assertNotIn('Other user private event', titles)

        all_response = self.client.get(
            reverse('schedule-event-list'),
            {'start': '2026-04-26', 'end': '2026-06-06', 'track': 'all'},
        )
        self.assertEqual(all_response.status_code, 200)
        all_titles = [item['title'] for item in all_response.json()]
        self.assertIn('Public generated event for another audience', all_titles)

    def test_event_list_includes_event_when_only_deadline_is_in_range(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 1, 9, 0))
        ScheduleEvent.objects.create(
            title='Deadline only metadata event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='deadline',
            source_type='notice',
            metadata_json={'deadline_at': '2026-06-10T23:59:00+09:00'},
        )

        response = self.client.get(reverse('schedule-event-list'), {'start': '2026-06-10', 'end': '2026-06-10'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([item['title'] for item in payload], ['Deadline only metadata event'])
        self.assertEqual(payload[0]['deadline_at'], '2026-06-10T23:59:00+09:00')

    def test_event_list_includes_event_overlapping_range(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 9, 9, 0))
        ScheduleEvent.objects.create(
            title='Multi day generated event',
            start_at=start_at,
            end_at=timezone.make_aware(timezone.datetime(2026, 6, 11, 18, 0)),
            event_type='generated',
            source_type='notice',
        )

        response = self.client.get(reverse('schedule-event-list'), {'start': '2026-06-10', 'end': '2026-06-10'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['title'] for item in response.json()], ['Multi day generated event'])

    def test_event_list_generated_response_contains_calendar_fields(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/906',
            title='Original notice title',
            raw_text='body',
        )
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='Generated field event',
            description='description',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='evaluation',
            source_type='notice',
            metadata_json={
                'display_title': 'Display field event',
                'deadline_at': '2026-06-10T18:00:00+09:00',
                'track_key': 'python',
                'is_common': False,
                'is_global': True,
                'is_important': True,
            },
        )

        response = self.client.get(reverse('schedule-event-list'), {'start': '2026-06-10', 'end': '2026-06-10'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()[0]
        for field in [
            'display_title',
            'track_key',
            'is_common',
            'is_global',
            'is_generated',
            'is_important',
            'raw_data_id',
            'source_url',
            'source_title',
            'deadline_at',
        ]:
            self.assertIn(field, payload)
        self.assertEqual(payload['display_title'], 'Display field event')
        self.assertEqual(payload['track_key'], 'python')
        self.assertFalse(payload['is_common'])
        self.assertTrue(payload['is_global'])
        self.assertTrue(payload['is_generated'])
        self.assertTrue(payload['is_important'])
        self.assertEqual(payload['raw_data_id'], raw_data.id)
        self.assertEqual(payload['source_url'], 'https://edu.ssafy.com/notices/906')
        self.assertEqual(payload['source_title'], 'Original notice title')

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
        self.assertEqual(ScheduleEvent.objects.get().owner, self.user)

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
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        event = ScheduleEvent.objects.create(
            owner=self.user,
            title='Owned personal event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='personal',
            source_type='manual',
        )

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

    def test_patch_event_updates_is_important_true(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        event = ScheduleEvent.objects.create(
            owner=self.user,
            title='Owned personal event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='personal',
            source_type='manual',
            metadata_json={'is_important': False},
        )

        response = self.client.patch(
            reverse('schedule-event-detail', args=[event.id]),
            data=json.dumps({'is_important': True}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        event.refresh_from_db()
        self.assertTrue(event.metadata_json['is_important'])
        self.assertTrue(response.json()['is_important'])

    def test_patch_event_updates_is_important_false(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        event = ScheduleEvent.objects.create(
            owner=self.user,
            title='Owned personal event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='personal',
            source_type='manual',
            metadata_json={'is_important': True},
        )

        response = self.client.patch(
            reverse('schedule-event-detail', args=[event.id]),
            data=json.dumps({'is_important': False}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        event.refresh_from_db()
        self.assertFalse(event.metadata_json['is_important'])
        self.assertFalse(response.json()['is_important'])

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

    def test_event_list_filters_java_alias_canonically(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        ScheduleEvent.objects.create(
            title='Java alias event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            metadata_json={'track': 'Java'},
        )
        ScheduleEvent.objects.create(
            title='Python event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            metadata_json={'track': 'python'},
        )

        response = self.client.get(reverse('schedule-event-list'), {'track': 'java_major'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['title'] for item in response.json()], ['Java alias event'])

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
            {'study', 'assignment', 'lecture', 'deadline', 'notice', 'unknown', 'other', '기타'},
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
        online_week = next(item for item in payload if item['title'] == '온라인 위크')
        self.assertEqual(online_week['track_key'], 'all')
        self.assertTrue(online_week['is_common'])

    def test_patch_event_rejects_source_fields(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        event = ScheduleEvent.objects.create(
            owner=self.user,
            title='Owned personal event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='personal',
            source_type='manual',
        )

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
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        event = ScheduleEvent.objects.create(
            owner=self.user,
            title='Owned personal event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='personal',
            source_type='manual',
        )

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
            owner=self.user,
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

    def test_other_user_cannot_see_modify_or_delete_owned_personal_event(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        ScheduleEvent.objects.create(
            owner=self.user,
            title='User A private event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='personal',
            source_type='manual',
        )
        public_event = ScheduleEvent.objects.create(
            title='Public SSAFY event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
            source_type='notice',
        )

        self.client.force_login(self.other_user)
        response = self.client.get(reverse('schedule-event-list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['title'] for item in response.json()], ['Public SSAFY event'])

        private_event = ScheduleEvent.objects.get(title='User A private event')
        patch_response = self.client.patch(
            reverse('schedule-event-detail', args=[private_event.id]),
            data=json.dumps({'title': 'Stolen title'}),
            content_type='application/json',
        )
        delete_response = self.client.delete(reverse('schedule-event-detail', args=[private_event.id]))
        self.assertEqual(patch_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)
        self.assertTrue(ScheduleEvent.objects.filter(pk=private_event.pk).exists())
        self.assertTrue(ScheduleEvent.objects.filter(pk=public_event.pk).exists())

    def test_regular_user_cannot_modify_public_event(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        event = ScheduleEvent.objects.create(
            title='Public SSAFY event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='notice',
            source_type='notice',
        )

        response = self.client.patch(
            reverse('schedule-event-detail', args=[event.id]),
            data=json.dumps({'title': 'Nope'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        event.refresh_from_db()
        self.assertEqual(event.title, 'Public SSAFY event')

    def test_regular_user_cannot_patch_generated_event_importance(self):
        raw_data = RawSsafyData.objects.create(source_type='notice', title='Generated source', raw_text='body')
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        event = ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='Generated public event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='generated',
            source_type='notice',
            metadata_json={'is_important': False},
        )

        response = self.client.patch(
            reverse('schedule-event-detail', args=[event.id]),
            data=json.dumps({'is_important': True}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        event.refresh_from_db()
        self.assertFalse(event.metadata_json['is_important'])

    def test_anonymous_user_cannot_create_modify_or_delete_personal_event(self):
        self.client.logout()
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        event = ScheduleEvent.objects.create(
            owner=self.user,
            title='User A private event',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='personal',
            source_type='manual',
        )

        create_response = self.client.post(
            reverse('schedule-event-list'),
            data=json.dumps(
                {
                    'title': 'Anonymous event',
                    'start_at': '2026-05-20T09:00:00+09:00',
                    'end_at': '2026-05-20T10:00:00+09:00',
                }
            ),
            content_type='application/json',
        )
        patch_response = self.client.patch(
            reverse('schedule-event-detail', args=[event.id]),
            data=json.dumps({'title': 'Anonymous patch'}),
            content_type='application/json',
        )
        delete_response = self.client.delete(reverse('schedule-event-detail', args=[event.id]))

        self.assertEqual(create_response.status_code, 401)
        self.assertEqual(patch_response.status_code, 401)
        self.assertEqual(delete_response.status_code, 401)

    def test_bearer_jwt_authenticates_legacy_schedule_api(self):
        self.client.logout()
        response = self.client.post(
            reverse('schedule-event-list'),
            data=json.dumps(
                {
                    'title': 'Bearer-created event',
                    'start_at': '2026-05-20T09:00:00+09:00',
                    'end_at': '2026-05-20T10:00:00+09:00',
                }
            ),
            content_type='application/json',
            HTTP_AUTHORIZATION=self._bearer(self.user),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(ScheduleEvent.objects.get().owner, self.user)
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
        self.assertEqual(payload['raw_data_id'], raw_data.id)

    def test_source_fields_resolve_metadata_raw_data_id_when_fk_is_missing(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/actual',
            title='Actual source notice',
            raw_text='body',
        )
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        ScheduleEvent.objects.create(
            title='Metadata linked event',
            description='description',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            is_all_day=False,
            event_type='notice',
            source_type='notice',
            metadata_json={
                'raw_data_id': raw_data.id,
                'source_url': 'https://example.com/forged',
                'source_title': 'Wrong title',
            },
        )

        response = self.client.get(reverse('schedule-event-list'))

        payload = response.json()[0]
        self.assertEqual(payload['raw_data_id'], raw_data.id)
        self.assertEqual(payload['source_url'], 'https://edu.ssafy.com/notices/actual')
        self.assertEqual(payload['source_title'], 'Actual source notice')

    def test_source_fields_fall_back_to_metadata_when_raw_data_row_is_missing(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 9, 0))
        ScheduleEvent.objects.create(
            title='Stale metadata generated event',
            description='description',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            is_all_day=False,
            event_type='study',
            source_type='notice',
            metadata_json={
                'raw_data_id': 999999,
                'source_url': 'https://edu.ssafy.com/notices/stale',
                'source_title': 'Stale source notice',
            },
        )

        response = self.client.get(
            reverse('schedule-event-list'),
            {'start': '2026-06-10', 'end': '2026-06-10'},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()[0]
        self.assertTrue(payload['is_generated'])
        self.assertEqual(payload['raw_data_id'], 999999)
        self.assertEqual(payload['source_url'], 'https://edu.ssafy.com/notices/stale')
        self.assertEqual(payload['source_title'], 'Stale source notice')

    def test_imported_events_store_audience_metadata_from_raw_data(self):
        run_sample_notice_import()

        event = ScheduleEvent.objects.first()

        self.assertIn('audience', event.metadata_json)
        self.assertIn('generation', event.metadata_json['audience'])
        self.assertIn('track', event.metadata_json['audience'])
        self.assertIn('class_number', event.metadata_json['audience'])
        self.assertIn('campus', event.metadata_json['audience'])

    def test_generated_event_metadata_infers_track_from_raw_title(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            title='5\uc6d4 3\uc8fc\ucc28 \ub9c8\uc774\uc2a4\ud130\uace0 \ud2b8\ub799 \uc2dc\uac04\ud45c',
            raw_text='body',
        )
        schedule = SimpleNamespace(
            title='Track timetable',
            start_at=timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0)),
            end_at=timezone.make_aware(timezone.datetime(2026, 5, 20, 10, 0)),
            event_type='study',
            metadata_json={},
        )

        metadata = build_generated_event_metadata(raw_data, schedule)

        self.assertEqual(metadata['track_key'], 'meister')
        self.assertEqual(metadata['track'], 'meister')
        self.assertEqual(metadata['audience']['track_key'], 'meister')
        self.assertEqual(metadata['audience']['track'], 'meister')
        self.assertFalse(metadata['is_common'])

    def test_backfill_schedule_event_tracks_infers_missing_track_metadata(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        event = ScheduleEvent.objects.create(
            title='5\uc6d4 3\uc8fc\ucc28 \ub9c8\uc774\uc2a4\ud130\uace0 \ud2b8\ub799 \uc2dc\uac04\ud45c',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            source_type='notice',
            metadata_json={'audience': {}},
        )

        dry_run_out = StringIO()
        call_command('backfill_schedule_event_tracks', stdout=dry_run_out)
        event.refresh_from_db()
        self.assertNotIn('track_key', event.metadata_json)
        self.assertIn('changed=1', dry_run_out.getvalue())

        call_command('backfill_schedule_event_tracks', '--apply', stdout=StringIO())
        event.refresh_from_db()

        self.assertEqual(event.metadata_json['track_key'], 'meister')
        self.assertEqual(event.metadata_json['audience']['track_key'], 'meister')
        self.assertFalse(event.metadata_json['is_common'])

    def test_backfill_schedule_event_tracks_corrects_generic_track_label(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        event = ScheduleEvent.objects.create(
            title='5\uc6d4 3\uc8fc\ucc28 Embedded Robot \ud2b8\ub799 \uc2dc\uac04\ud45c',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            source_type='notice',
            metadata_json={'audience': {'track': 'SW/AI'}, 'track': 'SW/AI'},
        )

        call_command('backfill_schedule_event_tracks', '--apply', stdout=StringIO())
        event.refresh_from_db()

        self.assertEqual(event.metadata_json['track_key'], 'embedded_robot')
        self.assertEqual(event.metadata_json['track'], 'embedded_robot')
        self.assertEqual(event.metadata_json['audience']['track_key'], 'embedded_robot')
        self.assertFalse(event.metadata_json['is_common'])

    def test_backfill_schedule_event_tracks_marks_whole_schedule_as_common(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 6, 1, 9, 0))
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            title='[\ud559\uc2b5] 15\uae30 1\ud559\uae30 \uc804\uccb4 \uc77c\uc815',
            raw_text='body',
        )
        event = ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='\uad00\ud1b5 \ud504\ub85c\uc81d\ud2b8 \uc9d1\uc911\uae30\uac04',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            source_type='notice',
            metadata_json={'audience': {}},
        )

        call_command('backfill_schedule_event_tracks', '--apply', stdout=StringIO())
        event.refresh_from_db()

        self.assertEqual(event.metadata_json['track_key'], 'all')
        self.assertTrue(event.metadata_json['is_common'])
        self.assertEqual(event.metadata_json['audience']['track_key'], 'all')

    def test_backfill_schedule_event_tracks_preserves_existing_canonical_track(self):
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        event = ScheduleEvent.objects.create(
            title='Java(\uc804\uacf5) \ud2b8\ub799 \uc2dc\uac04\ud45c',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='study',
            source_type='notice',
            metadata_json={'audience': {'track': 'python', 'track_key': 'python'}, 'track_key': 'python'},
        )

        call_command('backfill_schedule_event_tracks', '--apply', stdout=StringIO())
        event.refresh_from_db()

        self.assertEqual(event.metadata_json['track_key'], 'python')

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



# ---------------------------------------------------------------------------
# _is_timetable_source_period_mismatch 버그 수정 테스트 (Fix 2)
# ---------------------------------------------------------------------------
from datetime import date as _date, datetime as _datetime, time as _time
from schedules.services import _is_timetable_source_period_mismatch as _mismatch_fn


class _MockSchedule:
    """_is_timetable_source_period_mismatch 테스트용 경량 목 오브젝트."""
    def __init__(self, event_date):
        from django.utils import timezone
        naive = _datetime.combine(event_date, _time.min)
        self.start_at = timezone.make_aware(naive, timezone.get_current_timezone())
        self.metadata_json = {'parser_type': 'timetable_grid'}


class TimetablePeriodMismatchTests(TestCase):
    """'15기 2월 1주차 시간표' 같은 제목에서 첫 숫자 '15'를 월로 잘못 읽는 버그 수정."""

    def _check(self, source_title, event_date, expect_mismatch):
        result = _mismatch_fn(source_title, _MockSchedule(event_date))
        label = 'mismatch' if expect_mismatch else 'no mismatch'
        self.assertEqual(result, expect_mismatch,
                         f"source={repr(source_title)}, date={event_date} → "
                         f"기대={label}, 실제={result}")

    def test_기수_prefix_포함_제목_2월1주차_2일(self):
        """'[학습] 15기 2월 1주차 시간표' + Feb 2 → no mismatch."""
        self._check('[학습] 15기 2월 1주차 시간표', _date(2026, 2, 2), expect_mismatch=False)

    def test_기수_prefix_포함_제목_2월1주차_3일(self):
        """'[학습] 15기 2월 1주차 시간표' + Feb 3 → no mismatch."""
        self._check('[학습] 15기 2월 1주차 시간표', _date(2026, 2, 3), expect_mismatch=False)

    def test_기수_prefix_포함_제목_2월2주차_9일(self):
        """'[학습] 15기 2월 1주차 시간표' + Feb 9 (2주차) → mismatch."""
        self._check('[학습] 15기 2월 1주차 시간표', _date(2026, 2, 9), expect_mismatch=True)

    def test_기수_없는_제목_2월1주차_4일(self):
        """'마이스터고 2월 1주차 시간표' + Feb 4 → no mismatch."""
        self._check('마이스터고 2월 1주차 시간표', _date(2026, 2, 4), expect_mismatch=False)

    def test_다른_달_이벤트_mismatch(self):
        """'[학습] 15기 2월 1주차 시간표' + Mar 2 → mismatch (월 다름)."""
        self._check('[학습] 15기 2월 1주차 시간표', _date(2026, 3, 2), expect_mismatch=True)

    def test_주차_없는_제목은_검사_안함(self):
        """제목에 주차가 없으면 False (mismatch 판단 불가)."""
        self._check('2월 시간표', _date(2026, 2, 2), expect_mismatch=False)

    def test_월_없는_제목은_검사_안함(self):
        """제목에 월이 없으면 False."""
        self._check('1주차 시간표', _date(2026, 2, 2), expect_mismatch=False)

    def test_timetable_grid가_아닌_parser는_검사_안함(self):
        """parser_type이 timetable_grid가 아니면 항상 False."""
        mock = _MockSchedule(_date(2026, 3, 2))
        mock.metadata_json = {'parser_type': 'text_date'}
        result = _mismatch_fn('[학습] 15기 2월 1주차 시간표', mock)
        self.assertFalse(result)

# ---------------------------------------------------------------------------
# MVP QA 수정 테스트 (토요일 이벤트 범위 / 공휴일 blocking / 커뮤니티 API)
# ---------------------------------------------------------------------------
import json
from datetime import timedelta, date as _date, datetime as _datetime, time as _time

from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse, NoReverseMatch
from django.contrib.auth import get_user_model

from schedules.models import ScheduleEvent
from schedules.views import _event_occurs_in_range
from schedules.services import is_blocking_generated_schedule_warning


# ──────────────────────────────────────────────────────────────────────────────
# 1. _event_occurs_in_range 수정 테스트 (>= → >)
# ──────────────────────────────────────────────────────────────────────────────
def _make_event(start_date, end_date):
    """날짜만 있는 종일 이벤트 스텁 (exclusive-end 규칙)."""
    tz = timezone.get_current_timezone()

    class _Event:
        start_at = timezone.make_aware(_datetime.combine(start_date, _time.min), tz)
        end_at   = timezone.make_aware(_datetime.combine(end_date,   _time.min), tz)
        metadata_json = {}

    return _Event()


def _range_boundary(date_val, is_end=False):
    """날짜 경계값을 aware datetime으로 변환."""
    tz = timezone.get_current_timezone()
    t = _time.max if is_end else _time.min
    return timezone.make_aware(_datetime.combine(date_val, t), tz)


class EventOccursInRangeExclusiveEndTests(TestCase):
    """end_at이 정확히 다음 날 00:00인 종일 이벤트가 다음 날 쿼리에 포함되지 않는지 검증."""

    def test_friday_event_does_not_appear_on_saturday(self):
        """금요일 종일 이벤트(exclusive end=토요일 00:00)는 토요일 쿼리에서 제외."""
        friday = _date(2026, 2, 6)    # 금요일
        saturday = _date(2026, 2, 7)  # 토요일

        event = _make_event(friday, saturday)  # exclusive end = Sat 00:00
        sat_start = _range_boundary(saturday, is_end=False)
        sat_end   = _range_boundary(saturday, is_end=True)

        # 토요일 쿼리에서 금요일 이벤트가 나타나서는 안 됨
        result = _event_occurs_in_range(event, sat_start, sat_end)
        self.assertFalse(result, '금요일 종일 이벤트가 토요일에 표시되면 안 됩니다.')

    def test_friday_event_appears_on_friday(self):
        """금요일 종일 이벤트는 금요일 쿼리에서 반드시 포함."""
        friday   = _date(2026, 2, 6)
        saturday = _date(2026, 2, 7)

        event = _make_event(friday, saturday)
        fri_start = _range_boundary(friday, is_end=False)
        fri_end   = _range_boundary(friday, is_end=True)

        result = _event_occurs_in_range(event, fri_start, fri_end)
        self.assertTrue(result, '금요일 종일 이벤트가 금요일에 표시되어야 합니다.')

    def test_multi_day_event_appears_across_range(self):
        """실제 다일 이벤트는 기간 전체에 표시."""
        mon = _date(2026, 6, 1)   # 월
        fri = _date(2026, 6, 5)   # 금

        event = _make_event(mon, fri)  # 월~금 5일
        # 수요일 쿼리
        wed_start = _range_boundary(_date(2026, 6, 3), is_end=False)
        wed_end   = _range_boundary(_date(2026, 6, 3), is_end=True)

        result = _event_occurs_in_range(event, wed_start, wed_end)
        self.assertTrue(result, '다일 이벤트가 중간 날짜에도 표시되어야 합니다.')

    def test_all_day_event_end_equals_next_day_not_shown_next_day(self):
        """종일 이벤트 end_at=D+1 00:00 일 때 D+1에 표시되지 않음."""
        d     = _date(2026, 3, 5)
        d_p1  = _date(2026, 3, 6)

        event = _make_event(d, d_p1)
        d_p1_start = _range_boundary(d_p1, is_end=False)
        d_p1_end   = _range_boundary(d_p1, is_end=True)

        self.assertFalse(_event_occurs_in_range(event, d_p1_start, d_p1_end),
                         '종일 이벤트가 다음 날에 표시되면 안 됩니다.')

    def test_saturday_national_holiday_appears_on_saturday(self):
        """토요일 공식 공휴일(start=토, end=일 00:00)은 토요일 쿼리에 포함."""
        saturday = _date(2026, 6, 6)   # 현충일 (토요일)
        sunday   = _date(2026, 6, 7)

        event = _make_event(saturday, sunday)
        sat_start = _range_boundary(saturday, is_end=False)
        sat_end   = _range_boundary(saturday, is_end=True)

        self.assertTrue(_event_occurs_in_range(event, sat_start, sat_end),
                        '토요일 공휴일은 토요일에 표시되어야 합니다.')


# ──────────────────────────────────────────────────────────────────────────────
# 2. generated_class_on_korean_holiday → blocking 경고 테스트
# ──────────────────────────────────────────────────────────────────────────────
class HolidayBlockingWarningTests(TestCase):
    """generated_class_on_korean_holiday 경고가 이제 blocking임을 검증."""

    def test_generated_class_on_korean_holiday_is_blocking(self):
        self.assertTrue(
            is_blocking_generated_schedule_warning('generated_class_on_korean_holiday'),
            'generated_class_on_korean_holiday은 blocking 경고여야 합니다.',
        )

    def test_existing_blocking_warnings_unchanged(self):
        for w in ('timetable_title_equals_source_title', 'non_positive_duration', 'source_title_period_mismatch'):
            with self.subTest(warning=w):
                self.assertTrue(is_blocking_generated_schedule_warning(w))

    def test_non_blocking_warning_unchanged(self):
        self.assertFalse(is_blocking_generated_schedule_warning('unknown_warning'))


# ──────────────────────────────────────────────────────────────────────────────
# 3. 캘린더 API 토요일 누출 통합 테스트
# ──────────────────────────────────────────────────────────────────────────────
@override_settings(SECURE_SSL_REDIRECT=False)
class SaturdayLeakIntegrationTests(TestCase):
    """금요일 종일 이벤트가 토요일 쿼리에서 반환되지 않는지 API 레벨에서 검증."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='sat-test', email='sat@example.com', password='pw'
        )
        self.client.force_login(self.user)

        tz = timezone.get_current_timezone()
        friday   = _date(2026, 2, 6)
        saturday = _date(2026, 2, 7)

        self.fri_event = ScheduleEvent.objects.create(
            title='금요일 수업',
            start_at=timezone.make_aware(_datetime.combine(friday, _time.min), tz),
            end_at=timezone.make_aware(_datetime.combine(saturday, _time.min), tz),
            is_all_day=True,
            event_type='notice',
            source_type='notice',
            metadata_json={'is_timetable': True},
        )

    def test_friday_event_not_in_saturday_api_response(self):
        """API GET ?start=2026-02-07&end=2026-02-07 에서 금요일 이벤트 미포함."""
        resp = self.client.get(
            reverse('schedule-event-list'),
            {'start': '2026-02-07', 'end': '2026-02-07'},
        )
        self.assertEqual(resp.status_code, 200)
        ids = [e['id'] for e in resp.json()]
        self.assertNotIn(self.fri_event.id, ids,
                         '금요일 이벤트가 토요일 API 응답에 포함되면 안 됩니다.')

    def test_friday_event_in_friday_api_response(self):
        """API GET ?start=2026-02-06&end=2026-02-06 에서 금요일 이벤트 포함."""
        resp = self.client.get(
            reverse('schedule-event-list'),
            {'start': '2026-02-06', 'end': '2026-02-06'},
        )
        self.assertEqual(resp.status_code, 200)
        ids = [e['id'] for e in resp.json()]
        self.assertIn(self.fri_event.id, ids,
                      '금요일 이벤트가 금요일 API 응답에 포함되어야 합니다.')

    def test_saturday_national_holiday_still_in_saturday_api_response(self):
        """토요일 공식 공휴일은 토요일 API에서 반환."""
        tz = timezone.get_current_timezone()
        saturday = _date(2026, 6, 6)
        sunday   = _date(2026, 6, 7)
        hol = ScheduleEvent.objects.create(
            title='현충일',
            start_at=timezone.make_aware(_datetime.combine(saturday, _time.min), tz),
            end_at=timezone.make_aware(_datetime.combine(sunday, _time.min), tz),
            is_all_day=True,
            event_type='holiday',
            source_type='national_holiday',
        )
        resp = self.client.get(
            reverse('schedule-event-list'),
            {'start': '2026-06-06', 'end': '2026-06-06'},
        )
        self.assertEqual(resp.status_code, 200)
        ids = [e['id'] for e in resp.json()]
        self.assertIn(hol.id, ids, '토요일 공휴일은 토요일 API에 포함되어야 합니다.')


# ──────────────────────────────────────────────────────────────────────────────
# 4. 커뮤니티 URL 등록 확인 (fix/mvp-qa에서 community가 등록됐는지)
# ──────────────────────────────────────────────────────────────────────────────
class CommunityUrlRegistrationTests(TestCase):
    """fix/mvp-qa 브랜치에서 커뮤니티 URL이 등록됐는지 확인."""

    COMMUNITY_LIST_URL = '/api/v1/community/posts/'
    COMMUNITY_DETAIL_URL = '/api/v1/community/posts/999999/'

    def test_community_list_url_resolves(self):
        """커뮤니티 게시글 목록 URL이 URL-conf에 등록돼 있는지 resolve로 확인."""
        from django.urls import resolve, Resolver404
        try:
            resolve(self.COMMUNITY_LIST_URL)
        except Resolver404:
            self.fail(f'{self.COMMUNITY_LIST_URL} 가 URL conf에 등록되지 않았습니다.')

    def test_community_list_http_not_404_from_urlconf(self):
        """GET /api/v1/community/posts/ 가 URL 미등록 404가 아님 (401/403/200 중 하나)."""
        resp = self.client.get(self.COMMUNITY_LIST_URL)
        self.assertIn(
            resp.status_code, [200, 401, 403],
            f'커뮤니티 목록 URL 이 예상치 못한 상태 코드 반환: {resp.status_code}',
        )

    def test_community_named_urls_exist(self):
        """named URL reverse가 가능한지 확인."""
        from django.urls import reverse as _r, NoReverseMatch
        for name in ('community-post-list', 'community-comment-list'):
            try:
                if 'comment' in name:
                    _r(name, kwargs={'post_id': 1})
                else:
                    _r(name)
            except NoReverseMatch:
                self.fail(f'named URL {name!r} 가 등록되지 않았습니다.')

    def test_community_url_not_a_registration_404(self):
        """/api/v1/community/posts/ 는 등록된 URL이므로 URLconf 404가 아님."""
        from django.urls import resolve, Resolver404
        try:
            resolve(self.COMMUNITY_LIST_URL)
        except Resolver404:
            self.fail(f'{self.COMMUNITY_LIST_URL} 가 URL conf에 등록되지 않았습니다.')


