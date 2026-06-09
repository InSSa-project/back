from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from schedules.models import ScheduleEvent
from apps.ai.models import AiDocument
from apps.ai.sync_ingestion import SyncRawDataRagIngestionService
from sync.models import RawSsafyData


@override_settings(SECURE_SSL_REDIRECT=False)
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

    def test_notice_list_filters_mentoring_source_type_by_category(self):
        RawSsafyData.objects.create(source_type='mentoring_notice', title='멘토링 안내', raw_text='본문')
        RawSsafyData.objects.create(source_type='notice', title='일반 안내', raw_text='본문')

        response = self.client.get(reverse('notice-list'), {'category': 'mentoring'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 0)
        self.assertEqual(response.json()['results'], [])

    def test_notice_list_treats_academic_rule_as_etc(self):
        RawSsafyData.objects.create(source_type='academic_rule', title='학사 규정', raw_text='본문')
        RawSsafyData.objects.create(source_type='notice', title='월말평가 안내', raw_text='시험')

        response = self.client.get(reverse('notice-list'), {'category': 'etc'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 0)
        self.assertEqual(response.json()['results'], [])

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

    def test_notice_list_filters_study_category_by_track(self):
        RawSsafyData.objects.create(source_type='notice', title='Python 보충 학습', raw_text='파이썬')
        RawSsafyData.objects.create(source_type='notice', title='Java 보충 학습', raw_text='자바')
        RawSsafyData.objects.create(source_type='notice', title='공통 보충 학습', raw_text='전체 공통')

        response = self.client.get(reverse('notice-list'), {'category': 'study', 'track': 'python'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 2)
        self.assertEqual({item['track'] for item in response.json()['results']}, {'python', 'common'})

    def test_notice_list_ignores_track_for_mentoring_category(self):
        RawSsafyData.objects.create(
            source_type='mentoring_notice',
            title='멘토링 안내',
            raw_text='본문',
            metadata_json={'track': 'java'},
        )

        response = self.client.get(reverse('notice-list'), {'category': 'mentoring', 'track': 'python'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 0)
        self.assertEqual(response.json()['results'], [])

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
