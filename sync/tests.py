import json
import types
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from schedules.models import ScheduleEvent
from sync.models import CrawlJobLog, RawSsafyData
from sync.services.import_service import run_notice_import, run_sample_notice_import
from sync.services.ocr_service import extract_text_from_image_urls
from sync.services.reparse_service import reparse_raw_data_to_events
from sync.services.schedule_parser import parse_schedule_candidates
from bs4 import BeautifulSoup

from sync.services.ssafy_crawler import (
    SsafyCrawlerError,
    extract_image_urls_from_html,
    _extract_notice_links,
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

    def test_parser_failure_source_type_is_recorded_in_message(self):
        with patch('sync.services.import_service.load_notices_by_mode', return_value=[_notice_item('https://example.com/notices/error', 'notice-error')]):
            with patch('sync.services.import_service.parse_schedule_candidates', side_effect=ValueError('bad date')):
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
