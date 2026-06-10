from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.ai.models import AiDocument
from apps.ai.sync_ingestion import SyncRawDataRagIngestionService
from schedules.models import ScheduleEvent
from sync.models import RawSsafyData


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
        self.assertEqual(response.json()['results'][0]['track_key'], 'python')

    def test_notice_list_filters_by_track_key_with_common_and_count(self):
        RawSsafyData.objects.create(source_type='notice', title='Java major notice', raw_text='java body')
        RawSsafyData.objects.create(source_type='notice', title='Python notice', raw_text='python body')
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
        RawSsafyData.objects.create(source_type='notice', title='Python extra study', raw_text='python')
        RawSsafyData.objects.create(source_type='notice', title='Common notice', raw_text='common all')
        RawSsafyData.objects.create(source_type='notice', title='Java notice', raw_text='java')

        response = self.client.get(reverse('notice-list'), {'track': 'python'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 2)
        self.assertEqual({item['track_key'] for item in response.json()['results']}, {'python', 'all'})
        self.assertEqual({item['is_common'] for item in response.json()['results']}, {False, True})

    def test_notice_list_filters_study_category_by_track(self):
        RawSsafyData.objects.create(source_type='notice', title='Python extra study', raw_text='python')
        RawSsafyData.objects.create(source_type='notice', title='Java extra study', raw_text='java')
        RawSsafyData.objects.create(source_type='notice', title='Common extra study', raw_text='common all')

        response = self.client.get(reverse('notice-list'), {'category': 'learning', 'track_key': 'python'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 2)
        self.assertEqual({item['track_key'] for item in response.json()['results']}, {'python', 'all'})

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
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='Evaluation',
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
