from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.ai.models import AiDocument
from apps.ai.sync_ingestion import SyncRawDataRagIngestionService
from apps.users.models import UserProfile
from schedules.models import ScheduleEvent
from sync.models import RawSsafyData, UserNoticeReadStatus


@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['testserver'])
class NoticeApiTests(TestCase):
    def test_notice_list_filters_by_source_type(self):
        RawSsafyData.objects.create(source_type='notice', title='Notice', raw_text='Body')
        RawSsafyData.objects.create(source_type='academic_rule', title='Rule', raw_text='Body')

        response = self.client.get(reverse('notice-list'), {'source_type': 'notice'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(response.json()['results'][0]['source_type'], 'notice')

    def test_notice_list_keeps_pagination_shape_and_adds_summary_fields(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='Project notice',
            raw_text='<p>Project submission guide with enough detail for summary fallback.</p>',
            source_url='https://example.com/notices/project',
            metadata_json={'notice_date': '2026-06-10', 'track': 'python'},
        )

        response = self.client.get(reverse('notice-list'), {'page': 1, 'page_size': 10})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn('count', payload)
        self.assertIn('next', payload)
        self.assertIn('previous', payload)
        self.assertIn('results', payload)
        item = payload['results'][0]
        self.assertIn('summary', item)
        self.assertIn('content', item)
        self.assertIn('published_at', item)
        self.assertIn('collected_at', item)
        self.assertEqual(item['notice_date'], '2026-06-10')
        self.assertEqual(item['track_key'], 'python')
        self.assertFalse(item['is_common'])
        self.assertEqual(item['source_url'], 'https://example.com/notices/project')
        self.assertEqual(item['external_url'], 'https://example.com/notices/project')
        self.assertIn('related_schedules', item)

    def test_notice_summary_uses_raw_text_fallback(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='Short title',
            raw_text='<div>Short title</div><p>Important body text for frontend inline summary.</p>',
        )

        response = self.client.get(reverse('notice-list'))

        self.assertEqual(response.status_code, 200)
        summary = response.json()['results'][0]['summary']
        self.assertIn('Important body text', summary)
        self.assertNotIn('<p>', summary)

    def test_notice_summary_endpoint_returns_rule_based_summary(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            title='Summary notice',
            raw_text='Summary notice body with enough details for a frontend summary response.',
            source_url='https://example.com/notices/summary',
        )

        response = self.client.get(reverse('notice-summary', args=[raw_data.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn('summary', payload)
        self.assertEqual(payload['source_title'], 'Summary notice')
        self.assertEqual(payload['source_url'], 'https://example.com/notices/summary')
        self.assertFalse(payload['is_ai_generated'])
        self.assertEqual(payload['summary_type'], 'rule_based')

    def test_notice_read_endpoint_saves_user_status_idempotently(self):
        User = get_user_model()
        user = User.objects.create_user(username='reader-a', email='reader-a@example.com', password='password')
        other_user = User.objects.create_user(username='reader-b', email='reader-b@example.com', password='password')
        raw_data = RawSsafyData.objects.create(source_type='notice', title='Readable notice', raw_text='Body')

        self.client.force_login(user)
        first = self.client.post(reverse('notice-read', args=[raw_data.id]))
        second = self.client.post(reverse('notice-read', args=[raw_data.id]))

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()['notice_id'], raw_data.id)
        self.assertTrue(first.json()['is_read'])
        self.assertEqual(UserNoticeReadStatus.objects.filter(user=user, raw_data=raw_data).count(), 1)
        self.assertFalse(UserNoticeReadStatus.objects.filter(user=other_user, raw_data=raw_data).exists())

    def test_notice_summary_uses_ai_document_when_no_body_text(self):
        raw_data = RawSsafyData.objects.create(source_type='notice', title='AI document notice')
        AiDocument.objects.create(
            sync_raw_data=raw_data,
            title='AI document notice',
            content='Cleaned AI document content should become the summary source.',
            document_type='notice',
        )

        response = self.client.get(reverse('notice-list'))

        self.assertEqual(response.status_code, 200)
        self.assertIn('Cleaned AI document content', response.json()['results'][0]['summary'])

    def test_notice_summary_is_empty_when_only_title_exists(self):
        RawSsafyData.objects.create(source_type='notice', title='Only title', raw_text='Only title')

        response = self.client.get(reverse('notice-list'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'][0]['summary'], '')

    def test_notice_summary_filters_debug_metadata_strings(self):
        debug_text = (
            'Title: Timetable Source type: notice Source URL: '
            'https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=1 '
            'Raw data id: 906 OCR text included: False Raw text: internal only'
        )
        RawSsafyData.objects.create(source_type='notice', title='Timetable', raw_text=debug_text)

        response = self.client.get(reverse('notice-list'))

        self.assertEqual(response.status_code, 200)
        item = response.json()['results'][0]
        blocked = (
            'Source type:',
            'Source URL:',
            'Raw data id:',
            'OCR text included:',
            'Raw text:',
            'https://edu.ssafy.com',
        )
        for field in ('summary', 'content'):
            for pattern in blocked:
                self.assertNotIn(pattern, item[field])

    def test_notice_content_and_body_filter_debug_metadata_strings(self):
        debug_text = (
            'Title: Timetable Source type: notice Source URL: '
            'https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=1 '
            'Raw data id: 906 OCR text included: False Raw text: internal only'
        )
        raw_data = RawSsafyData.objects.create(source_type='notice', title='Timetable', raw_text=debug_text)

        response = self.client.get(reverse('notice-detail', args=[raw_data.id]))

        self.assertEqual(response.status_code, 200)
        item = response.json()
        self.assertEqual(item['content'], '')
        self.assertEqual(item['body'], '')

    def test_notice_published_date_prefers_metadata_over_collected_at(self):
        collected_at = timezone.make_aware(timezone.datetime(2026, 6, 12, 12, 0))
        RawSsafyData.objects.create(
            source_type='notice',
            title='Published date notice',
            raw_text='body',
            collected_at=collected_at,
            metadata_json={'raw_json': {'posted_at': '2026-06-10T09:00:00+09:00'}},
        )

        response = self.client.get(reverse('notice-list'))

        self.assertEqual(response.status_code, 200)
        item = response.json()['results'][0]
        self.assertEqual(item['notice_date'], '2026-06-10')
        self.assertEqual(item['published_at'], '2026-06-10T09:00:00+09:00')
        self.assertNotEqual(item['notice_date'], item['created_at'][:10])

    def test_notice_list_sorts_by_notice_date_before_collected_at(self):
        january_collected = timezone.make_aware(timezone.datetime(2026, 6, 10, 12, 0))
        june_collected = timezone.make_aware(timezone.datetime(2026, 6, 9, 12, 0))
        RawSsafyData.objects.create(
            source_type='notice',
            title='January notice',
            raw_text='old body',
            collected_at=january_collected,
            metadata_json={'notice_date': '2026-01-20'},
        )
        RawSsafyData.objects.create(
            source_type='notice',
            title='June notice',
            raw_text='new body',
            collected_at=june_collected,
            metadata_json={'notice_date': '2026-06-09'},
        )

        response = self.client.get(reverse('notice-list'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'][0]['title'], 'June notice')

    def test_notice_date_is_null_when_only_collected_at_exists(self):
        collected_at = timezone.make_aware(timezone.datetime(2026, 6, 10, 12, 0))
        RawSsafyData.objects.create(
            source_type='notice',
            title='Collected only notice',
            raw_text='body',
            collected_at=collected_at,
        )

        response = self.client.get(reverse('notice-list'))

        self.assertEqual(response.status_code, 200)
        item = response.json()['results'][0]
        self.assertIsNone(item['notice_date'])
        self.assertIsNone(item['published_at'])
        self.assertEqual(item['collected_at'][:10], '2026-06-10')

    def test_notice_common_track_fields_are_canonical(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='Common notice',
            raw_text='common body',
            metadata_json={'track_key': 'all', 'is_common': True},
        )

        response = self.client.get(reverse('notice-list'))

        self.assertEqual(response.status_code, 200)
        item = response.json()['results'][0]
        self.assertEqual(item['track_key'], 'all')
        self.assertTrue(item['is_common'])

    def test_notice_list_filters_by_category(self):
        RawSsafyData.objects.create(source_type='notice', title='Monthly evaluation notice', raw_text='Python exam')
        RawSsafyData.objects.create(source_type='notice', title='Mentoring notice', raw_text='Mentoring')

        response = self.client.get(reverse('notice-list'), {'category': 'evaluation'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(response.json()['results'][0]['category'], 'exam')

    def test_notice_list_filters_mentoring_source_type_by_category(self):
        RawSsafyData.objects.create(source_type='mentoring_notice', title='Mentoring notice', raw_text='Body')
        RawSsafyData.objects.create(source_type='notice', title='General notice', raw_text='Body')

        response = self.client.get(reverse('notice-list'), {'category': 'mentoring'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 0)
        self.assertEqual(response.json()['results'], [])

    def test_notice_list_excludes_mentoring_and_geeknews_sources(self):
        RawSsafyData.objects.create(source_type='notice', title='Official notice', raw_text='Body')
        RawSsafyData.objects.create(source_type='mentoring_notice', title='Mentoring post', raw_text='Body')
        RawSsafyData.objects.create(source_type='geeknews', title='Geeknews post', raw_text='Body')

        response = self.client.get(reverse('notice-list'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(response.json()['results'][0]['source_type'], 'notice')

    def test_notice_list_v1_alias_returns_paginated_official_notices_only(self):
        for index in range(3):
            RawSsafyData.objects.create(source_type='notice', title=f'Official notice {index}', raw_text='Body')
        RawSsafyData.objects.create(source_type='mentoring_notice', title='Mentoring post', raw_text='Body')
        RawSsafyData.objects.create(source_type='geeknews', title='Geeknews post', raw_text='Body')

        response = self.client.get('/api/v1/notices/', {'page': 1, 'page_size': 2})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['count'], 3)
        self.assertEqual(payload['page'], 1)
        self.assertEqual(payload['page_size'], 2)
        self.assertEqual(len(payload['results']), 2)
        self.assertEqual({item['source_type'] for item in payload['results']}, {'notice'})

    def test_notice_list_treats_academic_rule_as_etc(self):
        RawSsafyData.objects.create(source_type='academic_rule', title='Academic rule', raw_text='Body')
        RawSsafyData.objects.create(source_type='notice', title='Monthly evaluation notice', raw_text='Exam')

        response = self.client.get(reverse('notice-list'), {'category': 'other'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 0)
        self.assertEqual(response.json()['results'], [])

    def test_notice_list_filters_by_search(self):
        RawSsafyData.objects.create(source_type='notice', title='Python extra study', raw_text='Material')
        RawSsafyData.objects.create(source_type='notice', title='Java live', raw_text='Material')

        response = self.client.get(reverse('notice-list'), {'search': 'python'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(response.json()['results'][0]['title'], 'Python extra study')

    def test_notice_list_filters_by_track_key_with_common_and_count(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='Java major notice',
            raw_text='java body',
            metadata_json={'track_key': 'java_major'},
        )
        RawSsafyData.objects.create(
            source_type='notice',
            title='Python notice',
            raw_text='python body',
            metadata_json={'track_key': 'python'},
        )
        RawSsafyData.objects.create(
            source_type='notice',
            title='Common notice',
            raw_text='common body',
            metadata_json={'track_key': 'all', 'is_common': True},
        )

        response = self.client.get(reverse('notice-list'), {'track_key': 'java_advanced', 'page': 1, 'page_size': 10})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['count'], 2)
        titles = {item['title'] for item in payload['results']}
        self.assertEqual(titles, {'Java major notice', 'Common notice'})
        self.assertNotIn('Python notice', titles)
        self.assertEqual({item['track_key'] for item in payload['results']}, {'java_major', 'all'})

    def test_notice_list_filters_by_legacy_track_with_common(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='Python extra study',
            raw_text='python',
            metadata_json={'track_key': 'python'},
        )
        RawSsafyData.objects.create(
            source_type='notice',
            title='Common notice',
            raw_text='common all',
            metadata_json={'track_key': 'all', 'is_common': True},
        )
        RawSsafyData.objects.create(
            source_type='notice',
            title='Java notice',
            raw_text='java',
            metadata_json={'track_key': 'java_major'},
        )

        response = self.client.get(reverse('notice-list'), {'track': 'python'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 2)
        self.assertEqual({item['track_key'] for item in response.json()['results']}, {'python', 'all'})
        self.assertEqual({item['is_common'] for item in response.json()['results']}, {False, True})

    def test_notice_list_filters_study_category_by_track(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='Python extra study',
            raw_text='python',
            metadata_json={'track_key': 'python'},
        )
        RawSsafyData.objects.create(
            source_type='notice',
            title='Java extra study',
            raw_text='java',
            metadata_json={'track_key': 'java_major'},
        )
        RawSsafyData.objects.create(
            source_type='notice',
            title='Common extra study',
            raw_text='common all',
            metadata_json={'track_key': 'all', 'is_common': True},
        )

        response = self.client.get(reverse('notice-list'), {'category': 'learning', 'track_key': 'python'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 2)
        self.assertEqual({item['track_key'] for item in response.json()['results']}, {'python', 'all'})

    def test_notice_list_defaults_to_authenticated_user_profile_track(self):
        User = get_user_model()
        user = User.objects.create_user(username='meister', email='meister@example.com', password='password')
        UserProfile.objects.create(user=user, track='Meister')
        RawSsafyData.objects.create(
            source_type='notice',
            title='Meister notice',
            raw_text='body',
            metadata_json={'track_key': 'meister'},
        )
        RawSsafyData.objects.create(
            source_type='notice',
            title='Common notice',
            raw_text='body',
            metadata_json={'track_key': 'all', 'is_common': True},
        )
        RawSsafyData.objects.create(
            source_type='notice',
            title='Python notice',
            raw_text='body',
            metadata_json={'track_key': 'python'},
        )
        self.client.force_login(user)

        response = self.client.get(reverse('notice-list'))

        self.assertEqual(response.status_code, 200)
        titles = {item['title'] for item in response.json()['results']}
        self.assertEqual(titles, {'Meister notice', 'Common notice'})

    def test_notice_list_missing_profile_track_returns_common_only_for_authenticated_user(self):
        User = get_user_model()
        user = User.objects.create_user(username='no-profile', email='no-profile@example.com', password='password')
        RawSsafyData.objects.create(
            source_type='notice',
            title='Common notice',
            raw_text='body',
            metadata_json={'track_key': 'all', 'is_common': True},
        )
        RawSsafyData.objects.create(
            source_type='notice',
            title='Python notice',
            raw_text='body',
            metadata_json={'track_key': 'python'},
        )
        RawSsafyData.objects.create(source_type='notice', title='Unknown track notice', raw_text='Python in title only')
        self.client.force_login(user)

        response = self.client.get(reverse('notice-list'))

        self.assertEqual(response.status_code, 200)
        titles = {item['title'] for item in response.json()['results']}
        # Explicitly-common notices + notices whose title implies no specific track
        # (inferred is_common=True) are both shown.  Track-specific notices are excluded.
        self.assertIn('Common notice', titles)
        self.assertIn('Unknown track notice', titles)
        self.assertNotIn('Python notice', titles)
        track_keys = {item['track_key'] for item in response.json()['results']}
        self.assertNotIn('python', track_keys)

    def test_notice_list_explicit_track_all_returns_all_tracks(self):
        User = get_user_model()
        user = User.objects.create_user(username='track-all', email='track-all@example.com', password='password')
        UserProfile.objects.create(user=user, track='Meister')
        RawSsafyData.objects.create(
            source_type='notice',
            title='Meister notice',
            raw_text='body',
            metadata_json={'track_key': 'meister'},
        )
        RawSsafyData.objects.create(
            source_type='notice',
            title='Python notice',
            raw_text='body',
            metadata_json={'track_key': 'python'},
        )
        self.client.force_login(user)

        response = self.client.get(reverse('notice-list'), {'track': 'all'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual({item['title'] for item in response.json()['results']}, {'Meister notice', 'Python notice'})

    def test_notice_list_explicit_common_track_returns_common_only(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='Common notice',
            raw_text='body',
            metadata_json={'track_key': 'all', 'is_common': True},
        )
        RawSsafyData.objects.create(
            source_type='notice',
            title='Python notice',
            raw_text='body',
            metadata_json={'track_key': 'python'},
        )

        response = self.client.get(reverse('notice-list'), {'track': 'common'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(response.json()['results'][0]['title'], 'Common notice')

    def test_notice_list_ignores_track_for_mentoring_category(self):
        RawSsafyData.objects.create(
            source_type='mentoring_notice',
            title='Mentoring notice',
            raw_text='Body',
            metadata_json={'track': 'java'},
        )

        response = self.client.get(reverse('notice-list'), {'category': 'mentoring', 'track_key': 'python'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 0)
        self.assertEqual(response.json()['results'], [])

    def test_notice_detail_returns_linked_schedule_events(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/notices/1',
            title='Python evaluation',
            raw_text='OCR text',
            metadata_json={'source_id': 'n1'},
        )
        start_at = timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0))
        later_event = ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='Evaluation',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            event_type='exam',
            source_type='notice',
            metadata_json={'audience': {'track': 'python'}, 'raw_data_id': raw_data.id},
        )
        earlier_event = ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='Briefing',
            start_at=start_at - timedelta(hours=1),
            end_at=start_at,
            event_type='study',
            source_type='notice',
            metadata_json={'track_key': 'python'},
        )

        response = self.client.get(reverse('notice-detail', args=[raw_data.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['id'], raw_data.id)
        self.assertEqual(payload['ocr_text'], 'OCR text')
        self.assertEqual([item['id'] for item in payload['linked_events']], [earlier_event.id, later_event.id])
        self.assertEqual([item['id'] for item in payload['schedule_events']], [earlier_event.id, later_event.id])
        self.assertEqual(payload['linked_events'][1]['event_type'], 'exam')
        self.assertEqual(payload['linked_events'][1]['source_url'], raw_data.source_url)
        self.assertEqual(payload['linked_events'][1]['track_key'], 'python')

    def test_notice_detail_returns_images_from_saved_metadata(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/notices/1',
            title='Image notice',
            raw_text='OCR text',
            metadata_json={
                'image_urls': ['/media/notices/1.png', 'https://cdn.example.com/notices/2.png'],
            },
        )

        response = self.client.get(reverse('notice-detail', args=[raw_data.id]))

        self.assertEqual(response.status_code, 200)
        images = response.json()['images']
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0]['url'], 'http://testserver/media/notices/1.png')
        self.assertEqual(images[0]['alt'], 'Image notice')
        self.assertEqual(images[0]['sort_order'], 0)
        self.assertEqual(images[1]['url'], 'https://cdn.example.com/notices/2.png')

    def test_notice_detail_returns_empty_images_array_when_missing(self):
        raw_data = RawSsafyData.objects.create(source_type='notice', title='No image notice', raw_text='body')

        response = self.client.get(reverse('notice-detail', args=[raw_data.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['images'], [])

    def test_notice_list_filters_curriculum_source_type(self):
        RawSsafyData.objects.create(source_type='curriculum', title='Weekly curriculum', raw_text='Python')
        RawSsafyData.objects.create(source_type='notice', title='General notice', raw_text='body')

        response = self.client.get(reverse('notice-list'), {'source_type': 'curriculum'})

        self.assertEqual(response.status_code, 400)

    def test_notice_list_filters_learning_material_source_type(self):
        RawSsafyData.objects.create(source_type='learning_material', title='Learning material', raw_text='Java')
        RawSsafyData.objects.create(source_type='notice', title='General notice', raw_text='body')

        response = self.client.get(reverse('notice-list'), {'source_type': 'learning_material'})

        self.assertEqual(response.status_code, 400)

    def test_notice_list_study_category_includes_source_based_study_types(self):
        RawSsafyData.objects.create(source_type='notice', title='Python study', raw_text='study')
        RawSsafyData.objects.create(source_type='curriculum', title='Weekly curriculum', raw_text='body')
        RawSsafyData.objects.create(source_type='learning_material', title='Learning material', raw_text='body')
        RawSsafyData.objects.create(source_type='academic_rule', title='Rule', raw_text='body')

        response = self.client.get(reverse('notice-list'), {'category': 'study'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(
            {item['source_type'] for item in response.json()['results']},
            {'notice'},
        )

    def test_notice_detail_hides_non_user_visible_source_type(self):
        raw_data = RawSsafyData.objects.create(
            source_type='mentoring_notice',
            title='Mentor story',
            raw_text='body',
            source_url='https://edu.ssafy.com/edu/board/mentoState/detail.do?brdItmSeq=1',
        )

        response = self.client.get(reverse('notice-detail', args=[raw_data.id]))

        self.assertEqual(response.status_code, 404)

    def test_notice_list_source_url_uses_same_row(self):
        first = RawSsafyData.objects.create(
            source_type='notice',
            title='Same notice title',
            raw_text='body',
            source_url='https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=1',
        )
        second = RawSsafyData.objects.create(
            source_type='notice',
            title='Same notice title',
            raw_text='body',
            source_url='https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=2',
        )

        response = self.client.get(reverse('notice-list'), {'page_size': 10})

        self.assertEqual(response.status_code, 200)
        notices_by_id = {item['id']: item for item in response.json()['results']}
        self.assertEqual(notices_by_id[first.id]['source_url'], first.source_url)
        self.assertEqual(notices_by_id[second.id]['source_url'], second.source_url)

    def test_hidden_notice_source_can_still_be_used_for_ai_document(self):
        raw_data = RawSsafyData.objects.create(
            source_type='geeknews',
            title='Geeknews article',
            raw_text='AI reference body',
            source_url='https://example.com/geeknews/1',
        )

        document = SyncRawDataRagIngestionService().upsert_document(raw_data)

        self.assertEqual(document.sync_raw_data_id, raw_data.id)
        self.assertEqual(AiDocument.objects.filter(sync_raw_data=raw_data).count(), 1)


# ---------------------------------------------------------------------------
# Track classification tests
# ---------------------------------------------------------------------------

class TrackKeyFromTextTests(TestCase):
    """Unit tests for sync.services.tracks.track_key_from_text."""

    def _call(self, text):
        from sync.services.tracks import track_key_from_text
        return track_key_from_text(text)

    def test_python_title(self):
        self.assertEqual(self._call('[학습] 6월 4주차 Python 트랙 시간표'), 'python')

    def test_java_major_title_with_space(self):
        self.assertEqual(self._call('[학습] 6월 4주차 Java 전공 트랙 시간표'), 'java_major')

    def test_java_non_major_title_with_space(self):
        self.assertEqual(self._call('[학습] 6월 4주차 Java 비전공 트랙 시간표'), 'java_non_major')

    def test_java_non_major_not_classified_as_java_major(self):
        # "Java 비전공" must never resolve to java_major
        self.assertNotEqual(self._call('[학습] Java 비전공 트랙 시간표'), 'java_major')

    def test_embedded_robot_title(self):
        result = self._call('[학습] 6월 Embedded Robot 트랙 시간표')
        self.assertEqual(result, 'embedded_robot')

    def test_embedded_title(self):
        self.assertEqual(self._call('[학습] 6월 Embedded 트랙 시간표'), 'embedded')

    def test_mobile_title(self):
        self.assertEqual(self._call('[학습] 6월 Mobile 트랙 시간표'), 'mobile')

    def test_data_title(self):
        self.assertEqual(self._call('[학습] 6월 Data 트랙 시간표'), 'data')

    def test_meister_title(self):
        self.assertEqual(self._call('[학습] 6월 Meister 트랙 시간표'), 'meister')

    def test_meister_korean_title(self):
        self.assertEqual(self._call('[학습] 마이스터고 시간표'), 'meister')

    def test_general_notice_returns_empty(self):
        self.assertEqual(self._call('[공지] SSAFY 6기 행사 안내'), '')

    def test_empty_title_returns_empty(self):
        self.assertEqual(self._call(''), '')

    def test_none_returns_empty(self):
        self.assertEqual(self._call(None), '')


class ClassifyNoticeTrackTests(TestCase):
    """Unit tests for sync.services.tracks.classify_notice_track."""

    def _call(self, title, metadata=None):
        from sync.services.tracks import classify_notice_track
        return classify_notice_track(title, metadata)

    def test_explicit_is_common_wins(self):
        result = self._call('[학습] Python 시간표', {'is_common': True})
        self.assertEqual(result['track_key'], 'all')
        self.assertTrue(result['is_common'])

    def test_explicit_track_key_wins_over_title(self):
        result = self._call('[학습] Python 시간표', {'track_key': 'meister'})
        self.assertEqual(result['track_key'], 'meister')
        self.assertFalse(result['is_common'])

    def test_title_inference_python(self):
        result = self._call('[학습] 6월 4주차 Python 트랙 시간표')
        self.assertEqual(result['track_key'], 'python')
        self.assertFalse(result['is_common'])

    def test_title_inference_java_non_major(self):
        result = self._call('[학습] Java 비전공 트랙 시간표')
        self.assertEqual(result['track_key'], 'java_non_major')
        self.assertFalse(result['is_common'])

    def test_title_inference_java_major(self):
        result = self._call('[학습] Java 전공 트랙 시간표')
        self.assertEqual(result['track_key'], 'java_major')
        self.assertFalse(result['is_common'])

    def test_title_inference_meister(self):
        result = self._call('[학습] Meister 트랙 시간표')
        self.assertEqual(result['track_key'], 'meister')
        self.assertFalse(result['is_common'])

    def test_general_notice_becomes_common(self):
        result = self._call('[공지] 수료식 안내')
        self.assertEqual(result['track_key'], 'all')
        self.assertTrue(result['is_common'])

    def test_empty_title_and_no_metadata_becomes_common(self):
        result = self._call('')
        self.assertTrue(result['is_common'])

    def test_audience_track_respected(self):
        result = self._call('[공지] 안내', {'audience': {'track': 'data'}})
        self.assertEqual(result['track_key'], 'data')
        self.assertFalse(result['is_common'])


# ---------------------------------------------------------------------------
# Track filtering tests (via the notice list API)
# ---------------------------------------------------------------------------

class NoticeTrackFilterTests(TestCase):
    """Integration tests for track-aware notice list filtering."""

    def setUp(self):
        self.python_notice = RawSsafyData.objects.create(
            source_type='notice',
            title='Python 시간표',
            raw_text='body',
            metadata_json={'track_key': 'python'},
        )
        self.java_major_notice = RawSsafyData.objects.create(
            source_type='notice',
            title='Java 전공 시간표',
            raw_text='body',
            metadata_json={'track_key': 'java_major'},
        )
        self.meister_notice = RawSsafyData.objects.create(
            source_type='notice',
            title='마이스터고 시간표',
            raw_text='body',
            metadata_json={'track_key': 'meister'},
        )
        self.common_notice = RawSsafyData.objects.create(
            source_type='notice',
            title='전체 공지',
            raw_text='body',
            metadata_json={'track_key': 'all', 'is_common': True},
        )

    def test_meister_user_sees_meister_and_common_only(self):
        User = get_user_model()
        user = User.objects.create_user(username='m1', email='m1@test.com', password='pw')
        UserProfile.objects.create(user=user, track='Meister')
        self.client.force_login(user)

        response = self.client.get(reverse('notice-list'))

        self.assertEqual(response.status_code, 200)
        titles = {item['title'] for item in response.json()['results']}
        self.assertIn('마이스터고 시간표', titles)
        self.assertIn('전체 공지', titles)
        self.assertNotIn('Python 시간표', titles)
        self.assertNotIn('Java 전공 시간표', titles)

    def test_python_user_sees_python_and_common_only(self):
        User = get_user_model()
        user = User.objects.create_user(username='p1', email='p1@test.com', password='pw')
        UserProfile.objects.create(user=user, track='python')
        self.client.force_login(user)

        response = self.client.get(reverse('notice-list'))

        self.assertEqual(response.status_code, 200)
        titles = {item['title'] for item in response.json()['results']}
        self.assertIn('Python 시간표', titles)
        self.assertIn('전체 공지', titles)
        self.assertNotIn('마이스터고 시간표', titles)
        self.assertNotIn('Java 전공 시간표', titles)

    def test_meister_user_does_not_see_python_timetable(self):
        User = get_user_model()
        user = User.objects.create_user(username='m2', email='m2@test.com', password='pw')
        UserProfile.objects.create(user=user, track='Meister')
        self.client.force_login(user)

        response = self.client.get(reverse('notice-list'))

        python_ids = [item['id'] for item in response.json()['results'] if item['track_key'] == 'python']
        self.assertEqual(python_ids, [])

    def test_explicit_track_all_returns_all_tracks(self):
        response = self.client.get(reverse('notice-list'), {'track': 'all'})

        self.assertEqual(response.status_code, 200)
        track_keys = {item['track_key'] for item in response.json()['results']}
        self.assertIn('python', track_keys)
        self.assertIn('meister', track_keys)
        self.assertIn('java_major', track_keys)
        self.assertIn('all', track_keys)

    def test_unauthenticated_user_sees_all_notices(self):
        response = self.client.get(reverse('notice-list'))

        self.assertEqual(response.status_code, 200)
        # Anonymous users have no track filter applied
        self.assertGreaterEqual(response.json()['count'], 4)

    def test_notice_infers_track_from_title_when_no_metadata(self):
        # No track_key in metadata — should be inferred from title
        RawSsafyData.objects.create(
            source_type='notice',
            title='[학습] 6월 4주차 Java 비전공 트랙 시간표',
            raw_text='body',
            metadata_json={},
        )
        response = self.client.get(reverse('notice-list'), {'track': 'java_basic'})

        self.assertEqual(response.status_code, 200)
        inferred = [item for item in response.json()['results'] if '비전공' in item['title']]
        self.assertEqual(len(inferred), 1)
        self.assertEqual(inferred[0]['track_key'], 'java_non_major')
        self.assertFalse(inferred[0]['is_common'])

    def test_notice_infers_meister_from_title_for_meister_user(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='[학습] Meister 트랙 시간표',
            raw_text='body',
            metadata_json={},
        )
        User = get_user_model()
        user = User.objects.create_user(username='m3', email='m3@test.com', password='pw')
        UserProfile.objects.create(user=user, track='Meister')
        self.client.force_login(user)

        response = self.client.get(reverse('notice-list'))

        meister_items = [item for item in response.json()['results'] if 'Meister 트랙' in item['title']]
        self.assertEqual(len(meister_items), 1)
        self.assertEqual(meister_items[0]['track_key'], 'meister')

    def test_general_notice_without_metadata_shown_as_common(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='[공지] 일반 운영 안내',
            raw_text='body',
            metadata_json={},
        )
        User = get_user_model()
        user = User.objects.create_user(username='p2', email='p2@test.com', password='pw')
        UserProfile.objects.create(user=user, track='python')
        self.client.force_login(user)

        response = self.client.get(reverse('notice-list'))

        general_items = [item for item in response.json()['results'] if '일반 운영 안내' in item['title']]
        self.assertEqual(len(general_items), 1, 'General notice must be visible to all users')
        self.assertTrue(general_items[0]['is_common'])


# ---------------------------------------------------------------------------
# Image serialisation tests
# ---------------------------------------------------------------------------

class NoticeImageSerializerTests(TestCase):
    """Verify that the notice API never embeds image binaries or triggers HTTP requests."""

    def test_images_field_contains_only_urls_and_metadata(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            title='Image notice',
            raw_text='body',
            metadata_json={
                'image_urls': [
                    'https://cdn.example.com/notice-1.png',
                    'https://cdn.example.com/notice-2.webp',
                ],
            },
        )

        response = self.client.get(reverse('notice-detail', args=[raw_data.id]))

        self.assertEqual(response.status_code, 200)
        images = response.json()['images']
        self.assertEqual(len(images), 2)
        for image in images:
            self.assertIn('url', image)
            self.assertIn('sort_order', image)
            # No base64 or binary data
            self.assertFalse(str(image.get('url', '')).startswith('data:'))

    def test_list_api_does_not_include_image_binaries(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='List image notice',
            raw_text='body',
            metadata_json={'image_urls': ['https://cdn.example.com/big.png']},
        )

        response = self.client.get(reverse('notice-list'))

        self.assertEqual(response.status_code, 200)
        item = response.json()['results'][0]
        for image in item.get('images', []):
            self.assertFalse(str(image.get('url', '')).startswith('data:'))

    def test_no_images_returns_empty_array(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            title='No image notice',
            raw_text='body',
            metadata_json={},
        )

        response = self.client.get(reverse('notice-detail', args=[raw_data.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['images'], [])

    def test_local_media_url_is_served_as_absolute_url(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            title='Local image notice',
            raw_text='body',
            metadata_json={'image_urls': ['/media/notices/notice-1_opt.webp']},
        )

        response = self.client.get(reverse('notice-detail', args=[raw_data.id]))

        self.assertEqual(response.status_code, 200)
        images = response.json()['images']
        self.assertEqual(len(images), 1)
        self.assertIn('/media/notices/', images[0]['url'])


# ---------------------------------------------------------------------------
# Backfill command tests
# ---------------------------------------------------------------------------

class BackfillNoticeTracksCommandTests(TestCase):
    """Verify the backfill_notice_tracks management command behaviour."""

    def _run_command(self, **kwargs):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('backfill_notice_tracks', stdout=out, **kwargs)
        return out.getvalue()

    def test_dry_run_does_not_modify_db(self):
        notice = RawSsafyData.objects.create(
            source_type='notice',
            title='[학습] Python 트랙 시간표',
            raw_text='body',
            metadata_json={},
        )
        self._run_command(dry_run=True)
        notice.refresh_from_db()
        self.assertNotIn('track_key', notice.metadata_json)

    def test_fills_missing_track_key(self):
        notice = RawSsafyData.objects.create(
            source_type='notice',
            title='[학습] Python 트랙 시간표',
            raw_text='body',
            metadata_json={},
        )
        self._run_command()
        notice.refresh_from_db()
        self.assertEqual(notice.metadata_json.get('track_key'), 'python')
        self.assertFalse(notice.metadata_json.get('is_common'))

    def test_preserves_existing_track_key_without_force(self):
        notice = RawSsafyData.objects.create(
            source_type='notice',
            title='[학습] Python 트랙 시간표',
            raw_text='body',
            metadata_json={'track_key': 'meister'},
        )
        self._run_command()
        notice.refresh_from_db()
        # Existing value must be preserved
        self.assertEqual(notice.metadata_json.get('track_key'), 'meister')

    def test_force_overwrites_existing_track_key(self):
        notice = RawSsafyData.objects.create(
            source_type='notice',
            title='[학습] Python 트랙 시간표',
            raw_text='body',
            metadata_json={'track_key': 'meister'},
        )
        self._run_command(force=True)
        notice.refresh_from_db()
        self.assertEqual(notice.metadata_json.get('track_key'), 'python')

    def test_general_notice_classified_as_common(self):
        notice = RawSsafyData.objects.create(
            source_type='notice',
            title='[공지] 일반 운영 안내',
            raw_text='body',
            metadata_json={},
        )
        self._run_command()
        notice.refresh_from_db()
        self.assertEqual(notice.metadata_json.get('track_key'), 'all')
        self.assertTrue(notice.metadata_json.get('is_common'))

    def test_idempotent_reruns(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='[학습] Python 트랙 시간표',
            raw_text='body',
            metadata_json={},
        )
        self._run_command()
        self._run_command()
        # Should not raise and DB should still have correct value
        count = RawSsafyData.objects.filter(metadata_json__track_key='python').count()
        self.assertEqual(count, 1)
