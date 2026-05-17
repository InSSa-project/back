import json
import tempfile
import types
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from schedules.models import ScheduleEvent
from sync.models import CrawlJobLog, RawSsafyData
from sync.services.import_service import run_notice_import, run_sample_notice_import
from sync.services.ocr_service import extract_text_from_image_urls
from sync.services.reparse_service import reparse_raw_data_to_events
from sync.services.schedule_parser import parse_schedule_candidates, parse_schedule_candidates_with_debug
from bs4 import BeautifulSoup

from sync.services.ssafy_crawler import (
    SsafyCrawlerError,
    extract_image_urls_from_html,
    _extract_notice_links,
    _parse_detail_soup,
    _login_ssafy,
    _collect_authenticated_list,
    load_ssafy_authenticated_documents,
)


class SampleNoticeImportTests(TestCase):
    def test_run_crawl_api_imports_raw_data_and_schedule_events(self):
        response = self.client.post(reverse('sync-crawl-run'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], CrawlJobLog.STATUS_SUCCESS)
        self.assertEqual(payload['raw_count'], 3)
        self.assertEqual(payload['event_count'], 3)
        self.assertEqual(RawSsafyData.objects.count(), 3)
        self.assertEqual(ScheduleEvent.objects.count(), 3)

    def test_run_crawl_api_accepts_sample_mode(self):
        response = self.client.post(
            reverse('sync-crawl-run'),
            data=json.dumps({'mode': 'sample'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], CrawlJobLog.STATUS_SUCCESS)
        self.assertEqual(payload['raw_count'], 3)
        self.assertEqual(ScheduleEvent.objects.count(), 3)

    def test_invalid_mode_returns_error(self):
        response = self.client.post(
            reverse('sync-crawl-run'),
            data=json.dumps({'mode': 'unknown'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], 'error')
        self.assertEqual(payload['raw_count'], 0)
        self.assertEqual(payload['event_count'], 0)
        self.assertEqual(payload['failed_count'], 1)

    def test_duplicate_import_does_not_create_unbounded_events(self):
        first_log = run_sample_notice_import()
        second_log = run_sample_notice_import()

        self.assertEqual(first_log.event_count, 3)
        self.assertEqual(second_log.event_count, 0)
        self.assertEqual(second_log.skipped_count, 3)
        self.assertEqual(RawSsafyData.objects.count(), 3)
        self.assertEqual(ScheduleEvent.objects.count(), 3)

    def test_sample_mode_can_run_repeatedly_without_duplicate_schedule_events(self):
        first_log = run_notice_import(mode='sample')
        second_log = run_notice_import(mode='sample')
        third_log = run_notice_import(mode='sample')

        self.assertEqual(first_log.event_count, 3)
        self.assertEqual(second_log.event_count, 0)
        self.assertEqual(third_log.event_count, 0)
        self.assertEqual(second_log.skipped_count, 3)
        self.assertEqual(third_log.skipped_count, 3)
        self.assertEqual(ScheduleEvent.objects.count(), 3)

    def test_crawler_failure_keeps_existing_events(self):
        run_sample_notice_import()
        original_raw_count = RawSsafyData.objects.count()
        original_event_count = ScheduleEvent.objects.count()

        with patch(
            'sync.services.import_service.load_notices_by_mode',
            side_effect=SsafyCrawlerError('network failed'),
        ):
            response = self.client.post(
                reverse('sync-crawl-run'),
                data=json.dumps({'mode': 'ssafy_notice'}),
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], 'error')
        self.assertEqual(payload['raw_count'], 0)
        self.assertEqual(payload['event_count'], 0)
        self.assertEqual(payload['failed_count'], 1)
        self.assertEqual(RawSsafyData.objects.count(), original_raw_count)
        self.assertEqual(ScheduleEvent.objects.count(), original_event_count)

    def test_preflight_request_allows_local_frontend_origin(self):
        response = self.client.options(
            reverse('sync-crawl-run'),
            HTTP_ORIGIN='http://localhost:5173',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Access-Control-Allow-Origin'], 'http://localhost:5173')
        self.assertIn('POST', response['Access-Control-Allow-Methods'])

    def test_duplicate_notice_is_skipped_by_notice_id(self):
        items = [
            _notice_item('https://example.com/notices/1', 'notice-1'),
            _notice_item('https://example.com/notices/1-copy', 'notice-1'),
        ]

        with patch('sync.services.import_service.load_notices_by_mode', return_value=items):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(job_log.raw_count, 1)
        self.assertEqual(job_log.event_count, 1)
        self.assertEqual(job_log.skipped_count, 1)
        self.assertEqual(RawSsafyData.objects.count(), 1)
        self.assertEqual(ScheduleEvent.objects.count(), 1)

    def test_duplicate_schedule_event_is_skipped_even_when_raw_data_is_new(self):
        first_item = _notice_item('https://example.com/notices/event-1', 'notice-event-1')
        second_item = _notice_item('https://example.com/notices/event-2', 'notice-event-2')
        first_item['title'] = 'Notice A'
        second_item['title'] = 'Notice B'
        first_item['raw_text'] = 'Shared schedule 2026.05.20'
        second_item['raw_text'] = 'Shared schedule 2026.05.20'

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[first_item, second_item]):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(job_log.raw_count, 2)
        self.assertEqual(job_log.event_count, 1)
        self.assertEqual(job_log.skipped_count, 1)
        self.assertIn('event_skipped_count=1', job_log.message)
        self.assertEqual(RawSsafyData.objects.count(), 2)
        self.assertEqual(ScheduleEvent.objects.count(), 1)

    def test_existing_data_is_not_deleted_when_duplicate_is_skipped(self):
        existing = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/notices/2',
            title='Existing notice',
            raw_text='기존 일정 2026.05.20',
            metadata_json={'notice_id': 'notice-2'},
            status=RawSsafyData.STATUS_PARSED,
        )
        ScheduleEvent.objects.create(
            raw_data=existing,
            title='Existing schedule',
            start_at='2026-05-20T00:00:00+09:00',
            end_at='2026-05-21T00:00:00+09:00',
            is_all_day=True,
            event_type='notice',
            source_type='notice',
            source_id=str(existing.pk),
        )

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[_notice_item(existing.source_url, 'notice-2')]):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(job_log.raw_count, 0)
        self.assertEqual(job_log.event_count, 0)
        self.assertEqual(job_log.skipped_count, 1)
        self.assertEqual(RawSsafyData.objects.count(), 1)
        self.assertEqual(ScheduleEvent.objects.count(), 1)

    def test_raw_data_duplicate_skip_does_not_create_duplicate_schedule_event(self):
        first_item = _notice_item('https://example.com/notices/raw-duplicate', 'notice-raw-duplicate')
        second_item = _notice_item('https://example.com/notices/raw-duplicate', 'notice-raw-duplicate-copy')

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[first_item]):
            first_log = run_notice_import(mode='ssafy_notice')
        with patch('sync.services.import_service.load_notices_by_mode', return_value=[second_item]):
            second_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(first_log.event_count, 1)
        self.assertEqual(second_log.event_count, 0)
        self.assertEqual(second_log.skipped_count, 1)
        self.assertEqual(RawSsafyData.objects.count(), 1)
        self.assertEqual(ScheduleEvent.objects.count(), 1)

    def test_schedule_event_api_handles_existing_duplicate_rows(self):
        run_sample_notice_import()
        existing = ScheduleEvent.objects.first()
        ScheduleEvent.objects.create(
            raw_data=existing.raw_data,
            title=existing.title,
            description=existing.description,
            start_at=existing.start_at,
            end_at=existing.end_at,
            is_all_day=existing.is_all_day,
            event_type=existing.event_type,
            source_type=existing.source_type,
            source_id=existing.source_id,
        )

        response = self.client.get(reverse('schedule-event-list'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 4)

    def test_dedupe_schedule_events_command_removes_duplicate_rows(self):
        run_sample_notice_import()
        existing = ScheduleEvent.objects.first()
        ScheduleEvent.objects.create(
            raw_data=existing.raw_data,
            title=existing.title,
            description=existing.description,
            start_at=existing.start_at,
            end_at=existing.end_at,
            is_all_day=existing.is_all_day,
            event_type=existing.event_type,
            source_type=existing.source_type,
            source_id=existing.source_id,
        )
        dry_run_output = StringIO()
        run_output = StringIO()

        call_command('dedupe_schedule_events', '--dry-run', stdout=dry_run_output)
        call_command('dedupe_schedule_events', stdout=run_output)

        self.assertIn('delete_count=1', dry_run_output.getvalue())
        self.assertIn('deleted_count=1', run_output.getvalue())
        self.assertEqual(ScheduleEvent.objects.count(), 3)

    def test_reparse_existing_raw_data_creates_schedule_event(self):
        raw_data = _raw_data('https://example.com/raw/reparse-1', 'Reparse schedule 2026.05.20')

        summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(pk=raw_data.pk))

        self.assertEqual(summary.raw_checked, 1)
        self.assertEqual(summary.candidate_count, 1)
        self.assertEqual(summary.created_count, 1)
        self.assertEqual(summary.skipped_count, 0)
        self.assertEqual(ScheduleEvent.objects.count(), 1)
        self.assertEqual(ScheduleEvent.objects.get().raw_data, raw_data)

    def test_reparse_dry_run_does_not_create_schedule_event(self):
        _raw_data('https://example.com/raw/reparse-dry-run', 'Dry run schedule 2026.05.20')

        summary = reparse_raw_data_to_events(RawSsafyData.objects.all(), dry_run=True)

        self.assertTrue(summary.dry_run)
        self.assertEqual(summary.created_count, 1)
        self.assertEqual(ScheduleEvent.objects.count(), 0)

    def test_reparse_skips_existing_duplicate_schedule_event(self):
        raw_data = _raw_data('https://example.com/raw/reparse-duplicate', 'Duplicate schedule 2026.05.20')

        first_summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(pk=raw_data.pk))
        second_summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(pk=raw_data.pk))

        self.assertEqual(first_summary.created_count, 1)
        self.assertEqual(second_summary.created_count, 0)
        self.assertEqual(second_summary.skipped_count, 1)
        self.assertEqual(ScheduleEvent.objects.count(), 1)

    def test_reparse_source_type_filter_limits_checked_rows(self):
        _raw_data('https://example.com/raw/reparse-notice', 'Notice schedule 2026.05.20')
        _raw_data(
            'https://example.com/raw/reparse-rule',
            'Rule schedule 2026.05.20',
            source_type='academic_rule',
        )

        summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(source_type='notice'))

        self.assertEqual(summary.raw_checked, 1)
        self.assertEqual(summary.created_count, 1)
        self.assertEqual(ScheduleEvent.objects.count(), 1)

    def test_reparse_limit_option_limits_checked_rows(self):
        _raw_data('https://example.com/raw/reparse-limit-1', 'Limit schedule one 2026.05.20')
        _raw_data('https://example.com/raw/reparse-limit-2', 'Limit schedule two 2026.05.21')

        summary = reparse_raw_data_to_events(RawSsafyData.objects.all(), limit=1)

        self.assertEqual(summary.raw_checked, 1)
        self.assertEqual(summary.created_count, 1)
        self.assertEqual(ScheduleEvent.objects.count(), 1)

    def test_reparse_skips_academic_rule_by_default(self):
        _raw_data(
            'https://example.com/raw/reparse-academic-rule',
            'Academic rule schedule 2026.05.20',
            source_type='academic_rule',
        )

        summary = reparse_raw_data_to_events(RawSsafyData.objects.all())

        self.assertEqual(summary.raw_checked, 1)
        self.assertEqual(summary.created_count, 0)
        self.assertEqual(summary.no_schedule_count, 1)
        self.assertEqual(ScheduleEvent.objects.count(), 0)

    def test_reparse_command_outputs_summary(self):
        _raw_data('https://example.com/raw/reparse-command', 'Command schedule 2026.05.20')
        output = StringIO()

        call_command('reparse_raw_ssafy_data', '--dry-run', stdout=output)

        value = output.getvalue()
        self.assertIn('Reparse completed.', value)
        self.assertIn('raw_checked=1', value)
        self.assertIn('created_count=1', value)
        self.assertIn('dry_run=true', value)

    def test_reparse_command_can_target_id_and_replace_existing_events(self):
        raw_data = _raw_data('https://example.com/raw/reparse-replace', '월말평가 2026.05.20')
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='Old generated event',
            start_at=timezone.make_aware(timezone.datetime(2026, 5, 19, 0, 0)),
            end_at=timezone.make_aware(timezone.datetime(2026, 5, 20, 0, 0)),
            is_all_day=True,
            event_type='notice',
            source_type='notice',
            source_id=str(raw_data.pk),
        )
        output = StringIO()

        call_command('reparse_raw_ssafy_data', '--id', raw_data.id, '--replace-events', stdout=output)

        value = output.getvalue()
        self.assertIn('WARNING: --replace-events will delete existing ScheduleEvent rows', value)
        self.assertIn('replaced_event_count=1', value)
        self.assertEqual(ScheduleEvent.objects.count(), 1)
        self.assertEqual(ScheduleEvent.objects.get().title, '월말평가')

    def test_academic_rule_is_saved_without_schedule_event(self):
        items = [
            _notice_item('https://example.com/notices/3', 'notice-3'),
            _academic_rule_item('https://example.com/rules/1', 'rule-1'),
        ]

        with patch('sync.services.import_service.load_notices_by_mode', return_value=items):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(job_log.raw_count, 2)
        self.assertEqual(job_log.event_count, 1)
        self.assertEqual(job_log.skipped_count, 0)
        self.assertEqual(job_log.failed_count, 0)
        self.assertEqual(job_log.no_schedule_count, 1)
        self.assertEqual(job_log.academic_rule_count, 1)
        self.assertEqual(RawSsafyData.objects.filter(source_type='notice').count(), 1)
        self.assertEqual(RawSsafyData.objects.filter(source_type='academic_rule').count(), 1)
        self.assertEqual(ScheduleEvent.objects.count(), 1)

    def test_login_configuration_failure_returns_clear_error(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(SsafyCrawlerError) as error:
                load_ssafy_authenticated_documents()

        self.assertIn('Missing required SSAFY crawler environment variables', str(error.exception))

    def test_login_failure_raises_clear_error(self):
        page = _FailedLoginPage()

        with self.assertRaises(SsafyCrawlerError) as error:
            _login_ssafy(page, 'https://example.com/login', 'admin', 'secret')

        self.assertIn('SSAFY login failed', str(error.exception))

    def test_notice_without_schedule_is_not_failed(self):
        item = _notice_item('https://example.com/notices/no-schedule', 'notice-no-schedule')
        item['raw_text'] = '공지 본문에 일정 날짜가 없습니다.'

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[item]):
            job_log = run_notice_import(mode='ssafy_notice')

        raw_data = RawSsafyData.objects.get()
        self.assertEqual(job_log.raw_count, 1)
        self.assertEqual(job_log.event_count, 0)
        self.assertEqual(job_log.failed_count, 0)
        self.assertEqual(job_log.no_schedule_count, 1)
        self.assertEqual(raw_data.status, RawSsafyData.STATUS_PARSED)

    def test_parser_extracts_korean_date_formats(self):
        cases = [
            '2026.05.20 18:00 제출 마감',
            '2026-05-20 평가',
            '2026년 5월 20일 특강',
            '5월 20일 프로젝트',
            '05/20 18:00까지 제출 마감',
            '~ 2026.05.20 제출 마감',
        ]

        for raw_text in cases:
            with self.subTest(raw_text=raw_text):
                schedules = parse_schedule_candidates(raw_text, default_title='SSAFY 일정')
                self.assertEqual(len(schedules), 1)

    def test_parser_classifies_korean_event_keywords(self):
        self.assertEqual(parse_schedule_candidates('2026.05.20 평가')[0].event_type, 'exam')
        self.assertEqual(parse_schedule_candidates('2026.05.20 제출 마감')[0].event_type, 'assignment')
        self.assertEqual(parse_schedule_candidates('2026.05.20 특강')[0].event_type, 'lecture')
        self.assertEqual(parse_schedule_candidates('2026.05.20 프로젝트')[0].event_type, 'project')

    def test_parser_extracts_calendar_ocr_schedule_candidates(self):
        raw_text = '''
        [OCR_TEXT]
        SAMSUNG
        SW
        AI ACADEMY
        1월
        15기 1학기 진행 일정
        SUN
        MON
        TUE
        WED
        THU
        FRI
        SAT
        1 신정
        2
        3
        4
        5
        6
        7
        8
        9
        10
        15기 SW. AI 스타트캠프
        15기 SW-AI 스타트캠프
        11
        12
        13
        14
        15
        16
        17
        SW 역량테스트
        18
        19
        20
        21
        22
        23
        24
        15기 입학식
        SSAFY DAY
        25
        26
        27
        28
        29
        30
        31
        과목평가1/ 월말평가1
        5월
        1 근로자의 날
        5 어린이날
        6 부처님 오신날
        6월
        3 2026 지방선거
        6 현충일
        29
        30
        월말평가6
        월말평가6
        관통PJT 경진대회
        '''

        schedules = parse_schedule_candidates(raw_text, default_title='[학습] 15기 1학기 전체 일정')
        titles = [schedule.title for schedule in schedules]

        self.assertGreater(len(schedules), 0)
        self.assertIn('월말평가1', titles)
        self.assertIn('월말평가6', titles)
        self.assertIn('15기 입학식', titles)
        self.assertIn('SW 역량테스트', titles)
        self.assertIn('관통PJT 경진대회', titles)
        self.assertTrue(any(schedule.event_type == 'holiday' for schedule in schedules))
        self.assertEqual(titles.count('월말평가6'), 1)

    def test_parser_prefers_ocr_grid_boxes_when_available(self):
        schedules = parse_schedule_candidates(
            '[OCR_TEXT]\n1월\n15\n16\n17\n20\nSW 역량테스트',
            default_title='[학습] 15기 1학기 전체 일정',
            ocr_boxes=_calendar_ocr_boxes(),
        )

        event = next(schedule for schedule in schedules if schedule.title == 'SW 역량테스트')
        self.assertEqual(event.start_at.date().isoformat(), '2026-01-20')
        self.assertEqual(event.event_type, 'exam')

    def test_parser_rejects_mixed_exam_titles_and_records_review_candidates(self):
        schedules, grid_debug = parse_schedule_candidates_with_debug(
            '[OCR_TEXT]\n1월\n20\n배틀싸피과목평가',
            default_title='[학습] 15기 1학기 전체 일정',
            ocr_boxes=_review_required_exam_ocr_boxes(),
        )

        self.assertEqual(schedules, [])
        self.assertEqual(grid_debug.candidate_count, 0)
        self.assertEqual(grid_debug.review_required_candidate_count, 1)
        self.assertEqual(grid_debug.review_required_candidates[0]['title'], '배틀싸피과목평가')
        self.assertEqual(grid_debug.reason, 'review_required_candidates_only')

    def test_parser_failure_source_type_is_recorded_in_message(self):
        with patch('sync.services.import_service.load_notices_by_mode', return_value=[_notice_item('https://example.com/notices/error', 'notice-error')]):
            with patch('sync.services.import_service.parse_schedule_candidates_with_debug', side_effect=ValueError('bad date')):
                job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(job_log.failed_count, 1)
        self.assertIn('failed_items=notice:ValueError', job_log.message)

    def test_notice_link_extractor_skips_list_page_links(self):
        soup = BeautifulSoup(
            '''
            <a href="/edu/board/docReq/list.do">게시물 목록</a>
            <a href="/edu/board/docReq/detail.do?articleId=1">공지 상세</a>
            <a href="#;" onclick="fnDetail('115458');">상세 이동</a>
            ''',
            'html.parser',
        )

        links = _extract_notice_links(soup, 'https://edu.ssafy.com/edu/board/docReq/list.do')

        self.assertEqual(
            links,
            [
                'https://edu.ssafy.com/edu/board/docReq/detail.do?articleId=1',
                'https://edu.ssafy.com/edu/board/docReq/detail.do?brdItmSeq=115458',
            ],
        )

    def test_academic_rule_list_without_detail_links_is_collected_as_document(self):
        page = _StaticPage('<main><h1>학사규정</h1><p>규정 본문</p></main>')

        items = _collect_authenticated_list(
            page=page,
            list_url='https://edu.ssafy.com/edu/board/rule/list.do',
            source_type='academic_rule',
            link_extractor=lambda soup, base_url: [],
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['source_type'], 'academic_rule')
        self.assertEqual(items[0]['source_url'], 'https://edu.ssafy.com/edu/board/rule/list.do')

    def test_extract_image_urls_from_html_normalizes_and_deduplicates_urls(self):
        html = '''
        <article>
            <img src="/upload/notice/a.png">
            <img src="images/b.png">
            <img src="https://cdn.example.com/c.png">
            <img src="/upload/notice/a.png">
            <img src="data:image/png;base64,AAAA">
        </article>
        '''

        image_urls = extract_image_urls_from_html(
            html,
            'https://edu.ssafy.com/edu/board/docReq/detail.do?brdItmSeq=1',
        )

        self.assertEqual(
            image_urls,
            [
                'https://edu.ssafy.com/upload/notice/a.png',
                'https://edu.ssafy.com/edu/board/docReq/images/b.png',
                'https://cdn.example.com/c.png',
            ],
        )

    def test_extract_image_urls_from_html_skips_non_notice_images(self):
        html = '''
        <article>
            <img src="/assets/header-logo.jpg">
            <img src="/assets/menu-icon.png">
            <img src="/assets/top-banner.png">
            <img src="/upload/notice/schedule.png">
        </article>
        '''

        image_urls = extract_image_urls_from_html(
            html,
            'https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=1',
        )

        self.assertEqual(image_urls, ['https://edu.ssafy.com/upload/notice/schedule.png'])

    def test_ocr_mock_result_is_saved_to_metadata(self):
        item = _notice_item('https://example.com/notices/ocr-metadata', 'notice-ocr-metadata')
        item['raw_html'] = '<main><img src="/notice.png">SSAFY ?쇱젙 2026.05.20</main>'

        with patch.dict('os.environ', {'OCR_PROVIDER': 'mock'}):
            with patch('sync.services.import_service.load_notices_by_mode', return_value=[item]):
                job_log = run_notice_import(mode='ssafy_notice')

        raw_data = RawSsafyData.objects.get()
        self.assertEqual(job_log.image_count, 1)
        self.assertEqual(job_log.ocr_processed_count, 1)
        self.assertEqual(job_log.ocr_failed_count, 0)
        self.assertEqual(raw_data.metadata_json['image_urls'], ['https://example.com/notice.png'])
        self.assertEqual(raw_data.metadata_json['ocr_provider'], 'mock')
        self.assertEqual(raw_data.metadata_json['ocr_status'], 'skipped')
        self.assertEqual(raw_data.metadata_json['ocr_text_length'], 0)

    def test_mock_provider_keeps_existing_skipped_behavior(self):
        with patch.dict('os.environ', {'OCR_PROVIDER': 'mock'}):
            result = extract_text_from_image_urls(['https://example.com/notice.png'])

        self.assertEqual(result['ocr_provider'], 'mock')
        self.assertEqual(result['ocr_status'], 'skipped')
        self.assertEqual(result['ocr_text'], '')

    def test_google_vision_provider_extracts_text(self):
        with patch.dict(
            'os.environ',
            {
                'OCR_PROVIDER': 'google_vision',
                'GOOGLE_VISION_ENABLED': 'true',
                'GOOGLE_APPLICATION_CREDENTIALS': 'C:\\fake\\vision.json',
            },
        ):
            with patch.dict('sys.modules', _google_vision_modules('OCR text 2026.05.20')):
                with patch('sync.services.ocr_service.requests.get', return_value=_ImageResponse()):
                    result = extract_text_from_image_urls(['https://example.com/notice.png'])

        self.assertEqual(result['ocr_provider'], 'google_vision')
        self.assertEqual(result['ocr_status'], 'success')
        self.assertEqual(result['ocr_text'], 'OCR text 2026.05.20')

    def test_google_vision_missing_configuration_fails_without_secret_values(self):
        with patch.dict(
            'os.environ',
            {
                'OCR_PROVIDER': 'google_vision',
                'GOOGLE_VISION_ENABLED': 'true',
                'GOOGLE_APPLICATION_CREDENTIALS': '',
            },
        ):
            result = extract_text_from_image_urls(['https://example.com/notice.png'])

        self.assertEqual(result['ocr_provider'], 'google_vision')
        self.assertEqual(result['ocr_status'], 'failed')
        self.assertIn('credentials are not configured', result['ocr_error'])
        self.assertNotIn('GOOGLE_APPLICATION_CREDENTIALS=', result['ocr_error'])

    def test_clova_provider_extracts_infer_text_lines(self):
        response_payload = {
            'images': [
                {
                    'fields': [
                        {'inferText': 'monthly exam'},
                        {'inferText': '2026.05.20'},
                    ]
                }
            ]
        }

        with patch.dict(
            'os.environ',
            {
                'OCR_PROVIDER': 'clova',
                'CLOVA_OCR_INVOKE_URL': 'https://clova.example.com/ocr',
                'CLOVA_OCR_SECRET_KEY': 'super-secret',
            },
        ):
            with patch('sync.services.ocr_service.requests.get', return_value=_ImageResponse()):
                with patch('sync.services.ocr_service.requests.post', return_value=_JsonResponse(response_payload)) as post_mock:
                    result = extract_text_from_image_urls(['https://example.com/notice.png'])

        self.assertEqual(result['ocr_provider'], 'clova')
        self.assertEqual(result['ocr_status'], 'success')
        self.assertEqual(result['ocr_text'], 'monthly exam\n2026.05.20')
        self.assertEqual(result['ocr_failed_count'], 0)
        self.assertEqual(post_mock.call_args.kwargs['headers']['X-OCR-SECRET'], 'super-secret')

    def test_clova_provider_extracts_ocr_boxes(self):
        response_payload = {
            'images': [
                {
                    'fields': [
                        {
                            'inferText': '월말평가',
                            'inferConfidence': 0.98,
                            'boundingPoly': {
                                'vertices': [
                                    {'x': 100, 'y': 200},
                                    {'x': 180, 'y': 200},
                                    {'x': 180, 'y': 220},
                                    {'x': 100, 'y': 220},
                                ]
                            },
                        }
                    ]
                }
            ]
        }

        with patch.dict(
            'os.environ',
            {
                'OCR_PROVIDER': 'clova',
                'CLOVA_OCR_INVOKE_URL': 'https://clova.example.com/ocr',
                'CLOVA_OCR_SECRET_KEY': 'super-secret',
            },
        ):
            with patch('sync.services.ocr_service.requests.get', return_value=_ImageResponse()):
                with patch('sync.services.ocr_service.requests.post', return_value=_JsonResponse(response_payload)):
                    result = extract_text_from_image_urls(['https://example.com/notice.png'])

        self.assertEqual(result['ocr_status'], 'success')
        self.assertEqual(result['ocr_boxes'][0]['text'], '월말평가')
        self.assertEqual(result['ocr_boxes'][0]['x1'], 100.0)
        self.assertEqual(result['ocr_boxes'][0]['y2'], 220.0)
        self.assertEqual(result['ocr_boxes'][0]['confidence'], 0.98)

    def test_clova_missing_configuration_fails_safely(self):
        with patch.dict(
            'os.environ',
            {
                'OCR_PROVIDER': 'clova',
                'CLOVA_OCR_INVOKE_URL': '',
                'CLOVA_OCR_SECRET_KEY': '',
            },
        ):
            result = extract_text_from_image_urls(['https://example.com/notice.png'])

        self.assertEqual(result['ocr_provider'], 'clova')
        self.assertEqual(result['ocr_status'], 'failed')
        self.assertEqual(result['ocr_failed_count'], 1)
        self.assertIn('not configured', result['ocr_error'])
        self.assertNotIn('CLOVA_OCR_SECRET_KEY', result['ocr_error'])

    def test_clova_image_download_failure_does_not_stop_other_images(self):
        response_payload = {
            'images': [
                {
                    'fields': [
                        {'inferText': 'project submission'},
                        {'inferText': '2026.05.24'},
                    ]
                }
            ]
        }

        with patch.dict(
            'os.environ',
            {
                'OCR_PROVIDER': 'clova',
                'CLOVA_OCR_INVOKE_URL': 'https://clova.example.com/ocr',
                'CLOVA_OCR_SECRET_KEY': 'super-secret',
            },
        ):
            with patch(
                'sync.services.ocr_service.requests.get',
                side_effect=[RuntimeError('download down'), _ImageResponse()],
            ):
                with patch('sync.services.ocr_service.requests.post', return_value=_JsonResponse(response_payload)):
                    result = extract_text_from_image_urls(
                        [
                            'https://example.com/broken.png',
                            'https://example.com/schedule.png',
                        ]
                    )

        self.assertEqual(result['ocr_provider'], 'clova')
        self.assertEqual(result['ocr_status'], 'success')
        self.assertEqual(result['ocr_text'], 'project submission\n2026.05.24')
        self.assertEqual(result['ocr_failed_count'], 1)
        self.assertIn('download down', result['ocr_error'])

    def test_clova_error_redacts_secret_key(self):
        with patch.dict(
            'os.environ',
            {
                'OCR_PROVIDER': 'clova',
                'CLOVA_OCR_INVOKE_URL': 'https://clova.example.com/ocr',
                'CLOVA_OCR_SECRET_KEY': 'super-secret',
            },
        ):
            with patch('sync.services.ocr_service.requests.get', return_value=_ImageResponse()):
                with patch(
                    'sync.services.ocr_service.requests.post',
                    side_effect=RuntimeError('bad secret super-secret'),
                ):
                    result = extract_text_from_image_urls(['https://example.com/notice.png'])

        self.assertEqual(result['ocr_provider'], 'clova')
        self.assertEqual(result['ocr_status'], 'failed')
        self.assertIn('[redacted]', result['ocr_error'])
        self.assertNotIn('super-secret', result['ocr_error'])

    def test_ocr_failure_does_not_fail_crawl(self):
        item = _notice_item('https://example.com/notices/ocr-failure', 'notice-ocr-failure')
        item['raw_html'] = '<main><img src="/notice.png">SSAFY ?쇱젙 2026.05.20</main>'

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[item]):
            with patch('sync.services.import_service.extract_text_from_image_urls', side_effect=RuntimeError('ocr down')):
                job_log = run_notice_import(mode='ssafy_notice')

        raw_data = RawSsafyData.objects.get()
        self.assertEqual(job_log.status, CrawlJobLog.STATUS_SUCCESS)
        self.assertEqual(job_log.raw_count, 1)
        self.assertEqual(job_log.event_count, 1)
        self.assertEqual(job_log.failed_count, 0)
        self.assertEqual(job_log.ocr_failed_count, 1)
        self.assertEqual(raw_data.metadata_json['ocr_status'], 'failed')
        self.assertIn('ocr down', raw_data.metadata_json['ocr_error'])

    def test_image_download_failure_does_not_fail_crawl(self):
        item = _notice_item('https://example.com/notices/image-download-failure', 'notice-image-download-failure')
        item['raw_text'] = 'SSAFY ?쇱젙 2026.05.20'
        item['raw_html'] = '<main><img src="/notice.png">SSAFY ?쇱젙 2026.05.20</main>'

        with patch.dict(
            'os.environ',
            {
                'OCR_PROVIDER': 'google_vision',
                'GOOGLE_VISION_ENABLED': 'true',
                'GOOGLE_APPLICATION_CREDENTIALS': 'C:\\fake\\vision.json',
            },
        ):
            with patch.dict('sys.modules', _google_vision_modules('')):
                with patch('sync.services.ocr_service.requests.get', side_effect=RuntimeError('download down')):
                    with patch('sync.services.import_service.load_notices_by_mode', return_value=[item]):
                        job_log = run_notice_import(mode='ssafy_notice')

        raw_data = RawSsafyData.objects.get()
        self.assertEqual(job_log.status, CrawlJobLog.STATUS_SUCCESS)
        self.assertEqual(job_log.failed_count, 0)
        self.assertEqual(job_log.ocr_failed_count, 1)
        self.assertEqual(raw_data.metadata_json['ocr_status'], 'failed')

    def test_ocr_text_is_merged_into_raw_text_before_parsing(self):
        item = _notice_item('https://example.com/notices/ocr-text', 'notice-ocr-text')
        item['raw_text'] = 'SSAFY notice without date'
        item['raw_html'] = '<main><img src="/notice.png">SSAFY notice without date</main>'

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[item]):
            with patch(
                'sync.services.import_service.extract_text_from_image_urls',
                return_value={
                    'ocr_text': 'OCR ?쇱젙 2026.05.20',
                    'ocr_provider': 'mock',
                    'ocr_status': 'success',
                    'ocr_error': '',
                },
            ):
                job_log = run_notice_import(mode='ssafy_notice')

        raw_data = RawSsafyData.objects.get()
        self.assertIn('[OCR_TEXT]', raw_data.raw_text)
        self.assertIn('OCR ?쇱젙 2026.05.20', raw_data.raw_text)
        self.assertEqual(raw_data.metadata_json['ocr_status'], 'success')
        self.assertEqual(raw_data.metadata_json['ocr_text_length'], len('OCR ?쇱젙 2026.05.20'))
        self.assertEqual(job_log.event_count, 1)

    def test_google_vision_ocr_text_can_create_schedule_event(self):
        item = _notice_item('https://example.com/notices/vision-schedule', 'notice-vision-schedule')
        item['raw_text'] = 'SSAFY notice without date'
        item['raw_html'] = '<main><img src="/notice.png">SSAFY notice without date</main>'

        with patch.dict(
            'os.environ',
            {
                'OCR_PROVIDER': 'google_vision',
                'GOOGLE_VISION_ENABLED': 'true',
                'GOOGLE_APPLICATION_CREDENTIALS': 'C:\\fake\\vision.json',
            },
        ):
            with patch.dict('sys.modules', _google_vision_modules('OCR schedule 2026.05.20')):
                with patch('sync.services.ocr_service.requests.get', return_value=_ImageResponse()):
                    with patch('sync.services.import_service.load_notices_by_mode', return_value=[item]):
                        job_log = run_notice_import(mode='ssafy_notice')

        raw_data = RawSsafyData.objects.get()
        self.assertEqual(job_log.event_count, 1)
        self.assertEqual(ScheduleEvent.objects.count(), 1)
        self.assertEqual(raw_data.metadata_json['ocr_provider'], 'google_vision')
        self.assertEqual(raw_data.metadata_json['ocr_status'], 'success')
        self.assertIn('[OCR_TEXT]', raw_data.raw_text)

    def test_link_schedule_events_to_raw_data_dry_run_reports_linkable_rows(self):
        raw_data = _raw_data('https://edu.ssafy.com/notices/link', '월말평가 2026.05.20')
        ScheduleEvent.objects.create(
            title='월말평가',
            start_at=timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0)),
            end_at=timezone.make_aware(timezone.datetime(2026, 5, 20, 10, 0)),
            is_all_day=False,
            event_type='exam',
            source_type='notice',
        )
        output = StringIO()

        call_command('link_schedule_events_to_raw_data', '--dry-run', stdout=output)

        value = output.getvalue()
        self.assertIn('checked_count=1', value)
        self.assertIn('linked_count=1', value)
        self.assertIn(f'raw_data_id={raw_data.id}', value)
        self.assertIsNone(ScheduleEvent.objects.get().raw_data)

    def test_delete_sample_data_dry_run_reports_sample_targets(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://sample.ssafy.local/notices/sample',
            title='5월 월말평가 안내',
            raw_text='월말평가 2026.05.20',
        )
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='월말평가',
            start_at=timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0)),
            end_at=timezone.make_aware(timezone.datetime(2026, 5, 20, 10, 0)),
            is_all_day=False,
            event_type='exam',
            source_type='notice',
        )
        output = StringIO()

        call_command('delete_sample_data', '--dry-run', stdout=output)

        value = output.getvalue()
        self.assertIn('raw_delete_count=1', value)
        self.assertIn('event_delete_count=1', value)
        self.assertEqual(RawSsafyData.objects.count(), 1)
        self.assertEqual(ScheduleEvent.objects.count(), 1)

    def test_delete_sample_data_preserves_edu_ssafy_data(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=1',
            title='실제 공지',
            raw_text='월말평가 2026.05.20',
        )
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='월말평가',
            start_at=timezone.make_aware(timezone.datetime(2026, 5, 20, 9, 0)),
            end_at=timezone.make_aware(timezone.datetime(2026, 5, 20, 10, 0)),
            is_all_day=False,
            event_type='exam',
            source_type='notice',
        )

        call_command('delete_sample_data')

        self.assertEqual(RawSsafyData.objects.count(), 1)
        self.assertEqual(ScheduleEvent.objects.count(), 1)

    def test_preview_parse_raw_data_command_outputs_candidates(self):
        raw_data = _raw_data('https://example.com/raw/preview', '월말평가 2026.05.20')
        output = StringIO()

        call_command('preview_parse_raw_data', '--id', raw_data.id, stdout=output)

        value = output.getvalue()
        self.assertIn('raw_title=', value)
        self.assertIn('candidate_count=1', value)
        self.assertIn('title=월말평가', value)

    def test_preview_parse_raw_data_outputs_ocr_grid_debug(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/raw/preview-grid',
            title='[학습] 15기 1학기 전체 일정',
            raw_text='[OCR_TEXT]\n1월\n20\nSW 역량테스트',
            ocr_boxes=_calendar_ocr_boxes(),
        )
        output = StringIO()

        call_command('preview_parse_raw_data', '--id', raw_data.id, stdout=output)

        value = output.getvalue()
        self.assertIn('ocr_box_count=', value)
        self.assertIn('grid_date_cell_count=', value)
        self.assertIn('grid_candidate_count=1', value)
        self.assertIn('inferred_date=2026-01-20', value)
        self.assertIn('source_box_count=', value)
        self.assertIn('row_index=', value)
        self.assertIn('grid_review_required_candidate_count=0', value)
        self.assertIn('grid_unmatched_texts=', value)

    def test_reparse_records_review_required_exam_candidates_in_metadata(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/raw/review-required',
            title='[학습] 15기 1학기 전체 일정',
            raw_text='[OCR_TEXT]\n1월\n20\n배틀싸피과목평가',
            ocr_boxes=_review_required_exam_ocr_boxes(),
        )

        call_command('reparse_raw_ssafy_data', '--id', raw_data.id, '--replace-events')

        raw_data.refresh_from_db()
        self.assertEqual(ScheduleEvent.objects.count(), 0)
        self.assertEqual(raw_data.metadata_json['review_required_candidate_count'], 1)
        self.assertEqual(raw_data.metadata_json['review_required_candidates'][0]['title'], '배틀싸피과목평가')

    def test_preview_parse_raw_data_exports_grid_debug_json(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/raw/preview-grid-json',
            title='[학습] 15기 1학기 전체 일정',
            raw_text='[OCR_TEXT]\n1월\n20\nSW 역량테스트',
            ocr_boxes=_calendar_ocr_boxes(),
        )
        output = StringIO()

        with tempfile.TemporaryDirectory() as temp_dir:
            export_path = f'{temp_dir}/grid_candidates.json'
            call_command(
                'preview_parse_raw_data',
                '--id',
                raw_data.id,
                '--export-json',
                export_path,
                stdout=output,
            )
            with open(export_path, encoding='utf-8') as export_file:
                payload = json.load(export_file)

        self.assertEqual(payload['raw_id'], raw_data.id)
        self.assertEqual(payload['grid_debug']['candidate_count'], 1)
        self.assertEqual(payload['grid_debug']['candidates'][0]['inferred_date'], '2026-01-20')
        self.assertIn('export_json=', output.getvalue())

    def test_parser_extracts_multiple_ocr_table_schedule_rows(self):
        raw_text = '''
        [학습] 15기 1학기 전체 일정
        입과 및 OT 1월 7일
        1학기 프로젝트 3월 2일 ~ 3월 6일
        월말평가
        5월 20일
        프로젝트 제출 마감 05/24 23:59까지
        '''

        schedules = parse_schedule_candidates(raw_text, default_title='공지사항 상세')

        self.assertGreaterEqual(len(schedules), 4)
        self.assertTrue(all(schedule.title != '공지사항 상세' for schedule in schedules))
        self.assertIn('project', {schedule.event_type for schedule in schedules})

    def test_generic_detail_title_uses_list_title(self):
        soup = BeautifulSoup(
            '<main><h1>공지사항 상세</h1><p>[학습] 15기 1학기 전체 일정</p></main>'
            '<title>공지사항 상세</title>',
            'html.parser',
        )

        item = _parse_detail_soup(
            soup,
            'https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=115458',
            source_type='notice',
            list_title='[학습] 15기 1학기 전체 일정',
        )

        self.assertEqual(item['title'], '[학습] 15기 1학기 전체 일정')

    def test_backfill_raw_ocr_extracts_images_from_raw_html_and_updates_text(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=1',
            title='OCR 대상',
            raw_text='공지 본문',
            raw_html='<main><img src="/upload/schedule.png"></main>',
            metadata_json={},
        )
        output = StringIO()

        with patch(
            'sync.management.commands.backfill_raw_ocr.extract_text_from_image_urls',
            return_value={
                'ocr_text': '월말평가 2026.05.20',
                'ocr_provider': 'google_vision',
                'ocr_status': 'success',
                'ocr_error': '',
                'ocr_failed_count': 0,
            },
        ) as extract_mock:
            call_command('backfill_raw_ocr', '--id', raw_data.id, stdout=output)

        raw_data.refresh_from_db()
        extract_mock.assert_called_once_with(['https://edu.ssafy.com/upload/schedule.png'])
        self.assertIn('[OCR_TEXT]', raw_data.raw_text)
        self.assertIn('월말평가 2026.05.20', raw_data.raw_text)
        self.assertEqual(raw_data.metadata_json['ocr_status'], 'success')
        self.assertEqual(raw_data.metadata_json['ocr_text_length'], len('월말평가 2026.05.20'))
        self.assertIn('updated_count=1', output.getvalue())

    def test_backfill_raw_ocr_force_refills_existing_text_and_boxes(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=force',
            title='OCR force',
            raw_text='공지 본문\n\n[OCR_TEXT]\n기존 OCR',
            raw_html='<main><img src="/upload/schedule.png"></main>',
            metadata_json={'ocr_status': 'success', 'ocr_text_length': 6, 'ocr_box_count': 0},
            ocr_boxes=[],
        )
        output = StringIO()

        with patch(
            'sync.management.commands.backfill_raw_ocr.extract_text_from_image_urls',
            return_value={
                'ocr_text': '월말평가 2026.05.20',
                'ocr_provider': 'google_vision',
                'ocr_status': 'success',
                'ocr_error': '',
                'ocr_failed_count': 0,
                'ocr_boxes': _calendar_ocr_boxes(),
            },
        ):
            call_command('backfill_raw_ocr', '--id', raw_data.id, '--force', stdout=output)

        raw_data.refresh_from_db()
        self.assertEqual(len(raw_data.ocr_boxes), len(_calendar_ocr_boxes()))
        self.assertEqual(raw_data.metadata_json['ocr_box_count'], len(_calendar_ocr_boxes()))
        self.assertIn('월말평가 2026.05.20', raw_data.raw_text)
        self.assertIn('forced_count=1', output.getvalue())

    def test_backfill_raw_ocr_skips_existing_boxes_without_force(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=skip',
            title='OCR skip',
            raw_text='공지 본문\n\n[OCR_TEXT]\n기존 OCR',
            raw_html='<main><img src="/upload/schedule.png"></main>',
            metadata_json={'ocr_status': 'success', 'ocr_text_length': 6, 'ocr_box_count': 1},
            ocr_boxes=[{'text': '기존', 'x1': 1, 'y1': 1, 'x2': 2, 'y2': 2}],
        )
        output = StringIO()

        with patch('sync.management.commands.backfill_raw_ocr.extract_text_from_image_urls') as extract_mock:
            call_command('backfill_raw_ocr', '--id', raw_data.id, stdout=output)

        raw_data.refresh_from_db()
        extract_mock.assert_not_called()
        self.assertEqual(raw_data.ocr_boxes[0]['text'], '기존')
        self.assertIn('skipped_existing_ocr_count=1', output.getvalue())

    def test_backfill_raw_ocr_replaces_existing_ocr_section(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/ocr-replace',
            title='OCR 교체',
            raw_text='공지 본문\n\n[OCR_TEXT]\n이전 OCR 2026.05.01',
            raw_html='<main><img src="/new.png"></main>',
        )

        with patch(
            'sync.management.commands.backfill_raw_ocr.extract_text_from_image_urls',
            return_value={
                'ocr_text': '새 OCR 2026.05.20',
                'ocr_provider': 'google_vision',
                'ocr_status': 'success',
                'ocr_error': '',
                'ocr_failed_count': 0,
            },
        ):
            call_command('backfill_raw_ocr', '--id', raw_data.id)

        raw_data.refresh_from_db()
        self.assertIn('새 OCR 2026.05.20', raw_data.raw_text)
        self.assertNotIn('이전 OCR 2026.05.01', raw_data.raw_text)
        self.assertEqual(raw_data.raw_text.count('[OCR_TEXT]'), 1)

    def test_backfill_raw_ocr_dry_run_does_not_update_raw_or_metadata(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/dry-run',
            title='dry-run',
            raw_text='공지 본문',
            raw_html='<main><img src="/dry.png"></main>',
            metadata_json={'ocr_status': 'skipped'},
        )

        with patch('sync.management.commands.backfill_raw_ocr.extract_text_from_image_urls') as extract_mock:
            call_command('backfill_raw_ocr', '--id', raw_data.id, '--dry-run')

        raw_data.refresh_from_db()
        extract_mock.assert_not_called()
        self.assertEqual(raw_data.raw_text, '공지 본문')
        self.assertEqual(raw_data.metadata_json, {'ocr_status': 'skipped'})

    def test_backfill_raw_ocr_reparse_creates_schedule_event(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/reparse-ocr',
            title='OCR reparse',
            raw_text='공지 본문',
            raw_html='<main><img src="/schedule.png"></main>',
        )
        output = StringIO()

        with patch(
            'sync.management.commands.backfill_raw_ocr.extract_text_from_image_urls',
            return_value={
                'ocr_text': '월말평가 2026.05.20',
                'ocr_provider': 'google_vision',
                'ocr_status': 'success',
                'ocr_error': '',
                'ocr_failed_count': 0,
            },
        ):
            call_command('backfill_raw_ocr', '--id', raw_data.id, '--reparse', stdout=output)

        self.assertEqual(ScheduleEvent.objects.count(), 1)
        self.assertEqual(ScheduleEvent.objects.get().raw_data, raw_data)
        self.assertIn('reparse_created_count=1', output.getvalue())

    def test_backfill_raw_ocr_no_image_increments_no_image_count(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/no-image',
            title='이미지 없음',
            raw_text='공지 본문',
            raw_html='<main>image 없음</main>',
        )
        output = StringIO()

        call_command('backfill_raw_ocr', '--id', raw_data.id, stdout=output)

        raw_data.refresh_from_db()
        self.assertIn('no_image_count=1', output.getvalue())
        self.assertEqual(raw_data.metadata_json['image_urls'], [])

    def test_backfill_raw_ocr_failure_does_not_fail_command(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/ocr-failed',
            title='OCR 실패',
            raw_text='공지 본문',
            raw_html='<main><img src="/fail.png"></main>',
        )
        output = StringIO()

        with patch(
            'sync.management.commands.backfill_raw_ocr.extract_text_from_image_urls',
            return_value={
                'ocr_text': '',
                'ocr_provider': 'google_vision',
                'ocr_status': 'failed',
                'ocr_error': 'Google Vision credentials are not configured.',
                'ocr_failed_count': 1,
            },
        ):
            call_command('backfill_raw_ocr', '--id', raw_data.id, stdout=output)

        raw_data.refresh_from_db()
        self.assertEqual(raw_data.raw_text, '공지 본문')
        self.assertEqual(raw_data.metadata_json['ocr_status'], 'failed')
        self.assertIn('ocr_failed_count=1', output.getvalue())

    def test_admin_manual_ocr_api_updates_raw_text_and_metadata(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/manual-ocr',
            title='manual OCR',
            raw_text='notice body',
            metadata_json={'notice_id': 'manual-ocr'},
        )
        admin_user = get_user_model().objects.create_user(
            username='admin',
            password='pass',
            is_staff=True,
        )
        self.client.force_login(admin_user)

        response = self.client.post(
            reverse('sync-raw-data-manual-ocr', args=[raw_data.id]),
            data=json.dumps({'ocr_text': 'manual schedule 2026.05.20'}),
            content_type='application/json',
        )

        raw_data.refresh_from_db()
        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['status'], 'success')
        self.assertEqual(payload['ocr_provider'], 'manual')
        self.assertEqual(payload['ocr_text_length'], len('manual schedule 2026.05.20'))
        self.assertIn('[OCR_TEXT]', raw_data.raw_text)
        self.assertIn('manual schedule 2026.05.20', raw_data.raw_text)
        self.assertEqual(raw_data.metadata_json['ocr_provider'], 'manual')
        self.assertEqual(raw_data.metadata_json['ocr_status'], 'success')
        self.assertEqual(raw_data.metadata_json['ocr_failed_count'], 0)

    def test_manual_ocr_api_requires_staff_user(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/manual-forbidden',
            title='manual OCR forbidden',
            raw_text='notice body',
        )
        user = get_user_model().objects.create_user(username='member', password='pass')
        self.client.force_login(user)

        response = self.client.post(
            reverse('sync-raw-data-manual-ocr', args=[raw_data.id]),
            data=json.dumps({'ocr_text': 'manual schedule 2026.05.20'}),
            content_type='application/json',
        )

        raw_data.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(raw_data.raw_text, 'notice body')

    def test_manual_ocr_api_rejects_empty_text(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/manual-empty',
            title='manual OCR empty',
            raw_text='notice body',
        )
        admin_user = get_user_model().objects.create_user(
            username='admin-empty',
            password='pass',
            is_staff=True,
        )
        self.client.force_login(admin_user)

        response = self.client.post(
            reverse('sync-raw-data-manual-ocr', args=[raw_data.id]),
            data=json.dumps({'ocr_text': '   '}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('ocr_text is required', response.json()['detail'])

    def test_set_raw_ocr_text_command_updates_raw_data(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/manual-command',
            title='manual OCR command',
            raw_text='notice body',
        )
        output = StringIO()

        call_command(
            'set_raw_ocr_text',
            '--id',
            raw_data.id,
            '--text',
            'command schedule 2026.05.20',
            stdout=output,
        )

        raw_data.refresh_from_db()
        self.assertIn('command schedule 2026.05.20', raw_data.raw_text)
        self.assertEqual(raw_data.metadata_json['ocr_provider'], 'manual')
        self.assertIn('updated=true', output.getvalue())

    def test_set_raw_ocr_text_command_dry_run_does_not_update(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/manual-command-dry-run',
            title='manual OCR command dry run',
            raw_text='notice body',
            metadata_json={'ocr_status': 'skipped'},
        )
        output = StringIO()

        call_command(
            'set_raw_ocr_text',
            '--id',
            raw_data.id,
            '--text',
            'command schedule 2026.05.20',
            '--dry-run',
            stdout=output,
        )

        raw_data.refresh_from_db()
        self.assertEqual(raw_data.raw_text, 'notice body')
        self.assertEqual(raw_data.metadata_json, {'ocr_status': 'skipped'})
        self.assertIn('updated=false', output.getvalue())

    def test_set_raw_ocr_text_command_reparse_creates_schedule_event(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/manual-command-reparse',
            title='manual OCR command reparse',
            raw_text='notice body',
        )
        output = StringIO()

        call_command(
            'set_raw_ocr_text',
            '--id',
            raw_data.id,
            '--text',
            'manual schedule 2026.05.20',
            '--reparse',
            stdout=output,
        )

        self.assertEqual(ScheduleEvent.objects.count(), 1)
        self.assertEqual(ScheduleEvent.objects.get().raw_data, raw_data)
        self.assertIn('reparse_created_count=1', output.getvalue())


def _notice_item(source_url, notice_id):
    return {
        'source_type': 'notice',
        'source_url': source_url,
        'title': 'SSAFY notice',
        'raw_text': 'SSAFY 일정 2026.05.20',
        'raw_html': '<main>SSAFY 일정 2026.05.20</main>',
        'metadata_json': {
            'notice_id': notice_id,
            'published_at': '2026-05-14',
            'collected_from': 'ssafy_notice',
        },
    }


def _academic_rule_item(source_url, notice_id):
    return {
        'source_type': 'academic_rule',
        'source_url': source_url,
        'title': 'SSAFY academic rule',
        'raw_text': '학사규정 본문',
        'raw_html': '<main>학사규정 본문</main>',
        'metadata_json': {
            'notice_id': notice_id,
            'published_at': '2026-05-14',
            'collected_from': 'ssafy_notice',
        },
    }


def _raw_data(source_url, raw_text, source_type='notice'):
    return RawSsafyData.objects.create(
        source_type=source_type,
        source_url=source_url,
        title=f'{source_type} title',
        raw_text=raw_text,
        raw_html=f'<main>{raw_text}</main>',
        metadata_json={'notice_id': source_url.rsplit('/', 1)[-1]},
    )


def _calendar_ocr_boxes():
    return [
        {'text': '1', 'x1': 20, 'y1': 20, 'x2': 32, 'y2': 40},
        {'text': '월', 'x1': 31, 'y1': 20, 'x2': 50, 'y2': 40},
        {'text': 'SUN', 'x1': 10, 'y1': 60, 'x2': 40, 'y2': 80},
        {'text': 'MON', 'x1': 110, 'y1': 60, 'x2': 140, 'y2': 80},
        {'text': 'TUE', 'x1': 210, 'y1': 60, 'x2': 240, 'y2': 80},
        {'text': 'WED', 'x1': 310, 'y1': 60, 'x2': 340, 'y2': 80},
        {'text': 'THU', 'x1': 410, 'y1': 60, 'x2': 440, 'y2': 80},
        {'text': 'FRI', 'x1': 510, 'y1': 60, 'x2': 540, 'y2': 80},
        {'text': 'SAT', 'x1': 610, 'y1': 60, 'x2': 640, 'y2': 80},
        {'text': '15', 'x1': 10, 'y1': 180, 'x2': 24, 'y2': 200},
        {'text': '16', 'x1': 110, 'y1': 180, 'x2': 124, 'y2': 200},
        {'text': '17', 'x1': 210, 'y1': 180, 'x2': 224, 'y2': 200},
        {'text': '18', 'x1': 310, 'y1': 180, 'x2': 324, 'y2': 200},
        {'text': '19', 'x1': 410, 'y1': 180, 'x2': 424, 'y2': 200},
        {'text': '20', 'x1': 510, 'y1': 180, 'x2': 524, 'y2': 200},
        {'text': '21', 'x1': 610, 'y1': 180, 'x2': 624, 'y2': 200},
        {'text': 'SW 역량테스트', 'x1': 505, 'y1': 212, 'x2': 590, 'y2': 232, 'confidence': 0.96},
    ]


def _review_required_exam_ocr_boxes():
    boxes = _calendar_ocr_boxes()
    boxes[-1] = {'text': '배틀싸피과목평가', 'x1': 500, 'y1': 212, 'x2': 600, 'y2': 232, 'confidence': 0.96}
    return boxes


class _FailedLoginPage:
    def goto(self, *args, **kwargs):
        return None

    def fill(self, *args, **kwargs):
        return None

    def click(self, *args, **kwargs):
        return None

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def locator(self, *args, **kwargs):
        return self

    def count(self):
        return 1


class _StaticPage:
    def __init__(self, html):
        self.html = html

    def goto(self, *args, **kwargs):
        return None

    def content(self):
        return self.html


class _ImageResponse:
    content = b'image-bytes'

    def raise_for_status(self):
        return None


class _JsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _google_vision_modules(ocr_text):
    vision_module = types.ModuleType('google.cloud.vision')

    class Image:
        def __init__(self, content):
            self.content = content

    class ImageAnnotatorClient:
        def text_detection(self, image):
            return types.SimpleNamespace(
                text_annotations=[types.SimpleNamespace(description=ocr_text)] if ocr_text else [],
                error=types.SimpleNamespace(message=''),
            )

    vision_module.Image = Image
    vision_module.ImageAnnotatorClient = ImageAnnotatorClient
    google_module = types.ModuleType('google')
    cloud_module = types.ModuleType('google.cloud')
    cloud_module.vision = vision_module
    google_module.cloud = cloud_module
    return {
        'google': google_module,
        'google.cloud': cloud_module,
        'google.cloud.vision': vision_module,
    }
