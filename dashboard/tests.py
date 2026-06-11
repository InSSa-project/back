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

    def test_home_dashboard_returns_200_without_notice_data(self):
        self._event('내일 일정', 1)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['recent_notices'], [])
        notice_card = _action_card(payload, 'notices')
        self.assertEqual(notice_card['count'], 0)
        self.assertEqual(notice_card['value'], '새 공지 없음')

    def test_home_dashboard_focus_and_upcoming_use_rawless_schedule_events(self):
        event = self._event('Raw 없는 일정', 1, event_type='exam')

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(RawSsafyData.objects.count(), 0)
        payload = response.json()
        self.assertEqual(payload['focus']['source_event_id'], event.id)
        self.assertEqual([item['id'] for item in payload['upcoming_schedules']], [event.id])

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

    def test_past_schedule_is_not_selected_as_focus(self):
        self._event('지난 일정', -1)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['focus'])

    def test_upcoming_schedules_are_limited_to_three(self):
        for index in range(5):
            self._event(f'다가오는 일정 {index}', index + 1)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        upcoming = response.json()['upcoming_schedules']
        self.assertEqual(len(upcoming), 3)
        self.assertEqual([item['title'] for item in upcoming], ['다가오는 일정 0', '다가오는 일정 1', '다가오는 일정 2'])
        self.assertIn('deadline_at', upcoming[0])

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

    def test_recent_notices_include_source_url(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='링크 공지',
            raw_text='본문',
            source_url='https://edu.ssafy.com/notices/1',
            collected_at=self.now,
        )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        notice = response.json()['recent_notices'][0]
        self.assertIn('source_url', notice)
        self.assertEqual(notice['source_url'], 'https://edu.ssafy.com/notices/1')

    def test_recent_notice_source_url_falls_back_to_metadata(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='메타 링크 공지',
            raw_text='본문',
            metadata_json={'raw_json': {'link': 'https://edu.ssafy.com/notices/meta'}},
            collected_at=self.now,
        )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()['recent_notices'][0]['source_url'],
            'https://edu.ssafy.com/notices/meta',
        )

    def test_recent_notice_source_url_is_null_when_missing(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='링크 없는 공지',
            raw_text='본문',
            collected_at=self.now,
        )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['recent_notices'][0]['source_url'])

    def test_calendar_card_value_uses_full_calendar_copy(self):
        response = self._get()

        self.assertEqual(response.status_code, 200)
        calendar_card = _action_card(response.json(), 'calendar')
        self.assertEqual(calendar_card['value'], '전체 일정 한눈에 보기')

    def test_notice_count_uses_recent_notice_count_for_mvp(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='새 공지',
            raw_text='본문',
            collected_at=self.now - timedelta(days=1),
        )
        RawSsafyData.objects.create(
            source_type='notice',
            title='오래된 공지',
            raw_text='본문',
            collected_at=self.now - timedelta(days=8),
        )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['highlights'][1]['value'], '1개')
        notice_card = _action_card(payload, 'notices')
        self.assertEqual(notice_card['count'], 1)
        self.assertEqual(notice_card['value'], '확인 안 한 공지 1개')

    def test_notice_count_prefers_notice_date_over_collected_at(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='실제 최근 공지',
            raw_text='본문',
            collected_at=self.now - timedelta(days=30),
            metadata_json={'notice_date': '2026-06-07'},
        )
        RawSsafyData.objects.create(
            source_type='notice',
            title='수집만 최근인 오래된 공지',
            raw_text='본문',
            collected_at=self.now,
            metadata_json={'notice_date': '2026-01-10'},
        )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['new_notice_count'], 1)
        self.assertEqual(payload['unread_new_notice_count'], 1)
        self.assertEqual(payload['unread_notice_count_basis'], 'recent_7_days')

    def test_upcoming_schedules_are_limited_to_this_week(self):
        this_week = self._event('이번 주 일정', 2)
        self._event('다음 주 일정', 7)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([item['id'] for item in payload['upcoming_schedules']], [this_week.id])
        self.assertEqual([item['id'] for item in payload['week_schedules']], [this_week.id])
        self.assertEqual(payload['period_label'], 'this_week')

    def test_risk_recommendation_count_is_returned(self):
        self._event('알고리즘 평가', 1, event_type='exam')
        self._event('일반 안내', 1, event_type='notice')
        self._event('먼 평가', 5, event_type='exam')

        response = self._get()

        self.assertEqual(response.status_code, 200)
        risk_card = _action_card(response.json(), 'risk')
        self.assertEqual(risk_card['count'], 3)
        self.assertEqual(risk_card['value'], '오늘 확인할 추천 3개')

    def test_risk_card_count_matches_risk_recommended_schedules_limit(self):
        for index, days in enumerate([1, 2, 3, 11, 11], start=1):
            self._event(f'온라인 워크 {index}', days, event_type='notice')

        response = self._get()

        self.assertEqual(response.status_code, 200)
        risk_card = _action_card(response.json(), 'risk')
        self.assertEqual(risk_card['count'], 5)
        self.assertEqual(risk_card['value'], '오늘 확인할 추천 5개')

    def test_risk_card_returns_empty_state_without_recommendations(self):
        response = self._get()

        self.assertEqual(response.status_code, 200)
        risk_card = _action_card(response.json(), 'risk')
        self.assertEqual(risk_card['count'], 0)
        self.assertEqual(risk_card['value'], '추천 항목 없음')

    def test_recent_notice_source_url_fallback_only_needs_recent_three(self):
        for index in range(5):
            RawSsafyData.objects.create(
                source_type='notice',
                title=f'공지 {index}',
                raw_text='본문',
                metadata_json={'raw_json': {'link': f'https://edu.ssafy.com/notices/{index}'}},
                collected_at=self.now - timedelta(days=index),
            )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        notices = response.json()['recent_notices']
        self.assertEqual(len(notices), 3)
        self.assertEqual(
            [notice['source_url'] for notice in notices],
            [
                'https://edu.ssafy.com/notices/0',
                'https://edu.ssafy.com/notices/1',
                'https://edu.ssafy.com/notices/2',
            ],
        )

    def test_recent_notice_title_and_url_stay_matched_by_same_row(self):
        rows = []
        for index, title in enumerate(['A 공지', 'B 공지', 'C 공지']):
            rows.append(
                RawSsafyData.objects.create(
                    source_type='notice',
                    title=title,
                    raw_text='본문',
                    source_url=f'https://edu.ssafy.com/notices/{title[0].lower()}',
                    collected_at=self.now + timedelta(minutes=index),
                )
            )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        notices_by_id = {notice['id']: notice for notice in response.json()['recent_notices']}
        for row in rows:
            notice = notices_by_id[row.id]
            self.assertEqual(notice['title'], row.title)
            self.assertEqual(notice['source_url'], row.source_url)

    def test_recent_notice_keeps_duplicate_titles_as_separate_rows(self):
        first = RawSsafyData.objects.create(
            source_type='notice',
            title='멘토 스토리 상세',
            raw_text='본문',
            source_url='https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=1',
            collected_at=self.now,
        )
        second = RawSsafyData.objects.create(
            source_type='notice',
            title='멘토 스토리 상세',
            raw_text='본문',
            source_url='https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=2',
            collected_at=self.now + timedelta(minutes=1),
        )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        notices_by_id = {notice['id']: notice for notice in response.json()['recent_notices']}
        self.assertEqual(notices_by_id[first.id]['source_url'], first.source_url)
        self.assertEqual(notices_by_id[second.id]['source_url'], second.source_url)

    def test_recent_notice_invalid_source_url_is_null(self):
        for value in ['', '#', 'javascript:void(0)']:
            with self.subTest(value=value):
                RawSsafyData.objects.all().delete()
                RawSsafyData.objects.create(
                    source_type='notice',
                    title='잘못된 링크 공지',
                    raw_text='본문',
                    source_url=value,
                    collected_at=self.now,
                )

                response = self._get()

                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.json()['recent_notices'][0]['source_url'])

    def test_recent_notice_uses_metadata_title_for_generic_page_title(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='게시물 목록',
            raw_text='본문',
            source_url='https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=10',
            metadata_json={'notice_title': '실제 공지 제목'},
            collected_at=self.now,
        )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        notice = response.json()['recent_notices'][0]
        self.assertEqual(notice['title'], '실제 공지 제목')
        self.assertEqual(notice['source_url'], 'https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=10')

    def test_recent_notice_prefers_detail_url_over_list_url_in_same_row(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='목록 링크 포함 공지',
            raw_text='본문',
            source_url='https://edu.ssafy.com/edu/board/notice/list.do',
            metadata_json={'detail_url': 'https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=20'},
            collected_at=self.now,
        )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()['recent_notices'][0]['source_url'],
            'https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=20',
        )

    def test_recent_notices_exclude_mentoring_story_rows(self):
        RawSsafyData.objects.create(
            source_type='mentoring_notice',
            title='멘토 스토리 상세',
            raw_text='멘토 스토리 | 멘토칼럼 | Geeknews 를 소개합니다. | 에드윈 | 2023.03.07',
            source_url='https://edu.ssafy.com/edu/board/mentoState/detail.do?brdItmSeq=58923',
            collected_at=self.now,
        )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['recent_notices'], [])

    def test_recent_notices_exclude_academic_rule_list_rows(self):
        RawSsafyData.objects.create(
            source_type='academic_rule',
            title='게시물 목록',
            raw_text='학사규정 | 등록된 학사규정이 없습니다.',
            source_url='https://edu.ssafy.com/edu/board/rule/list.do',
            collected_at=self.now + timedelta(minutes=1),
        )
        notice = RawSsafyData.objects.create(
            source_type='notice',
            title='실제 공지',
            raw_text='본문',
            source_url='https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=30',
            collected_at=self.now,
        )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        notices = response.json()['recent_notices']
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['id'], notice.id)

    def test_today_schedule_count_includes_spanning_event(self):
        ScheduleEvent.objects.create(
            title='오늘 걸친 일정',
            start_at=self.now - timedelta(days=1),
            end_at=self.now + timedelta(days=1),
            event_type='notice',
            source_type='notice',
        )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['highlights'][0]['value'], '1개')


def _action_card(payload, key):
    return next(card for card in payload['action_cards'] if card['key'] == key)
