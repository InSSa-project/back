import json
import tempfile
import types
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from schedules.models import ScheduleEvent
from schedules.utils import is_wrapper_schedule_title, normalize_event_title_for_dedupe
from sync.models import CrawlJobLog, RawSsafyData
from sync.services.import_service import run_notice_import, run_sample_notice_import
from sync.services.ocr_service import extract_text_from_image_urls
from sync.services.reparse_service import reparse_raw_data_to_events
from sync.services.schedule_parser import ParsedSchedule, parse_schedule_candidates, parse_schedule_candidates_with_debug
from bs4 import BeautifulSoup

from sync.services.ssafy_crawler import (
    SsafyCrawlerError,
    SsafySessionExpiredError,
    extract_academic_rule_reply_image_urls_from_html,
    extract_image_urls_from_html,
    _extract_next_page_url,
    _extract_notice_links,
    _parse_detail_soup,
    _login_ssafy,
    _collect_authenticated_list,
    _open_academic_toggles,
    get_last_collection_debug,
    load_ssafy_authenticated_documents,
    _extract_detail_url_from_onclick,
    _extract_pagination_totals,
    _filter_controls_debug,
    _pagination_controls_debug,
    _source_collection_specs,
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

    def test_duplicate_schedule_event_keeps_each_raw_data_source_mapping(self):
        first_item = _notice_item('https://example.com/notices/event-1', 'notice-event-1')
        second_item = _notice_item('https://example.com/notices/event-2', 'notice-event-2')
        first_item['title'] = 'Notice A'
        second_item['title'] = 'Notice B'
        first_item['raw_text'] = 'Shared schedule 2026.05.20'
        second_item['raw_text'] = 'Shared schedule 2026.05.20'

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[first_item, second_item]):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(job_log.raw_count, 2)
        self.assertEqual(job_log.event_count, 2)
        self.assertEqual(job_log.skipped_count, 0)
        self.assertEqual(RawSsafyData.objects.count(), 2)
        self.assertEqual(ScheduleEvent.objects.count(), 2)
        self.assertEqual(
            set(ScheduleEvent.objects.values_list('raw_data__source_url', flat=True)),
            {'https://example.com/notices/event-1', 'https://example.com/notices/event-2'},
        )

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

    def test_dedupe_schedule_events_uses_normalized_title_and_keeps_manual_rows(self):
        raw_data = _raw_data('https://example.com/raw/dedupe-normalized', 'AI challenge')
        start_at = timezone.datetime(2026, 4, 2, tzinfo=timezone.get_current_timezone())
        end_at = timezone.datetime(2026, 4, 3, tzinfo=timezone.get_current_timezone())
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='마이스터고) AI 챌린지 (예정)',
            start_at=start_at,
            end_at=end_at,
            is_all_day=True,
            event_type='notice',
            source_type='notice',
            metadata_json={'track': 'meister'},
        )
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='마이스터고) AI 챌린지',
            start_at=start_at,
            end_at=end_at,
            is_all_day=True,
            event_type='notice',
            source_type='notice',
            metadata_json={'track': 'meister'},
        )
        ScheduleEvent.objects.create(
            title='마이스터고) AI 챌린지',
            start_at=start_at,
            end_at=end_at,
            is_all_day=True,
            event_type='notice',
            source_type='manual',
            metadata_json={'track': 'meister'},
        )
        output = StringIO()

        call_command('dedupe_schedule_events', stdout=output)

        self.assertIn('deleted_count=1', output.getvalue())
        self.assertEqual(ScheduleEvent.objects.filter(raw_data__isnull=False).count(), 1)
        self.assertEqual(ScheduleEvent.objects.filter(raw_data__isnull=True).count(), 1)

    def test_dedupe_schedule_events_keeps_repeated_title_on_different_times(self):
        raw_data = _raw_data('https://example.com/raw/dedupe-repeated-practice', 'practice timetable')
        for day in [12, 13]:
            start_at = timezone.datetime(2026, 5, day, tzinfo=timezone.get_current_timezone())
            ScheduleEvent.objects.create(
                raw_data=raw_data,
                title='[실습 및 Q&A]',
                start_at=start_at,
                end_at=start_at + timedelta(days=1),
                is_all_day=True,
                event_type='study',
                source_type='notice',
                metadata_json={'track': 'data'},
            )
        output = StringIO()

        call_command('dedupe_schedule_events', stdout=output)

        self.assertIn('deleted_count=0', output.getvalue())
        self.assertEqual(ScheduleEvent.objects.count(), 2)

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

    def test_normalized_title_treats_status_suffix_as_same_event(self):
        self.assertEqual(
            normalize_event_title_for_dedupe('마이스터고) AI 챌린지 (예정)'),
            normalize_event_title_for_dedupe('마이스터고) AI 챌린지'),
        )

    def test_wrapper_schedule_title_is_detected_generically(self):
        self.assertTrue(is_wrapper_schedule_title('Data) 학습 주차 Data 트랙 시간표'))
        self.assertTrue(is_wrapper_schedule_title('Python) 학습 주차 Python 트랙 시간표'))
        self.assertFalse(is_wrapper_schedule_title('[학습] Django: DRF 1'))
        self.assertFalse(is_wrapper_schedule_title('[학습] Django: DRF 2'))
        self.assertFalse(is_wrapper_schedule_title('[학습] JS: DOM'))
        self.assertFalse(is_wrapper_schedule_title('[학습] JS: Basic Syntax 1'))
        self.assertFalse(is_wrapper_schedule_title('[실습 및 Q&A]'))
        self.assertFalse(is_wrapper_schedule_title('중식'))
        self.assertFalse(is_wrapper_schedule_title('과목평가 9'))

    def test_reparse_uses_normalized_title_to_skip_duplicate_events(self):
        first_raw = _raw_data('https://example.com/raw/challenge-1', '2026.04.02 AI 챌린지 (예정)')
        second_raw = _raw_data('https://example.com/raw/challenge-2', '2026.04.02 AI 챌린지')

        first_summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(pk=first_raw.pk))
        second_summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(pk=second_raw.pk))

        self.assertEqual(first_summary.created_count, 1)
        self.assertEqual(second_summary.created_count, 1)
        self.assertEqual(second_summary.skipped_count, 0)
        self.assertEqual(ScheduleEvent.objects.count(), 2)

    def test_reparse_skips_timetable_wrapper_title(self):
        raw_data = _raw_data(
            'https://example.com/raw/timetable-wrapper',
            '2026.05.12 Data) 학습 주차 Data 트랙 시간표',
        )

        summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(pk=raw_data.pk))

        raw_data.refresh_from_db()
        self.assertEqual(summary.created_count, 0)
        self.assertEqual(summary.skipped_count, 1)
        self.assertEqual(ScheduleEvent.objects.count(), 0)
        self.assertIn('timetable_title_equals_source_title', raw_data.metadata_json['parser_warnings'])
        self.assertEqual(summary.wrapper_skip_count, 1)

    def test_reparse_creates_timetable_cell_titles_for_may_calendar(self):
        source_title = '[학습] 5월 2주차 Data 트랙 시간표'
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/raw/timetable-cells',
            title=source_title,
            raw_text='[OCR_TEXT]\n5월\n11\n12\n13\n14\n15\nDjango DRF\nJS DOM\n중식',
            ocr_boxes=_timetable_many_cell_ocr_boxes(),
        )

        summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(pk=raw_data.pk))

        titles = list(ScheduleEvent.objects.order_by('start_at', 'id').values_list('title', flat=True))
        may_count = ScheduleEvent.objects.filter(start_at__date__gte='2026-05-01', start_at__date__lt='2026-06-01').count()
        self.assertEqual(summary.created_count, 6)
        self.assertEqual(summary.wrapper_skip_count, 0)
        self.assertEqual(may_count, 6)
        self.assertIn('[학습] Django: DRF 1', titles)
        self.assertIn('[학습] Django: DRF 2', titles)
        self.assertIn('[학습] JS: DOM', titles)
        self.assertIn('[학습] JS: Basic Syntax 1', titles)
        self.assertIn('[실습 및 Q&A]', titles)
        self.assertNotIn('중식', titles)
        self.assertIn('과목평가 9', titles)
        self.assertNotIn(source_title, titles)

    def test_reparse_wrapper_title_does_not_block_timetable_cell_title(self):
        source_title = '[학습] 5월 2주차 Python 트랙 시간표'
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/raw/timetable-wrapper-and-cell',
            title=source_title,
            raw_text='[OCR_TEXT]\n5월\n11\n12\n13\n14\n15\nPython time table',
            ocr_boxes=_timetable_wrapper_and_cell_ocr_boxes(source_title),
        )

        summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(pk=raw_data.pk))

        self.assertEqual(summary.created_count, 1)
        self.assertEqual(summary.wrapper_skip_count, 1)
        self.assertEqual(list(ScheduleEvent.objects.values_list('title', flat=True)), ['[학습] JS: DOM'])

    def test_reparse_warns_timetable_class_on_korean_holiday_without_blocking(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/raw/timetable-holiday',
            title='[학습] 5월 1주차 Data 트랙 시간표',
            raw_text='[OCR_TEXT]\n5월 5일\nDjango DRF',
            ocr_boxes=_timetable_holiday_ocr_boxes(),
        )

        summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(pk=raw_data.pk))

        raw_data.refresh_from_db()
        self.assertEqual(summary.created_count, 1)
        self.assertEqual(summary.validation_skip_count, 0)
        self.assertEqual(ScheduleEvent.objects.count(), 1)
        self.assertIn('generated_class_on_korean_holiday', raw_data.metadata_json['parser_warnings'])
        self.assertIn('generated_class_on_korean_holiday', ScheduleEvent.objects.get().metadata_json['parser_warnings'])

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
        self.assertIn('duplicate_skip_count=0', value)
        self.assertIn('wrapper_skip_count=0', value)

    def test_reparse_command_warns_when_target_raw_data_is_low(self):
        _raw_data('https://example.com/raw/reparse-low-count', 'Low count schedule 2026.05.20')
        output = StringIO()

        call_command('reparse_raw_ssafy_data', '--source-type', 'notice', '--dry-run', stdout=output)

        value = output.getvalue()
        self.assertIn('WARNING: only 1 RawSsafyData rows matched', value)
        self.assertIn('target_raw_count=1', value)

    def test_reparse_command_prints_duplicate_reason_and_existing_dates(self):
        raw_data = _raw_data('https://example.com/raw/reparse-duplicate-command', 'Duplicate schedule 2026.05.20')
        summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(pk=raw_data.pk))
        self.assertEqual(summary.created_count, 1)
        output = StringIO()

        call_command('reparse_raw_ssafy_data', '--id', raw_data.id, stdout=output)

        value = output.getvalue()
        self.assertIn('created_count=0', value)
        self.assertIn('skip_reasons=duplicate:1|wrapper:0|validation:0|empty_title:0', value)
        self.assertIn('existing_duplicate_event_dates=2026-05-20:Duplicate schedule', value)

    def test_restore_calendar_data_aborts_when_raw_data_is_insufficient(self):
        _raw_data('https://example.com/raw/restore-low-count', 'Restore schedule 2026.05.20')

        with self.assertRaisesMessage(CommandError, 'Not enough RawSsafyData rows'):
            call_command('restore_calendar_data', stdout=StringIO())

        self.assertEqual(ScheduleEvent.objects.count(), 0)

    def test_restore_calendar_data_dry_run_does_not_delete_existing_generated_events(self):
        raw_rows = [
            _raw_data(f'https://example.com/raw/restore-{index}', f'Restore schedule {index} 2026.05.2{index}')
            for index in range(3)
        ]
        ScheduleEvent.objects.create(
            raw_data=raw_rows[0],
            title='Existing generated schedule',
            start_at=timezone.datetime(2026, 5, 20, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 5, 21, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='study',
            source_type='notice',
        )
        output = StringIO()

        call_command('restore_calendar_data', '--dry-run', stdout=output)

        value = output.getvalue()
        self.assertIn('Calendar restore preflight.', value)
        self.assertIn('Generated schedule reset completed.', value)
        self.assertIn('dry_run=true', value)
        self.assertEqual(ScheduleEvent.objects.count(), 1)

    def test_debug_schedule_events_command_outputs_calendar_counts(self):
        _raw_data('https://example.com/raw/debug-command', 'Debug schedule 2026.05.20')
        output = StringIO()

        call_command('debug_schedule_events', stdout=output)

        value = output.getvalue()
        self.assertIn('schedule_total=0', value)
        self.assertIn('schedule_2026_05_count=0', value)
        self.assertIn('generated_schedule_count=0', value)
        self.assertIn('manual_schedule_count=0', value)
        self.assertIn('monthly_schedule_counts=none', value)
        self.assertIn('raw_total=1', value)
        self.assertIn('raw_source_type_counts=notice:1', value)
        self.assertIn('reparse_target_count=1', value)
        self.assertIn('WARNING: reparse target RawSsafyData count is low', value)
        self.assertIn('reparse_dry_run=raw_checked:1|candidate:1|created:1', value)

    def test_debug_schedule_events_date_outputs_event_source_metadata(self):
        raw_data = _raw_data('https://example.com/raw/debug-date', '월말 평가 source 2026.05.05')
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='월말 평가',
            start_at=timezone.datetime(2026, 5, 5, 9, 0, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 5, 5, 10, 0, tzinfo=timezone.get_current_timezone()),
            is_all_day=False,
            event_type='exam',
            source_type='notice',
            metadata_json={
                'display_title': '월말평가',
                'source_title': '[학습] 5월 2주차 Data 트랙 시간표',
                'raw_title': '월말 평가 OCR',
                'parser_type': 'timetable_grid',
                'date_mapping_source': 'fallback',
                'original_header_date': '2026-05-12',
                'fallback_date': '2026-05-05',
            },
        )
        output = StringIO()

        call_command('debug_schedule_events', '--date', '2026-05-05', stdout=output)

        value = output.getvalue()
        self.assertIn('date=2026-05-05', value)
        self.assertIn('event_count=1', value)
        self.assertIn('title=월말 평가', value)
        self.assertIn('display_title=월말평가', value)
        self.assertIn(f'raw_data_id={raw_data.id}', value)
        self.assertIn('source_title=notice title', value)
        self.assertIn('raw_title=월말 평가 OCR', value)
        self.assertIn('parser_type=timetable_grid', value)
        self.assertIn('date_mapping_source=fallback', value)
        self.assertIn('original_header_date=2026-05-12', value)
        self.assertIn('fallback_date=2026-05-05', value)

    def test_debug_schedule_events_month_reports_suspicious_fallback_holiday_event(self):
        raw_data = _raw_data('https://example.com/raw/debug-month-holiday', 'monthly exam source')
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='월말 평가',
            start_at=timezone.datetime(2026, 5, 5, 9, 0, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 5, 5, 10, 0, tzinfo=timezone.get_current_timezone()),
            is_all_day=False,
            event_type='exam',
            source_type='notice',
            metadata_json={
                'display_title': '월말 평가',
                'parser_type': 'timetable_grid',
                'date_mapping_source': 'fallback_week',
                'original_header_date': '2026-05-12',
                'fallback_date': '2026-05-05',
            },
        )
        output = StringIO()

        call_command('debug_schedule_events', '--month', '2026-05', '--no-reparse', stdout=output)

        value = output.getvalue()
        self.assertIn('schedule_2026_05_count=1', value)
        self.assertIn('suspicious_events=', value)
        self.assertIn('holiday_generated', value)
        self.assertIn('fallback_week', value)
        self.assertIn('월말 평가', value)

    def test_debug_schedule_events_month_outputs_quality_buckets(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/raw/debug-quality',
            title='평가 안내',
            raw_text='월말평가',
        )
        start_at = timezone.datetime(2026, 5, 20, tzinfo=timezone.get_current_timezone())
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='월말평가',
            start_at=start_at,
            end_at=start_at + timedelta(days=1),
            is_all_day=True,
            event_type='exam',
            source_type='notice',
            metadata_json={
                'raw_data_id': raw_data.id,
                'source_url': raw_data.source_url,
                'source_title': raw_data.title,
                'track': 'Python',
                'track_key': 'python',
                'parser_type': 'evaluation_notice',
            },
        )
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='월말평가',
            start_at=start_at,
            end_at=start_at + timedelta(days=1),
            is_all_day=True,
            event_type='exam',
            source_type='notice',
            metadata_json={
                'raw_data_id': raw_data.id,
                'source_url': 'https://example.com/wrong',
                'source_title': '다른 공지',
                'track': 'Python',
                'track_key': 'python',
                'parser_type': 'evaluation_notice',
            },
        )
        ScheduleEvent.objects.create(
            title='DB',
            start_at=start_at,
            end_at=start_at + timedelta(days=1),
            is_all_day=True,
            event_type='study',
            source_type='manual',
        )
        output = StringIO()

        call_command('debug_schedule_events', '--month', '2026-05', '--no-reparse', stdout=output)

        value = output.getvalue()
        self.assertIn('track_event_counts=', value)
        self.assertIn('python:2', value)
        self.assertIn('common:1', value)
        self.assertIn('evaluation_missing_tracks=', value)
        self.assertIn('java_non_major', value)
        self.assertIn('source_mismatches=', value)
        self.assertIn('source_url,source_title', value)
        self.assertIn('meaningless_titles=', value)
        self.assertIn('DB', value)
        self.assertIn('duplicate_candidates=', value)
        self.assertIn('월말평가:python', value)

    def test_debug_schedule_events_month_reports_empty_weekdays(self):
        output = StringIO()

        call_command('debug_schedule_events', '--month', '2026-05', '--no-reparse', stdout=output)

        value = output.getvalue()
        self.assertIn('suspicious_empty_weekdays=', value)
        self.assertIn('2026-05-01', value)
        self.assertNotIn('suspicious_empty_weekdays=2026-05-05', value)

    def test_repair_calendar_events_month_dry_run_reports_duplicates_and_keeps_manual_out_of_scope(self):
        raw_data = _raw_data('https://example.com/raw/repair-duplicate-month', 'repair duplicate source')
        start_at = timezone.datetime(2026, 5, 26, tzinfo=timezone.get_current_timezone())
        end_at = timezone.datetime(2026, 5, 27, tzinfo=timezone.get_current_timezone())
        first = ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='월말 평가',
            start_at=start_at,
            end_at=end_at,
            is_all_day=True,
            event_type='exam',
            source_type='notice',
        )
        second = ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='월말 평가',
            start_at=start_at,
            end_at=end_at,
            is_all_day=True,
            event_type='exam',
            source_type='notice',
        )
        manual = ScheduleEvent.objects.create(
            title='월말 평가',
            start_at=start_at,
            end_at=end_at,
            is_all_day=True,
            event_type='exam',
            source_type='manual',
        )
        output = StringIO()

        call_command('repair_calendar_events', '--month', '2026-05', '--dry-run', stdout=output)

        value = output.getvalue()
        self.assertIn('dry_run=true', value)
        self.assertIn('confirmed=false', value)
        self.assertIn('duplicate_events=', value)
        self.assertIn(str(first.id), value)
        self.assertIn(str(second.id), value)
        self.assertNotIn(f'#{manual.id}#', value)
        self.assertEqual(ScheduleEvent.objects.count(), 3)

    def test_repair_calendar_events_month_confirm_deletes_only_generated_holiday_events(self):
        raw_data = _raw_data('https://example.com/raw/repair-confirm-holiday', 'repair holiday source')
        generated = ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='?붾쭚 ?됯?',
            start_at=timezone.datetime(2026, 5, 5, 9, 0, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 5, 5, 10, 0, tzinfo=timezone.get_current_timezone()),
            is_all_day=False,
            event_type='exam',
            source_type='notice',
            metadata_json={'source_title': '시간표 공지'},
        )
        metadata_generated = ScheduleEvent.objects.create(
            title='월말 평가',
            start_at=timezone.datetime(2026, 5, 5, 11, 0, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 5, 5, 12, 0, tzinfo=timezone.get_current_timezone()),
            is_all_day=False,
            event_type='exam',
            source_type='notice',
            metadata_json={'raw_data_id': raw_data.id, 'source_title': '시간표 공지'},
        )
        manual = ScheduleEvent.objects.create(
            title='개인 약속',
            start_at=timezone.datetime(2026, 5, 5, 13, 0, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 5, 5, 14, 0, tzinfo=timezone.get_current_timezone()),
            is_all_day=False,
            event_type='exam',
            source_type='manual',
        )
        output = StringIO()

        call_command('repair_calendar_events', '--month', '2026-05', '--confirm', stdout=output)

        value = output.getvalue()
        self.assertIn(f'id={generated.id}', value)
        self.assertIn(f'id={metadata_generated.id}', value)
        self.assertIn('deleted_count=2', value)
        self.assertFalse(ScheduleEvent.objects.filter(id=generated.id).exists())
        self.assertFalse(ScheduleEvent.objects.filter(id=metadata_generated.id).exists())
        self.assertTrue(ScheduleEvent.objects.filter(id=manual.id).exists())

    def test_seed_june_online_week_2026_excludes_holiday_weekend_and_is_idempotent(self):
        first_output = StringIO()
        second_output = StringIO()

        call_command('seed_june_online_week_2026', stdout=first_output)
        call_command('seed_june_online_week_2026', stdout=second_output)

        dates = list(
            ScheduleEvent.objects.filter(title='온라인 위크')
            .order_by('start_at')
            .values_list('start_at__date', flat=True)
        )
        self.assertEqual(
            [day.isoformat() for day in dates],
            [
                '2026-06-02',
                '2026-06-05',
                '2026-06-08',
                '2026-06-09',
                '2026-06-10',
                '2026-06-11',
                '2026-06-12',
            ],
        )
        self.assertEqual(ScheduleEvent.objects.filter(title='온라인 위크').count(), 7)
        self.assertIn('created_count=7', first_output.getvalue())
        self.assertIn('skipped_count=7', second_output.getvalue())
        self.assertFalse(ScheduleEvent.objects.filter(start_at__date='2026-06-03', title='온라인 위크').exists())
        self.assertFalse(ScheduleEvent.objects.filter(start_at__date='2026-06-06', title='온라인 위크').exists())

    def test_seed_june_online_week_2026_does_not_duplicate_existing_online_week(self):
        ScheduleEvent.objects.create(
            title='온라인 위크',
            start_at=timezone.datetime(2026, 6, 2, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 6, 3, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='study',
            source_type='notice',
        )
        stale_seed = ScheduleEvent.objects.create(
            title='온라인 위크',
            start_at=timezone.datetime(2026, 6, 2, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 6, 3, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='etc',
            source_type='manual_seed',
            metadata_json={'reason': 'online_week_recovery'},
        )
        output = StringIO()

        call_command('seed_june_online_week_2026', stdout=output)

        self.assertFalse(ScheduleEvent.objects.filter(id=stale_seed.id).exists())
        self.assertEqual(ScheduleEvent.objects.filter(start_at__date='2026-06-02', title='온라인 위크').count(), 1)
        self.assertIn('created_count=6', output.getvalue())
        self.assertIn('duplicate_seed_removed_count=1', output.getvalue())

    def test_reparse_blocks_fallback_week_timetable_candidates_by_default(self):
        raw_data = _raw_data('https://example.com/raw/fallback-week-block', 'fallback source')
        start_at = timezone.datetime(2026, 5, 5, 9, 0, tzinfo=timezone.get_current_timezone())
        schedule = ParsedSchedule(
            title='월말 평가',
            description='fallback candidate',
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            is_all_day=False,
            event_type='exam',
            metadata_json={
                'parser_type': 'timetable_grid',
                'date_mapping_source': 'fallback_week',
                'confidence': 0.4,
            },
        )
        grid_debug = types.SimpleNamespace(
            metadata_json={},
            review_required_candidates=[],
            as_dict=lambda: {},
        )

        with patch('sync.services.reparse_service.parse_schedule_candidates_with_debug', return_value=([schedule], grid_debug)):
            summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(pk=raw_data.pk))

        raw_data.refresh_from_db()
        self.assertEqual(summary.created_count, 0)
        self.assertEqual(summary.skipped_count, 1)
        self.assertEqual(summary.validation_skip_count, 1)
        self.assertEqual(ScheduleEvent.objects.count(), 0)
        self.assertIn('fallback_week_timetable_blocked', raw_data.metadata_json['parser_warnings'])

    def test_reparse_merges_same_raw_date_range_with_different_titles(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/raw/merge-title',
            title='[공지] 평가 안내',
            raw_text='2026.05.20 월말 평가 안내',
            metadata_json={'published_at': '2026-05-01'},
        )
        first_start = timezone.datetime(2026, 5, 20, tzinfo=timezone.get_current_timezone())
        first = ParsedSchedule(
            title='월말 평가 안내',
            description='first',
            start_at=first_start,
            end_at=first_start + timedelta(days=1),
            is_all_day=True,
            event_type='exam',
            metadata_json={'date_mapping_source': 'ocr_text_date', 'extracted_date_range': '2026-05-20..2026-05-21'},
        )
        second = ParsedSchedule(
            title='월말 평가',
            description='second',
            start_at=first_start,
            end_at=first_start + timedelta(days=1),
            is_all_day=True,
            event_type='exam',
            metadata_json={'date_mapping_source': 'ocr_text_date', 'extracted_date_range': '2026-05-20..2026-05-21'},
        )
        grid_debug = types.SimpleNamespace(metadata_json={}, review_required_candidates=[], as_dict=lambda: {})

        with patch('sync.services.reparse_service.parse_schedule_candidates_with_debug', return_value=([first, second], grid_debug)):
            summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(pk=raw_data.pk))

        event = ScheduleEvent.objects.get()
        raw_data.refresh_from_db()
        self.assertEqual(summary.created_count, 1)
        self.assertEqual(summary.duplicate_skip_count, 1)
        self.assertEqual(event.title, '월말 평가')
        self.assertIn('월말 평가 안내', event.metadata_json['alias_titles'])
        self.assertEqual(event.metadata_json['normalized_content_hash'], raw_data.metadata_json['normalized_content_hash'])
        self.assertEqual(event.metadata_json['source_published_at'], '2026-05-01')
        self.assertEqual(event.metadata_json['date_mapping_source'], 'ocr_text_date')
        self.assertEqual(event.metadata_json['extracted_date_range'], '2026-05-20..2026-05-21')

    def test_repair_month_confirm_merges_generated_duplicates_and_keeps_manual(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/raw/repair-merge',
            title='평가 공지',
            raw_text='2026.05.20 월말 평가',
            metadata_json={'normalized_content_hash': 'samehash', 'ocr_text_hash': 'ocrhash'},
        )
        start_at = timezone.datetime(2026, 5, 20, tzinfo=timezone.get_current_timezone())
        first = ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='월말 평가 안내',
            start_at=start_at,
            end_at=start_at + timedelta(days=1),
            is_all_day=True,
            event_type='exam',
            source_type='notice',
            source_id=str(raw_data.id),
            metadata_json={
                'raw_data_id': raw_data.id,
                'normalized_content_hash': 'samehash',
                'ocr_text_hash': 'ocrhash',
                'extracted_date_range': '2026-05-20..2026-05-21',
                'date_mapping_source': 'ocr_text_date',
            },
        )
        second = ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='월말 평가',
            start_at=start_at,
            end_at=start_at + timedelta(days=1),
            is_all_day=True,
            event_type='exam',
            source_type='notice',
            source_id=str(raw_data.id),
            metadata_json={
                'raw_data_id': raw_data.id,
                'normalized_content_hash': 'samehash',
                'ocr_text_hash': 'ocrhash',
                'extracted_date_range': '2026-05-20..2026-05-21',
                'date_mapping_source': 'ocr_text_date',
            },
        )
        manual = ScheduleEvent.objects.create(
            title='월말 평가 개인 메모',
            start_at=start_at,
            end_at=start_at + timedelta(days=1),
            is_all_day=True,
            event_type='exam',
            source_type='manual',
        )
        output = StringIO()

        call_command('repair_calendar_events', '--month', '2026-05', '--confirm', stdout=output)

        value = output.getvalue()
        self.assertIn('merge_candidates=', value)
        self.assertIn('deleted_count=1', value)
        self.assertEqual(ScheduleEvent.objects.filter(raw_data=raw_data).count(), 1)
        kept = ScheduleEvent.objects.get(raw_data=raw_data)
        self.assertEqual(kept.title, '월말 평가')
        self.assertIn(first.id, kept.metadata_json['merged_from_event_ids'])
        self.assertIn(second.id, kept.metadata_json['merged_from_event_ids'])
        self.assertIn('월말 평가 안내', kept.metadata_json['alias_titles'])
        self.assertTrue(ScheduleEvent.objects.filter(id=manual.id).exists())

    def test_debug_month_reports_source_published_at_suspicious_event(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/raw/source-date',
            title='공지',
            raw_text='작성일만 있는 공지',
            metadata_json={'published_at': '2026-05-20'},
        )
        start_at = timezone.datetime(2026, 5, 20, tzinfo=timezone.get_current_timezone())
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='의심 일정',
            start_at=start_at,
            end_at=start_at + timedelta(days=1),
            is_all_day=True,
            event_type='notice',
            source_type='notice',
            source_id=str(raw_data.id),
            metadata_json={
                'raw_data_id': raw_data.id,
                'source_published_at': '2026-05-20',
                'date_mapping_source': 'unknown',
            },
        )
        output = StringIO()

        call_command('debug_schedule_events', '--month', '2026-05', '--no-reparse', stdout=output)

        self.assertIn('source_published_at_date', output.getvalue())

    def test_body_date_takes_precedence_over_source_published_at(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/raw/body-date-priority',
            title='평가 공지',
            raw_text='2026.05.20 월말 평가',
            metadata_json={'published_at': '2026-05-01'},
        )

        summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(pk=raw_data.pk))

        event = ScheduleEvent.objects.get()
        self.assertEqual(summary.created_count, 1)
        self.assertEqual(timezone.localdate(event.start_at).isoformat(), '2026-05-20')
        self.assertEqual(event.metadata_json['source_published_at'], '2026-05-01')
        self.assertNotEqual(timezone.localdate(event.start_at).isoformat(), event.metadata_json['source_published_at'])

    def test_timetable_event_rejects_other_month_source_title_mapping(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/raw/april-week5',
            title='[??] 4? 5?? Embedded Robot ?? ???',
            raw_text='[OCR_TEXT]',
        )
        start_at = timezone.datetime(2026, 5, 8, tzinfo=timezone.get_current_timezone())
        schedule = ParsedSchedule(
            title='?? ??',
            description='wrong source mapping candidate',
            start_at=start_at,
            end_at=start_at + timedelta(days=1),
            is_all_day=True,
            event_type='exam',
            metadata_json={
                'parser': 'ocr_timetable_grid',
                'parser_type': 'timetable_grid',
                'raw_title': '?? ??',
                'date_mapping_source': 'ocr_header',
                'track': 'embedded_robot',
            },
        )
        grid_debug = types.SimpleNamespace(metadata_json={}, review_required_candidates=[], as_dict=lambda: {})

        with patch('sync.services.reparse_service.parse_schedule_candidates_with_debug', return_value=([schedule], grid_debug)):
            summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(pk=raw_data.pk))

        raw_data.refresh_from_db()
        self.assertEqual(summary.created_count, 0)
        self.assertEqual(summary.validation_skip_count, 1)
        self.assertEqual(ScheduleEvent.objects.count(), 0)
        self.assertIn('source_title_period_mismatch', raw_data.metadata_json['parser_warnings'])

    def test_monthly_evaluation_notice_creates_all_track_events(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/raw/monthly-exam-all-tracks',
            title='?? ??',
            raw_text='evaluation notice text',
        )
        start_at = timezone.datetime(2026, 5, 26, tzinfo=timezone.get_current_timezone())
        tracks = ['python', 'java_non_major', 'java_major', 'embedded', 'mobile', 'embedded_robot', 'data', 'meister']
        schedules = [
            ParsedSchedule(
                title='????5',
                description='monthly exam',
                start_at=start_at,
                end_at=start_at + timedelta(days=1),
                is_all_day=True,
                event_type='exam',
                metadata_json={
                    'parser': 'evaluation_notice_ocr',
                    'parser_type': 'evaluation_notice',
                    'raw_title': '????5',
                    'date_mapping_source': 'explicit_text_date',
                    'track': track,
                },
            )
            for track in tracks
        ]
        grid_debug = types.SimpleNamespace(metadata_json={}, review_required_candidates=[], as_dict=lambda: {})

        with patch('sync.services.reparse_service.parse_schedule_candidates_with_debug', return_value=(schedules, grid_debug)):
            summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(pk=raw_data.pk))

        self.assertEqual(summary.created_count, 8)
        self.assertEqual(ScheduleEvent.objects.filter(title='????5', event_type='exam').count(), 8)
        self.assertEqual(
            sorted(event.metadata_json['track'] for event in ScheduleEvent.objects.all()),
            sorted(tracks),
        )

    def test_debug_event_id_outputs_source_mapping(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/notices/may-week',
            title='[??] 5? 1?? Data ?? ???',
            raw_text='source',
        )
        start_at = timezone.datetime(2026, 5, 8, tzinfo=timezone.get_current_timezone())
        event = ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='Data) ?? ??',
            start_at=start_at,
            end_at=start_at + timedelta(days=1),
            is_all_day=True,
            event_type='exam',
            source_type='notice',
            source_id=str(raw_data.id),
            metadata_json={
                'source_title': raw_data.title,
                'source_url': raw_data.source_url,
                'raw_title': '?? ??',
                'parser_type': 'timetable_grid',
                'track': 'data',
            },
        )
        output = StringIO()

        call_command('debug_schedule_events', '--event-id', str(event.id), stdout=output)

        value = output.getvalue()
        self.assertIn(f'raw_data_id={raw_data.id}', value)
        self.assertIn('source_url=https://example.com/notices/may-week', value)
        self.assertIn('source_title=[??] 5? 1?? Data ?? ???', value)

    def test_online_week_date_range_expands_to_weekdays_except_holidays(self):
        schedules = parse_schedule_candidates(
            '2026.06.02~06.13 온라인 위크',
            default_title='온라인 위크 공지',
        )

        dates = [schedule.start_at.date().isoformat() for schedule in schedules]
        self.assertEqual(
            dates,
            [
                '2026-06-02',
                '2026-06-04',
                '2026-06-05',
                '2026-06-08',
                '2026-06-09',
                '2026-06-10',
                '2026-06-11',
                '2026-06-12',
            ],
        )
        self.assertTrue(all(schedule.title == '온라인 위크' for schedule in schedules))
        self.assertTrue(all(schedule.metadata_json['date_mapping_source'] == 'explicit_text_date' for schedule in schedules))

    def test_timetable_header_candidates_store_ocr_header_date_mapping(self):
        schedules = parse_schedule_candidates(
            '[OCR_TEXT]\n5월 2주차 시간표',
            default_title='[학습] 5월 2주차 Data 트랙 시간표',
            ocr_boxes=_timetable_date_header_ocr_boxes(),
        )

        dates = [schedule.start_at.date().isoformat() for schedule in schedules]
        self.assertIn('2026-05-11', dates)
        self.assertIn('2026-05-15', dates)
        self.assertTrue(all(schedule.metadata_json['date_mapping_source'] == 'ocr_header' for schedule in schedules))
        self.assertTrue(all(schedule.metadata_json['original_header_date'] for schedule in schedules))

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

    def test_reset_generated_schedule_events_deletes_only_raw_linked_events(self):
        raw_data = _raw_data('https://example.com/raw/generated-reset', 'Generated schedule 2026.05.20')
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='Generated schedule',
            start_at=timezone.datetime(2026, 5, 20, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 5, 21, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='study',
            source_type='notice',
        )
        ScheduleEvent.objects.create(
            title='Manual personal schedule',
            start_at=timezone.datetime(2026, 5, 20, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 5, 21, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='personal',
            source_type='manual',
        )
        output = StringIO()

        call_command('reset_generated_schedule_events', '--confirm', stdout=output)

        self.assertIn('delete_count=1', output.getvalue())
        self.assertIn('confirmed=true', output.getvalue())
        self.assertEqual(list(ScheduleEvent.objects.values_list('title', flat=True)), ['Manual personal schedule'])

    def test_reset_generated_schedule_events_requires_confirm(self):
        raw_data = _raw_data('https://example.com/raw/generated-reset-safe', 'Generated schedule 2026.05.20')
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='Generated schedule',
            start_at=timezone.datetime(2026, 5, 20, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 5, 21, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='study',
            source_type='notice',
        )
        output = StringIO()

        call_command('reset_generated_schedule_events', stdout=output)

        self.assertIn('Reset aborted', output.getvalue())
        self.assertIn('confirmed=false', output.getvalue())
        self.assertEqual(ScheduleEvent.objects.count(), 1)

    def test_reset_generated_schedule_events_dry_run_does_not_delete(self):
        raw_data = _raw_data('https://example.com/raw/generated-reset-dry-run', 'Generated schedule 2026.05.20')
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='Generated schedule',
            start_at=timezone.datetime(2026, 5, 20, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 5, 21, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='study',
            source_type='notice',
        )
        output = StringIO()

        call_command('reset_generated_schedule_events', '--dry-run', stdout=output)

        self.assertIn('target_count=1', output.getvalue())
        self.assertIn('dry_run=true', output.getvalue())
        self.assertEqual(ScheduleEvent.objects.count(), 1)

    def test_seed_korean_holidays_creates_childrens_day_on_may_5(self):
        output = StringIO()

        call_command('seed_korean_holidays', '--year', '2026', stdout=output)

        holiday = ScheduleEvent.objects.get(title='어린이날')
        self.assertEqual(timezone.localdate(holiday.start_at).isoformat(), '2026-05-05')
        self.assertEqual(holiday.event_type, 'holiday')
        self.assertEqual(holiday.source_type, 'seed')
        self.assertIn('created_count=', output.getvalue())

    def test_seed_korean_holidays_uses_requested_year_fixture(self):
        call_command('seed_korean_holidays', '--year', '2027')

        holiday = ScheduleEvent.objects.get(title='어린이날')
        self.assertEqual(timezone.localdate(holiday.start_at).isoformat(), '2027-05-05')
        self.assertTrue(ScheduleEvent.objects.filter(title='설날', start_at__date='2027-02-07').exists())

    def test_seed_korean_holidays_rejects_unsupported_year(self):
        with self.assertRaisesMessage(CommandError, 'Unsupported Korean holiday year: 2028'):
            call_command('seed_korean_holidays', '--year', '2028')

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

    def test_duplicate_academic_rule_updates_changed_image_urls_and_reprocesses_ocr(self):
        source_url = 'https://edu.ssafy.com/edu/board/rule/list.do'
        RawSsafyData.objects.create(
            source_type='academic_rule',
            source_url=source_url,
            title='Old academic rule',
            raw_text='old rule text',
            raw_html='<main><img src="/old-rule.png"></main>',
            metadata_json={'notice_id': 'rule-list', 'image_urls': ['https://edu.ssafy.com/old-rule.png']},
        )
        item = _academic_rule_item(source_url, 'rule-list')
        item['raw_html'] = '<table><tr class="reply"><td><img src="/rule-1.png"><img src="/rule-2.png"></td></tr></table>'
        item['metadata_json']['image_urls'] = [
            'https://edu.ssafy.com/rule-1.png',
            'https://edu.ssafy.com/rule-2.png',
        ]

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[item]):
            with patch(
                'sync.services.import_service.extract_text_from_image_urls',
                return_value={
                    'ocr_text': 'updated academic OCR',
                    'ocr_provider': 'mock',
                    'ocr_status': 'success',
                    'ocr_error': '',
                    'ocr_error_type': '',
                    'ocr_failed_count': 0,
                    'ocr_boxes': [],
                },
            ) as ocr_mock:
                job_log = run_notice_import(mode='ssafy_notice')

        raw_data = RawSsafyData.objects.get(source_type='academic_rule')
        self.assertEqual(RawSsafyData.objects.filter(source_type='academic_rule').count(), 1)
        self.assertEqual(job_log.raw_count, 0)
        self.assertEqual(job_log.skipped_count, 0)
        self.assertEqual(job_log.ocr_processed_count, 2)
        self.assertIn('updated_count=1', job_log.message)
        self.assertEqual(raw_data.metadata_json['image_urls'], item['metadata_json']['image_urls'])
        self.assertIn('[OCR_TEXT]', raw_data.raw_text)
        ocr_mock.assert_called_once_with(item['metadata_json']['image_urls'])

    def test_collect_authenticated_list_skips_failed_detail_pages(self):
        page = _StaticPage('<main><a href="/detail/1">1</a><a href="/detail/2">2</a></main>')

        with patch(
            'sync.services.ssafy_crawler.fetch_authenticated_detail',
            side_effect=[
                _source_item('notice', 'https://example.com/detail/1', 'First notice', 'detail-1'),
                SsafyCrawlerError('detail failed'),
                _source_item('notice', 'https://example.com/detail/3', 'Third notice', 'detail-3'),
            ],
        ):
            items = _collect_authenticated_list(
                page=page,
                list_url='https://example.com/list',
                source_type='notice',
                link_extractor=lambda soup, base_url: [
                    'https://example.com/detail/1',
                    'https://example.com/detail/2',
                    'https://example.com/detail/3',
                ],
            )

        self.assertEqual(len(items), 2)
        self.assertEqual([item['source_url'] for item in items], [
            'https://example.com/detail/1',
            'https://example.com/detail/3',
        ])

    def test_authenticated_documents_continue_after_source_page_failure(self):
        fake_env = {
            'SSAFY_LOGIN_URL': 'https://example.com/login',
            'SSAFY_ID': 'tester',
            'SSAFY_PASSWORD': 'secret',
            'SSAFY_NOTICE_LIST_URL': 'https://example.com/list/notice',
            'SSAFY_FAQ_LIST_URL': 'https://example.com/list/faq',
            'SSAFY_QUEST_LIST_URL': 'https://example.com/list/quest',
        }

        with patch.dict('os.environ', fake_env, clear=True):
            with patch.dict('sys.modules', _fake_playwright_modules()):
                with patch(
                    'sync.services.ssafy_crawler._collect_authenticated_list',
                    side_effect=[
                        [_source_item('notice', 'https://example.com/notice/1', 'Notice 1', 'notice-1')],
                        SsafyCrawlerError('faq page failed'),
                        [_source_item('quest', 'https://example.com/quest/1', 'Quest 1', 'quest-1')],
                    ],
                ):
                    items = load_ssafy_authenticated_documents()

        self.assertEqual([item['source_type'] for item in items], ['notice', 'quest'])
        self.assertEqual([item['source_url'] for item in items], [
            'https://example.com/notice/1',
            'https://example.com/quest/1',
        ])

    def test_authenticated_documents_records_skipped_sources_without_urls(self):
        fake_env = {
            'SSAFY_LOGIN_URL': 'https://example.com/login',
            'SSAFY_ID': 'tester',
            'SSAFY_PASSWORD': 'secret',
            'SSAFY_NOTICE_LIST_URL': 'https://example.com/list/notice',
        }

        with patch.dict('os.environ', fake_env, clear=True):
            with patch.dict('sys.modules', _fake_playwright_modules()):
                with patch(
                    'sync.services.ssafy_crawler._collect_authenticated_list',
                    return_value=[
                        _source_item('notice', 'https://example.com/notice/1', 'Notice 1', 'notice-1')
                    ],
                ):
                    items = load_ssafy_authenticated_documents()

        debug_messages = get_last_collection_debug()
        self.assertEqual([item['source_type'] for item in items], ['notice'])
        self.assertTrue(any(message.startswith('skipped_source=faq reason=missing_url') for message in debug_messages))
        self.assertTrue(any(message.startswith('skipped_source=quest reason=missing_url') for message in debug_messages))
        self.assertTrue(
            any(message.startswith('skipped_source=mentoring_notice reason=missing_url') for message in debug_messages)
        )
        self.assertTrue(
            any(message.startswith('skipped_source=curriculum reason=missing_url') for message in debug_messages)
        )
        self.assertTrue(
            any(message.startswith('skipped_source=learning_material reason=missing_url') for message in debug_messages)
        )

    def test_authenticated_documents_uses_mentoring_list_url_env(self):
        fake_env = {
            'SSAFY_LOGIN_URL': 'https://example.com/login',
            'SSAFY_ID': 'tester',
            'SSAFY_PASSWORD': 'secret',
            'SSAFY_NOTICE_LIST_URL': 'https://example.com/list/notice',
            'SSAFY_MENTORING_LIST_URL': 'https://example.com/list/mentoring',
        }

        with patch.dict('os.environ', fake_env, clear=True):
            with patch.dict('sys.modules', _fake_playwright_modules()):
                with patch(
                    'sync.services.ssafy_crawler._collect_authenticated_list',
                    side_effect=[
                        [_source_item('notice', 'https://example.com/notice/1', 'Notice 1', 'notice-1')],
                        [
                            _source_item(
                                'mentoring_notice',
                                'https://example.com/mentoring/1',
                                'Mentoring notice',
                                'mentoring-1',
                            )
                        ],
                    ],
                ) as collect:
                    items = load_ssafy_authenticated_documents()

        self.assertEqual([item['source_type'] for item in items], ['notice', 'mentoring_notice'])
        self.assertEqual(collect.call_args_list[1].kwargs['list_url'], 'https://example.com/list/mentoring')

    def test_collect_authenticated_list_raises_session_expired_for_login_page(self):
        page = _StaticPage(
            '<html><head><title>로그인</title></head><body><input name="userId"><input name="userPwd"></body></html>',
            url='https://example.com/login',
            title='로그인',
        )

        with self.assertRaises(SsafySessionExpiredError):
            _collect_authenticated_list(
                page=page,
                list_url='https://example.com/list/notice',
                source_type='notice',
                link_extractor=lambda soup, base_url: [],
                login_url='https://example.com/login',
            )

        self.assertTrue(any('error_reason=session_expired' in message for message in get_last_collection_debug()))

    def test_collect_authenticated_list_saves_debug_when_links_not_found(self):
        page = _StaticPage('<html><head><title>FAQ</title></head><main>No rows</main></html>')

        with patch(
            'sync.services.ssafy_crawler._save_crawler_debug_page',
            return_value={
                'title': 'FAQ',
                'url': 'https://example.com/list/faq',
                'html_path': 'tmp/ssafy_crawler_debug/faq.html',
                'screenshot_path': '',
            },
        ) as save_debug:
            with self.assertRaises(SsafyCrawlerError):
                _collect_authenticated_list(
                    page=page,
                    list_url='https://example.com/list/faq',
                    source_type='faq',
                    link_extractor=lambda soup, base_url: [],
                )

        save_debug.assert_called_once()

    def test_new_source_types_are_saved_to_raw_data(self):
        items = [
            _source_item('notice', 'https://example.com/notices/1', 'Notice 1', 'notice-1', 'SSAFY 일정 2026.05.20'),
            _source_item('faq', 'https://example.com/faqs/1', 'FAQ 1', 'faq-1', '자주 묻는 질문'),
            _source_item('mentoring_notice', 'https://example.com/mentor/1', 'Mentoring 1', 'mentor-1', '멘토링 공지'),
            _source_item('learning_material', 'https://example.com/material/1', 'Material 1', 'material-1', '학습자료 안내'),
        ]

        with patch('sync.services.import_service.load_notices_by_mode', return_value=items):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(job_log.raw_count, 4)
        self.assertEqual(job_log.event_count, 1)
        self.assertEqual(RawSsafyData.objects.filter(source_type='notice').count(), 1)
        self.assertEqual(RawSsafyData.objects.filter(source_type='faq').count(), 1)
        self.assertEqual(RawSsafyData.objects.filter(source_type='mentoring_notice').count(), 1)
        self.assertEqual(RawSsafyData.objects.filter(source_type='learning_material').count(), 1)
        self.assertEqual(ScheduleEvent.objects.count(), 1)

    def test_placeholder_notice_item_is_excluded(self):
        items = [
            _source_item('notice', 'https://example.com/notices/menu', '목록', 'notice-menu', 'HOME\nCopyright'),
            _notice_item('https://example.com/notices/ok', 'notice-ok'),
        ]

        with patch('sync.services.import_service.load_notices_by_mode', return_value=items):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(RawSsafyData.objects.count(), 1)
        self.assertEqual(RawSsafyData.objects.get().source_url, 'https://example.com/notices/ok')
        self.assertIn('excluded_count=1', job_log.message)

    def test_evaluation_notice_import_stores_exam_metadata(self):
        item = _notice_item('https://example.com/notices/eval', 'eval-1')
        item['title'] = '1학기 과목월말평가 안내'
        item['raw_text'] = '마이스터고 과목평가 월말평가'
        item['raw_html'] = '<img src="/eval.png" alt="과목월말평가 안내">'

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[item]):
            run_notice_import(mode='ssafy_notice')

        raw_data = RawSsafyData.objects.get()
        self.assertEqual(raw_data.metadata_json['category'], 'exam')
        self.assertEqual(raw_data.metadata_json['document_type'], 'evaluation_notice')
        self.assertTrue(raw_data.metadata_json['ocr_ready'])

    def test_excluded_like_evaluation_notice_keyword_item_is_saved(self):
        item = _notice_item('https://example.com/notices/eval-short', 'eval-short')
        item['title'] = 'SSAFY document'
        item['raw_text'] = '월말평가'
        item['raw_html'] = ''

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[item]):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(RawSsafyData.objects.count(), 1)
        raw_data = RawSsafyData.objects.get()
        self.assertEqual(raw_data.metadata_json['document_type'], 'evaluation_notice')
        self.assertIn('keyword_candidate_count=1', job_log.message)
        self.assertIn('saved_evaluation_notice_count=1', job_log.message)

    def test_image_only_evaluation_notice_candidate_is_saved(self):
        item = _notice_item('https://example.com/notices/eval-image', 'eval-image')
        item['title'] = 'SSAFY document'
        item['raw_text'] = ''
        item['raw_html'] = '<img src="https://example.com/eval.png" alt="과목월말평가 안내">'

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[item]):
            job_log = run_notice_import(mode='ssafy_notice')

        raw_data = RawSsafyData.objects.get()
        self.assertEqual(raw_data.metadata_json['document_type'], 'evaluation_notice')
        self.assertEqual(raw_data.metadata_json['image_urls'], ['https://example.com/eval.png'])
        self.assertIn('keyword_candidates_by_source=notice:1', job_log.message)

    def test_tenth_evaluation_notice_import_stores_exam_metadata(self):
        item = _notice_item('https://example.com/notices/eval-10', 'eval-10')
        item['title'] = '[평가] 10회차 과목 5회차 월말평가 안내'
        item['raw_text'] = '과목평가 월말평가 평가 안내'
        item['raw_html'] = '<img src="/eval-10.png" alt="10회차 과목 5회차 월말평가 안내">'

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[item]):
            job_log = run_notice_import(mode='ssafy_notice')

        raw_data = RawSsafyData.objects.get()
        self.assertEqual(raw_data.metadata_json['category'], 'exam')
        self.assertEqual(raw_data.metadata_json['document_type'], 'evaluation_notice')
        self.assertTrue(raw_data.metadata_json['ocr_ready'])
        self.assertIn('target_evaluation_10th_found=true', job_log.message)

    def test_image_url_item_with_short_content_is_saved(self):
        item = _notice_item('https://example.com/notices/image-only', 'image-only')
        item['title'] = '이미지 공지'
        item['raw_text'] = ''
        item['raw_html'] = '<img src="https://example.com/body.png" alt="notice image">'

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[item]):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(RawSsafyData.objects.count(), 1)
        self.assertIn('image_found_count=1', job_log.message)

    def test_empty_detail_item_is_excluded(self):
        item = {
            'source_type': 'notice',
            'source_url': 'https://example.com/notices/empty',
            'title': '',
            'raw_text': '',
            'raw_html': '',
            'metadata_json': {'notice_id': 'empty'},
        }

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[item]):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(RawSsafyData.objects.count(), 0)
        self.assertIn('no_title_no_content_no_image', job_log.message)

    def test_source_keyword_candidate_counts_are_reported(self):
        items = [
            _source_item('notice', 'https://example.com/notices/eval', '월말평가 안내', 'eval-1', '평가 안내'),
            _source_item('quest', 'https://example.com/quest/eval', '과목평가 안내', 'quest-eval', '평가 안내'),
        ]

        with patch('sync.services.import_service.load_notices_by_mode', return_value=items):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertIn('keyword_candidate_count=2', job_log.message)
        self.assertIn('keyword_candidates_by_source=notice:1|quest:1', job_log.message)
        self.assertIn('saved_evaluation_notice_count=2', job_log.message)

    def test_raw_data_api_filters_exam_category(self):
        RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://example.com/notices/eval',
            title='1학기 과목월말평가 안내',
            raw_text='evaluation',
            metadata_json={'category': 'exam', 'document_type': 'evaluation_notice'},
        )
        RawSsafyData.objects.create(source_type='notice', title='일반 공지', raw_text='notice', metadata_json={'category': 'etc'})

        response = self.client.get(reverse('sync-raw-data-list'), {'category': 'exam'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]['document_type'], 'evaluation_notice')

    def test_different_source_urls_with_same_generic_title_are_not_deduped(self):
        items = [
            _source_item('mentoring_notice', 'https://example.com/mentor/1', '멘토 스토리 상세', 'mentor-1'),
            _source_item('mentoring_notice', 'https://example.com/mentor/2', '멘토 스토리 상세', 'mentor-2'),
        ]

        with patch('sync.services.import_service.load_notices_by_mode', return_value=items):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(job_log.raw_count, 2)
        self.assertEqual(job_log.skipped_count, 0)
        self.assertEqual(RawSsafyData.objects.filter(source_type='mentoring_notice').count(), 2)

    def test_same_title_different_brd_item_sequence_is_not_deduped(self):
        items = [
            _source_item(
                'notice',
                'https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=1001',
                'Same title',
                '1001',
            ),
            _source_item(
                'notice',
                'https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=1002',
                'Same title',
                '1002',
            ),
        ]

        with patch('sync.services.import_service.load_notices_by_mode', return_value=items):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(job_log.raw_count, 2)
        self.assertIn('unique_brdItmSeq_count=2', job_log.message)
        self.assertEqual(RawSsafyData.objects.filter(title='Same title').count(), 2)

    def test_expected_count_gap_and_source_counts_are_logged(self):
        RawSsafyData.objects.create(source_type='notice', title='existing', raw_text='x')
        items = [_source_item('notice', 'https://example.com/notices/new', 'new', 'new')]

        with patch.dict('os.environ', {'SSAFY_NOTICE_EXPECTED_COUNT': '3'}):
            with patch('sync.services.import_service.load_notices_by_mode', return_value=items):
                job_log = run_notice_import(mode='ssafy_notice')

        self.assertIn('raw_notice_count=2', job_log.message)
        self.assertIn('raw_all_notice_like_count=2', job_log.message)
        self.assertIn('source_type_counts=notice:2', job_log.message)
        self.assertIn('target_visible_count=3', job_log.message)
        self.assertIn('inaccessible_or_unknown_gap=1', job_log.message)

    def test_session_expired_job_partial_success_without_dropping_collected_items(self):
        collected_item = _source_item('notice', 'https://example.com/notices/1', 'Notice 1', 'notice-1')

        with patch(
            'sync.services.import_service.load_notices_by_mode',
            side_effect=SsafySessionExpiredError('session_expired source_type=mentoring_notice', [collected_item]),
        ):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(job_log.status, CrawlJobLog.STATUS_PARTIAL_SUCCESS)
        self.assertIn('session_expired', job_log.message)
        self.assertIn('source_results=', job_log.message)
        self.assertIn('mentoring_notice:failed', job_log.message)
        self.assertEqual(job_log.raw_count, 1)
        self.assertEqual(RawSsafyData.objects.filter(source_url='https://example.com/notices/1').count(), 1)

    def test_session_expired_without_collected_items_fails(self):
        with patch(
            'sync.services.import_service.load_notices_by_mode',
            side_effect=SsafySessionExpiredError('session_expired source_type=notice', []),
        ):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(job_log.status, CrawlJobLog.STATUS_FAILED)
        self.assertIn('session_expired', job_log.message)
        self.assertEqual(RawSsafyData.objects.count(), 0)

    def test_success_job_records_source_summary(self):
        item = _source_item('notice', 'https://example.com/notices/source-summary', 'Notice', 'source-summary')

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[item]):
            job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(job_log.status, CrawlJobLog.STATUS_SUCCESS)
        self.assertIn('source_results=', job_log.message)
        self.assertIn('notice:success', job_log.message)

    def test_success_job_combines_source_timing_with_import_counts(self):
        item = _source_item('notice', 'https://example.com/notices/source-run', 'Notice', 'source-run')
        crawler_debug = [
            'source_run_end source_type=notice started_at=2026-05-22T10:00:00+09:00 '
            'ended_at=2026-05-22T10:00:02+09:00 elapsed_seconds=2.125 status=success '
            'collected_count=1 saved_count=pending skipped_count=pending error_count=0',
        ]

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[item]):
            with patch('sync.services.import_service.get_last_collection_debug', return_value=crawler_debug):
                job_log = run_notice_import(mode='ssafy_notice')

        self.assertIn('source_run_logs=', job_log.message)
        self.assertIn('source_type=notice:started_at=2026-05-22T10:00:00+09:00', job_log.message)
        self.assertIn('saved_count=1:updated_count=0:skipped_count=0:error_count=0', job_log.message)

    def test_failed_source_debug_marks_partial_success(self):
        item = _source_item('notice', 'https://example.com/notices/source-timeout', 'Notice', 'source-timeout')

        with patch('sync.services.import_service.load_notices_by_mode', return_value=[item]):
            with patch(
                'sync.services.import_service.get_last_collection_debug',
                return_value=['failed_source=mentoring_notice url=https://example.com error_reason=timeout error=source_timeout'],
            ):
                job_log = run_notice_import(mode='ssafy_notice')

        self.assertEqual(job_log.status, CrawlJobLog.STATUS_PARTIAL_SUCCESS)
        self.assertIn('mentoring_notice:failed', job_log.message)
        self.assertIn('error_type=timeout', job_log.message)

    def test_source_filter_and_skip_source_env(self):
        with patch.dict('os.environ', {'SSAFY_CRAWLER_SOURCES': 'notice,academic_rule'}, clear=False):
            specs = _source_collection_specs('notice-url', 'rule-url', 'faq-url', '', '', '', '', '')
        self.assertEqual([source for source, _, _ in specs], ['notice', 'academic_rule'])

        with patch.dict('os.environ', {'SSAFY_CRAWLER_SKIP_SOURCES': 'mentoring_notice'}, clear=False):
            specs = _source_collection_specs('notice-url', 'rule-url', '', '', 'mentor-url', '', '', '')
        self.assertNotIn('mentoring_notice', [source for source, _, _ in specs])

    def test_crawl_command_sets_source_options(self):
        seen = {}

        def fake_run_notice_import(mode=None):
            import os
            seen['sources'] = os.environ.get('SSAFY_CRAWLER_SOURCES')
            seen['max_pages'] = os.environ.get('SSAFY_NOTICE_MAX_PAGES')
            seen['source_timeout'] = os.environ.get('SSAFY_SOURCE_TIMEOUT')
            return CrawlJobLog.objects.create(status=CrawlJobLog.STATUS_SUCCESS, message='ok', crawler_mode=mode or '')

        with patch('sync.management.commands.crawl_ssafy_notices.run_notice_import', side_effect=fake_run_notice_import):
            call_command('crawl_ssafy_notices', source='notice', max_pages=30, timeout=12, stdout=StringIO())

        self.assertEqual(seen['sources'], 'notice')
        self.assertEqual(seen['max_pages'], '30')
        self.assertEqual(seen['source_timeout'], '12')

    def test_crawl_command_prints_source_run_logs(self):
        output = StringIO()
        message = (
            'ok, source_run_logs=source_type=academic_rule:started_at=2026-05-22T10:00:00+09:00:'
            'ended_at=2026-05-22T10:00:01+09:00:elapsed_seconds=1.000:status=success:'
            'collected_count=1:saved_count=0:skipped_count=1:error_count=0:error=-'
        )

        with patch(
            'sync.management.commands.crawl_ssafy_notices.run_notice_import',
            return_value=CrawlJobLog.objects.create(status=CrawlJobLog.STATUS_SUCCESS, message=message),
        ):
            call_command('crawl_ssafy_notices', stdout=output)

        self.assertIn('Source run logs:', output.getvalue())
        self.assertIn('source_type=academic_rule', output.getvalue())

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
        self.assertFalse(any(schedule.event_type == 'holiday' for schedule in schedules))
        self.assertNotIn('어린이날', titles)
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

    def test_timetable_grid_uses_cell_text_instead_of_notice_title(self):
        source_title = '[학습] 5월 2주차 Data 트랙 시간표'

        schedules, grid_debug = parse_schedule_candidates_with_debug(
            '[OCR_TEXT]\n5월\n11\n12\n13\n14\n15\nPandas 실습',
            default_title=source_title,
            ocr_boxes=_timetable_ocr_boxes(),
        )

        self.assertEqual([schedule.title for schedule in schedules], ['[학습] Pandas 실습'])
        self.assertEqual(schedules[0].metadata_json['source_title'], source_title)
        self.assertEqual(schedules[0].metadata_json['parser'], 'ocr_timetable_grid')
        self.assertEqual(schedules[0].metadata_json['parser_type'], 'timetable_grid')
        self.assertEqual(schedules[0].metadata_json['raw_title'], 'Pandas 실습')
        self.assertEqual(schedules[0].metadata_json['display_title'], 'Pandas 실습')
        self.assertEqual(schedules[0].metadata_json['category_label'], '학습')
        self.assertEqual(schedules[0].metadata_json['track'], 'Data')
        self.assertIn(source_title, schedules[0].description)
        self.assertEqual(schedules[0].start_at.date().isoformat(), '2026-05-12')
        self.assertEqual(grid_debug.candidates[0]['source_text'], 'Pandas 실습')

    def test_timetable_grid_normalizes_learning_titles(self):
        source_title = '[학습] 5월 2주차 Data 트랙 시간표'

        schedules, _grid_debug = parse_schedule_candidates_with_debug(
            '[OCR_TEXT]\n5월\n11\n12\n13\n14\n15\nDjango DRF',
            default_title=source_title,
            ocr_boxes=_timetable_multiline_ocr_boxes(),
        )

        self.assertEqual([schedule.title for schedule in schedules], ['[학습] Django: DRF 1'])

    def test_timetable_grid_does_not_prefix_clear_non_learning_items(self):
        source_title = '[학습] 5월 2주차 마이스터고 트랙 시간표'

        schedules, _grid_debug = parse_schedule_candidates_with_debug(
            '[OCR_TEXT]\n5월\n11\n12\n13\n14\n15\n중식\n과목평가',
            default_title=source_title,
            ocr_boxes=_timetable_non_learning_ocr_boxes(),
        )

        self.assertEqual([schedule.title for schedule in schedules], ['과목평가'])

    def test_timetable_grid_skips_time_lunch_and_slogan_text(self):
        schedules, _grid_debug = parse_schedule_candidates_with_debug(
            '[OCR_TEXT]\n5월\n11\n12\n13\n12:00 13:00\n중식\nLunch\nThere is no change',
            default_title='[학습] 5월 2주차 Data 트랙 시간표',
            ocr_boxes=_timetable_noise_ocr_boxes(),
        )

        self.assertEqual(schedules, [])

    def test_timetable_grid_removes_time_prefix_and_keeps_class_title(self):
        schedules, _grid_debug = parse_schedule_candidates_with_debug(
            '[OCR_TEXT]\n5월\n11\n12\n9: 00 10: 00 [ Live 방송 ] JS Basic Syntax1',
            default_title='[학습] 5월 2주차 Data 트랙 시간표',
            ocr_boxes=_timetable_time_prefixed_class_ocr_boxes(),
        )

        self.assertEqual([schedule.title for schedule in schedules], ['[학습] JS: Basic Syntax1'])

    def test_timetable_grid_uses_date_header_boxes_for_mapping(self):
        schedules, _grid_debug = parse_schedule_candidates_with_debug(
            '[OCR_TEXT]\n5월 11일\n5월 12일\n5월 13일\n5월 14일\n5월 15일\nDjango DRF',
            default_title='[학습] 5월 2주차 Data 트랙 시간표',
            ocr_boxes=_timetable_date_header_ocr_boxes(),
        )

        self.assertEqual(
            [(schedule.start_at.date().isoformat(), schedule.title) for schedule in schedules],
            [
                ('2026-05-11', '[학습] Django: DRF 1'),
                ('2026-05-12', '[학습] Django: DRF 2'),
                ('2026-05-13', '[학습] JS: DOM'),
                ('2026-05-14', '[학습] JS: Basic Syntax 1'),
                ('2026-05-15', '과목평가 9'),
            ],
        )

    def test_timetable_grid_keeps_existing_practice_marker(self):
        schedules, _grid_debug = parse_schedule_candidates_with_debug(
            '[OCR_TEXT]\n5월\n11\n12\n13\n[실습 및 Q&A] Django',
            default_title='[학습] 5월 2주차 Data 트랙 시간표',
            ocr_boxes=_timetable_practice_marker_ocr_boxes(),
        )

        self.assertEqual([schedule.title for schedule in schedules], ['[실습 및 Q&A] Django'])

    def test_timetable_grid_rejects_notice_title_as_cell_title(self):
        source_title = '[학습] 5월 2주차 Data 트랙 시간표'
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            title=source_title,
            raw_text='[OCR_TEXT]\n5월\n11\n12\n13',
            ocr_boxes=_timetable_notice_title_ocr_boxes(source_title),
        )

        summary = reparse_raw_data_to_events(RawSsafyData.objects.filter(pk=raw_data.pk))

        raw_data.refresh_from_db()
        self.assertEqual(summary.created_count, 0)
        self.assertEqual(summary.skipped_count, 1)
        self.assertEqual(ScheduleEvent.objects.count(), 0)
        self.assertIn('timetable_title_equals_source_title', raw_data.metadata_json['parser_warnings'])

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

    def test_parser_splits_subject_and_monthly_exam_on_same_date(self):
        schedules = parse_schedule_candidates(
            '[OCR_TEXT]\n1월\n20\n과목평가1/ 월말평가1',
            default_title='[학습] 15기 1학기 전체 일정',
            ocr_boxes=_combined_exam_ocr_boxes(),
        )
        titles = [schedule.title for schedule in schedules]

        self.assertIn('과목평가1', titles)
        self.assertIn('월말평가1', titles)
        self.assertEqual(len([schedule for schedule in schedules if schedule.start_at.date().isoformat() == '2026-01-20']), 2)

    def test_parser_creates_meister_evaluation_notice_march_exams_only(self):
        raw_text = '''
        [OCR_TEXT]
        15기 1학기 평가 안내
        마이스터고 트랙
        3월 3일 월말평가 알고리즘 기본
        3월 16일 과목평가 알고리즘 응용
        3월 26일 과목평가 AI
        '''

        schedules, grid_debug = parse_schedule_candidates_with_debug(raw_text, default_title='평가 안내')
        result = [(schedule.start_at.date().isoformat(), schedule.title) for schedule in schedules]

        self.assertEqual(len(result), 24)
        self.assertEqual(
            sorted(set(result)),
            [
                ('2026-03-03', '월말평가: 알고리즘 기본'),
                ('2026-03-16', '과목평가: 알고리즘 응용'),
                ('2026-03-26', '과목평가: AI'),
            ],
        )
        self.assertEqual(
            sorted({schedule.metadata_json['track_key'] for schedule in schedules}),
            ['data', 'embedded', 'embedded_robot', 'java_major', 'java_non_major', 'meister', 'mobile', 'python'],
        )
        self.assertEqual(grid_debug.metadata_json['track'], '마이스터고')

    def test_generic_four_day_exam_grid_goes_to_review_required(self):
        schedules = parse_schedule_candidates(
            '[OCR_TEXT]\n3월\n2\n3\n4\n5\n과목평가',
            default_title='[학습] 15기 1학기 전체 일정',
            ocr_boxes=_march_exam_run_ocr_boxes(day_count=4),
        )

        self.assertEqual(schedules, [])

    def test_evaluation_notice_without_track_expands_to_all_tracks(self):
        schedules, grid_debug = parse_schedule_candidates_with_debug(
            '[OCR_TEXT]\n\ud3c9\uac00 \uc548\ub0b4\n3\uc6d4 3\uc77c \uc6d4\ub9d0\ud3c9\uac00 \uc54c\uace0\ub9ac\uc998 \uae30\ubcf8',
            default_title='\ud3c9\uac00 \uc548\ub0b4',
        )

        self.assertEqual(len(schedules), 8)
        self.assertEqual(
            sorted({schedule.metadata_json['track'] for schedule in schedules}),
            ['Data', 'Embedded', 'Embedded Robot', 'Java\ube44\uc804\uacf5', 'Java\uc804\uacf5', 'Mobile', 'Python', '\ub9c8\uc774\uc2a4\ud130\uace0'],
        )
        self.assertEqual(grid_debug.review_required_candidate_count, 0)
        self.assertEqual(grid_debug.metadata_json['track'], 'all')

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

    def test_pagination_next_url_uses_page_no(self):
        soup = BeautifulSoup(
            '''
            <a href="#;" onclick="fnPage('2')">2</a>
            <a href="#;" onclick="fnPage('3')">3</a>
            ''',
            'html.parser',
        )

        next_url = _extract_next_page_url(
            soup,
            'https://edu.ssafy.com/edu/board/docReq/list.do?pageNo=1',
            {'https://edu.ssafy.com/edu/board/docReq/list.do?pageNo=1'},
        )

        self.assertEqual(next_url, 'https://edu.ssafy.com/edu/board/docReq/list.do?pageNo=2')

    def test_pagination_repeated_html_is_reported_as_failed(self):
        page = _StaticPage(
            '''
            <table><tbody>
              <tr><td><a href="#;" onclick="fnDetail('1')">공지 1</a></td></tr>
            </tbody></table>
            <a href="#;" onclick="fnPage('2')">2</a>
            '''
        )

        with patch.dict('os.environ', {'SSAFY_NOTICE_MAX_PAGES': '3'}):
            with patch(
                'sync.services.ssafy_crawler.fetch_authenticated_detail',
                return_value=_source_item('notice', 'https://example.com/detail/1', '공지 1', '1'),
            ):
                _collect_authenticated_list(
                    page,
                    'https://edu.ssafy.com/edu/board/notice/list.do',
                    'notice',
                    _extract_notice_links,
                )

        self.assertTrue(any('pagination_failed source_type=notice' in message for message in get_last_collection_debug()))

    def test_pagination_debug_reports_hidden_inputs_and_functions(self):
        soup = BeautifulSoup(
            '''
            <form name="searchForm">
              <input type="hidden" name="pageIndex" value="1">
              <input name="searchKeyword" value="">
            </form>
            <script>function linkPage(pageNo) {}</script>
            ''',
            'html.parser',
        )

        debug = _pagination_controls_debug(soup)

        self.assertIn('pageIndex', debug)
        self.assertIn('searchKeyword', debug)
        self.assertIn('linkPage', debug)

    def test_filter_controls_debug_reports_form_select_and_tabs(self):
        soup = BeautifulSoup(
            '''
            <form name="searchForm" method="post" action="/notice/list.do">
              <input type="hidden" name="pageIndex" value="1">
              <input type="hidden" name="searchBrdItmCdVal" value="NOTICE">
              <select name="searchCondition">
                <option value="">전체</option>
                <option value="title">제목</option>
              </select>
              <button>검색</button>
            </form>
            <a href="#;" onclick="changeTab('exam')">평가</a>
            ''',
            'html.parser',
        )

        debug = _filter_controls_debug(soup)

        self.assertIn('searchForm:post:/notice/list.do', debug)
        self.assertIn('searchBrdItmCdVal=NOTICE', debug)
        self.assertIn('searchCondition', debug)
        self.assertIn('평가', debug)

    def test_pagination_total_count_is_extracted(self):
        soup = BeautifulSoup(
            '''
            <div class="total">총 193건</div>
            <table><tbody><tr><td>1</td></tr><tr><td>2</td></tr></tbody></table>
            <a href="#;" onclick="fnPage('19')">19</a>
            <a href="#;" onclick="fnPage('20')">20</a>
            ''',
            'html.parser',
        )

        totals = _extract_pagination_totals(soup)

        self.assertEqual(totals['total_notice_count'], 193)
        self.assertEqual(totals['last_page'], 20)
        self.assertEqual(totals['page_size'], 2)

    def test_pagination_page_number_ignores_detail_ids(self):
        soup = BeautifulSoup(
            '''
            <a href="/edu/board/notice/detail.do?brdItmSeq=1541431">detail</a>
            <a href="#;" onclick="fnPage('2')">2</a>
            ''',
            'html.parser',
        )

        self.assertEqual(_extract_next_page_url(soup, 'https://edu.ssafy.com/edu/board/notice/list.do', set()), 'https://edu.ssafy.com/edu/board/notice/list.do?pageNo=2')

    def test_link_extractor_supports_fn_detail2_onclick(self):
        soup = BeautifulSoup(
            '''
            <a href="#;" onclick="fnDetail2('119629','NOW');">Mentoring detail</a>
            ''',
            'html.parser',
        )

        links = _extract_notice_links(soup, 'https://edu.ssafy.com/edu/board/mentoState/list.do')

        self.assertEqual(
            links,
            ['https://edu.ssafy.com/edu/board/mentoState/detail.do?brdItmSeq=119629'],
        )

    def test_notice_links_use_brd_item_sequence_as_unique_source_url(self):
        soup = BeautifulSoup(
            '''
            <a href="#;" onclick="fnDetail('101');">공지 A</a>
            <a href="#;" onclick="fnDetail('102');">공지 B</a>
            ''',
            'html.parser',
        )

        links = _extract_notice_links(soup, 'https://edu.ssafy.com/edu/board/notice/list.do')

        self.assertEqual(
            links,
            [
                'https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=101',
                'https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=102',
            ],
        )

    def test_detail_onclick_parser_supports_go_detail_and_location(self):
        self.assertEqual(
            _extract_detail_url_from_onclick("goDetail('12345')", 'https://edu.ssafy.com/edu/board/notice/list.do'),
            'https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=12345',
        )
        self.assertEqual(
            _extract_detail_url_from_onclick(
                "location.href='/edu/board/notice/detail.do?brdItmSeq=54321'",
                'https://edu.ssafy.com/edu/board/notice/list.do',
            ),
            'https://edu.ssafy.com/edu/board/notice/detail.do?brdItmSeq=54321',
        )

    def test_detail_parser_extracts_real_content_without_menu(self):
        soup = BeautifulSoup(
            '''
            <html><body>
              <nav>HOME Copyright 메뉴</nav>
              <div class="view_content">
                <h1>공지 제목</h1>
                <p>실제 본문입니다. 평가와 무관한 일반 상세 내용입니다.</p>
              </div>
              <footer>Copyright SSAFY</footer>
            </body></html>
            ''',
            'html.parser',
        )

        item = _parse_detail_soup(soup, 'https://example.com/detail.do?brdItmSeq=1', 'notice')

        self.assertIn('실제 본문입니다', item['raw_text'])
        self.assertNotIn('Copyright', item['raw_text'])
        self.assertTrue(item['metadata_json']['real_content'])

    def test_detail_parser_body_fallback_removes_menu_text(self):
        soup = BeautifulSoup(
            '''
            <html><body>
              <header>HOME 메뉴 Copyright</header>
              <section><p>본문 fallback 내용입니다. 일정 설명 본문입니다.</p></section>
              <footer>Copyright</footer>
            </body></html>
            ''',
            'html.parser',
        )

        item = _parse_detail_soup(soup, 'https://example.com/detail.do?brdItmSeq=2', 'notice')

        self.assertIn('본문 fallback 내용입니다', item['raw_text'])
        self.assertNotIn('HOME', item['raw_text'])

    def test_detail_debug_html_can_be_saved(self):
        soup = BeautifulSoup('<html><body><div class="view_content">debug body</div></body></html>', 'html.parser')
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict('os.environ', {'SSAFY_CRAWLER_DEBUG_HTML': 'true'}):
                with patch('sync.services.ssafy_crawler.DETAIL_DEBUG_DIR', Path(tmp_dir)):
                    _parse_detail_soup(soup, 'https://example.com/detail.do?brdItmSeq=3', 'notice')

            self.assertTrue(list(Path(tmp_dir).glob('notice_*.html')))

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

    def test_extract_image_urls_from_html_collects_lazy_and_background_images(self):
        html = '''
        <article>
            <img src="/upload/notice/a.png">
            <img data-src="/upload/notice/lazy.png">
            <img srcset="/upload/notice/srcset.png 1x, /upload/notice/a.png 2x">
            <img data-srcset="images/month.png 640w">
            <section style="background-image: url('../rules/rule-bg.png')"></section>
            <div style="background: url('/upload/notice/lazy.png') center no-repeat"></div>
        </article>
        '''

        image_urls = extract_image_urls_from_html(
            html,
            'https://edu.ssafy.com/edu/board/rule/list.do',
        )

        self.assertEqual(
            image_urls,
            [
                'https://edu.ssafy.com/upload/notice/a.png',
                'https://edu.ssafy.com/upload/notice/lazy.png',
                'https://edu.ssafy.com/upload/notice/srcset.png',
                'https://edu.ssafy.com/edu/board/rule/images/month.png',
                'https://edu.ssafy.com/edu/board/rules/rule-bg.png',
            ],
        )

    def test_academic_rule_reply_image_urls_collect_six_reply_images_without_page_background(self):
        html = '''
        <main style="background-image: url('/assets/profile-background.png')">
          <img src="/assets/header-logo.jpg">
          <table class="accordian-list">
            <tr class="reply"><td><img src="/rules/attendance.png"></td></tr>
            <tr class="reply"><td><img src="/rules/life.png"></td></tr>
            <tr class="reply"><td><img src="/rules/award.png"></td></tr>
            <tr class="reply"><td><img src="/rules/security.png"></td></tr>
            <tr class="reply"><td><img src="/rules/evaluation.png"></td></tr>
            <tr class="reply"><td><img src="/rules/welfare.png"></td></tr>
          </table>
        </main>
        '''

        image_urls = extract_academic_rule_reply_image_urls_from_html(
            html,
            'https://edu.ssafy.com/edu/board/rule/list.do',
        )

        self.assertEqual(len(image_urls), 6)
        self.assertEqual(image_urls[0], 'https://edu.ssafy.com/rules/attendance.png')
        self.assertEqual(image_urls[-1], 'https://edu.ssafy.com/rules/welfare.png')
        self.assertNotIn('https://edu.ssafy.com/assets/profile-background.png', image_urls)
        self.assertNotIn('https://edu.ssafy.com/assets/header-logo.jpg', image_urls)

    def test_academic_toggle_opener_skips_already_open_buttons(self):
        page = _AcademicTogglePage(
            [
                _AcademicToggle(aria_expanded='false'),
                _AcademicToggle(aria_expanded='true'),
            ]
        )

        toggle_count, opened_toggle_count = _open_academic_toggles(page, 'academic_rule')

        self.assertEqual(toggle_count, 2)
        self.assertEqual(opened_toggle_count, 1)
        self.assertEqual([button.click_count for button in page.buttons], [1, 0])
        self.assertEqual(page.waits, [300])

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
        self.assertEqual(result['ocr_error_type'], 'provider_auth_error')
        self.assertIn('credentials are not configured', result['ocr_error'])
        self.assertNotIn('GOOGLE_APPLICATION_CREDENTIALS=', result['ocr_error'])

    def test_ocr_download_failure_is_classified(self):
        with patch.dict(
            'os.environ',
            {
                'OCR_PROVIDER': 'google_vision',
                'GOOGLE_VISION_ENABLED': 'true',
                'GOOGLE_APPLICATION_CREDENTIALS': 'C:\\fake\\vision.json',
            },
        ):
            with patch.dict('sys.modules', _google_vision_modules('ignored')):
                with patch('sync.services.ocr_service.requests.get', side_effect=RuntimeError('download down')):
                    result = extract_text_from_image_urls(['https://example.com/down.png'])

        self.assertEqual(result['ocr_status'], 'failed')
        self.assertEqual(result['ocr_error_type'], 'image_download_failed')
        self.assertIn('image_download_failed', result['ocr_error'])

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
        self.assertEqual(raw_data.metadata_json['ocr_error_type'], 'image_download_failed')

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
                'ocr_error_type': 'provider_auth_error',
                'ocr_failed_count': 1,
            },
        ):
            call_command('backfill_raw_ocr', '--id', raw_data.id, stdout=output)

        raw_data.refresh_from_db()
        self.assertEqual(raw_data.raw_text, '공지 본문')
        self.assertEqual(raw_data.metadata_json['ocr_status'], 'failed')
        self.assertEqual(raw_data.metadata_json['ocr_error_type'], 'provider_auth_error')
        self.assertIn('ocr_failed_count=1', output.getvalue())

    def test_reprocess_ocr_filters_by_category_and_document_type_dry_run(self):
        RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/eval',
            title='evaluation',
            raw_text='body',
            raw_html='<main><img src="/eval.png"></main>',
            metadata_json={'category': 'exam', 'document_type': 'evaluation_notice'},
        )
        RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/study',
            title='study',
            raw_text='body',
            raw_html='<main><img src="/study.png"></main>',
            metadata_json={'category': 'study'},
        )
        output = StringIO()

        with patch('sync.management.commands.backfill_raw_ocr.extract_text_from_image_urls') as extract_mock:
            call_command(
                'reprocess_ocr',
                '--category=exam',
                '--document-type=evaluation_notice',
                '--limit=20',
                '--dry-run',
                stdout=output,
            )

        extract_mock.assert_not_called()
        self.assertIn('raw_checked=1', output.getvalue())
        self.assertIn('image_count=1', output.getvalue())

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

    def test_reparse_evaluation_exams_replaces_only_linked_exam_events(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/evaluation',
            title='평가 안내',
            raw_text=(
                '[OCR_TEXT]\n'
                '15기 1학기 평가 안내\n'
                '마이스터고 트랙\n'
                '3월 3일 월말평가 알고리즘 기본\n'
                '3월 16일 과목평가 알고리즘 응용\n'
                '3월 26일 과목평가 AI\n'
            ),
        )
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='잘못된 과목평가',
            start_at=timezone.datetime(2026, 3, 1, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 3, 2, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='exam',
            source_type='notice',
            source_id=str(raw_data.id),
        )
        ScheduleEvent.objects.create(
            title='사용자 직접 시험',
            start_at=timezone.datetime(2026, 3, 4, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 3, 5, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='exam',
            source_type='manual',
            source_id='manual',
        )
        output = StringIO()

        call_command('reparse_evaluation_exams', stdout=output)

        titles = list(ScheduleEvent.objects.order_by('start_at').values_list('title', flat=True))
        self.assertNotIn('잘못된 과목평가', titles)
        self.assertIn('사용자 직접 시험', titles)
        self.assertIn('월말평가: 알고리즘 기본', titles)
        self.assertIn('과목평가: 알고리즘 응용', titles)
        self.assertIn('과목평가: AI', titles)
        self.assertIn('deleted_count=1', output.getvalue())
        self.assertIn('created_count=24', output.getvalue())

    def test_reparse_evaluation_exams_aborts_without_evaluation_ocr_raw_data(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/full-calendar',
            title='[학습] 15기 1학기 전체 일정',
            raw_text='[OCR_TEXT]\n3월\n2\n3\n4\n과목평가',
        )
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='기존 시험',
            start_at=timezone.datetime(2026, 3, 2, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 3, 3, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='exam',
            source_type='notice',
            source_id=str(raw_data.id),
        )
        output = StringIO()

        call_command('reparse_evaluation_exams', stdout=output)

        self.assertEqual(ScheduleEvent.objects.filter(title='기존 시험').count(), 1)
        self.assertIn('evaluation_raw_count=0', output.getvalue())
        self.assertIn('deleted_count=0', output.getvalue())
        self.assertIn('abort_reason=no_evaluation_ocr_raw_data', output.getvalue())

    def test_parser_filters_ocr_garbage_titles(self):
        schedules = parse_schedule_candidates(
            '시간 2026.05.20\nViewModel 2026.05.21\nwithout questions 2026.05.22\nLive 방송 2026.05.23',
            default_title='공지사항 상세',
        )

        self.assertEqual(schedules, [])

    def test_parser_filters_promotional_ocr_titles_but_keeps_valid_events(self):
        schedules = parse_schedule_candidates(
            '2026.02.19 운영자\n'
            '2026.02.19 ♥알림신청♥\n'
            '2026.02.23 [싸피티비] 박슬기랑 수다 타임 치킨세트 이벤트\n'
            '2026.02.19 SW역량테스트(IM형/A형)\n'
            '2026.02.23 과목평가3(일타싸피)',
            default_title='공지사항 상세',
        )
        titles = [schedule.title for schedule in schedules]

        self.assertNotIn('운영자', titles)
        self.assertNotIn('♥알림신청♥', titles)
        self.assertFalse(any('싸피티비' in title for title in titles))
        self.assertTrue(any('SW역량테스트' in title for title in titles))
        self.assertTrue(any('과목평가3' in title for title in titles))

    def test_parser_keeps_meaningful_pjt_schedule(self):
        schedules = parse_schedule_candidates('관통 PJT 2026.05.20', default_title='공지사항 상세')

        self.assertEqual(len(schedules), 1)
        self.assertEqual(schedules[0].title, '관통 PJT')

    def test_repair_calendar_events_creates_january_camp_and_keeps_manual_event(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/full-calendar',
            title='15기 1학기 전체 일정',
            raw_text='calendar',
        )
        ScheduleEvent.objects.create(
            title='사용자 직접 일정',
            start_at=timezone.datetime(2026, 1, 7, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 1, 8, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='etc',
            source_type='manual',
        )
        output = StringIO()

        call_command('repair_calendar_events', '--use-manual-fallback', stdout=output)

        self.assertEqual(ScheduleEvent.objects.filter(title='15기 SW AI 캠프').count(), 1)
        self.assertEqual(ScheduleEvent.objects.get(title='15기 SW AI 캠프').raw_data, raw_data)
        self.assertEqual(ScheduleEvent.objects.filter(title='사용자 직접 일정').count(), 1)
        self.assertIn('created_count=', output.getvalue())

    def test_repair_calendar_events_removes_jan15_duplicates_and_jan31_notice(self):
        raw_data = RawSsafyData.objects.create(source_type='notice', title='15기 1학기 전체 일정', raw_text='calendar')
        for title in ['15기 SW AI 스타트 캠프', '공지 안내문', 'ViewModel']:
            ScheduleEvent.objects.create(
                raw_data=raw_data,
                title=title,
                start_at=timezone.datetime(2026, 1, 15, tzinfo=timezone.get_current_timezone()),
                end_at=timezone.datetime(2026, 1, 16, tzinfo=timezone.get_current_timezone()),
                is_all_day=True,
                event_type='etc',
                source_type='notice',
            )
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='1월 31일 공지용 설명 문장',
            start_at=timezone.datetime(2026, 1, 31, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 2, 1, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='notice',
            source_type='notice',
        )

        call_command('repair_calendar_events', '--use-manual-fallback')

        self.assertEqual(
            list(ScheduleEvent.objects.filter(start_at__date='2026-01-15').values_list('title', flat=True)),
            ['15기 SW AI 스타트 캠프'],
        )
        self.assertEqual(ScheduleEvent.objects.filter(start_at__date='2026-01-31').count(), 0)

    def test_repair_calendar_events_normalizes_ai_lecture_and_online_week(self):
        RawSsafyData.objects.create(source_type='notice', title='15기 1학기 전체 일정', raw_text='calendar')

        call_command('repair_calendar_events', '--use-manual-fallback')

        self.assertEqual(ScheduleEvent.objects.filter(title='AI 강의 1', start_at__date='2026-02-24').count(), 1)
        self.assertEqual(ScheduleEvent.objects.filter(title='AI 강의 Ⅱ', start_at__date='2026-03-16').count(), 1)
        self.assertEqual(ScheduleEvent.objects.filter(title='AI 강의 2').count(), 0)
        self.assertEqual(ScheduleEvent.objects.filter(title='AI 강의 Ⅱ', start_at__date='2026-03-21').count(), 0)
        self.assertEqual(ScheduleEvent.objects.filter(title='온라인 위크', start_at__date='2026-06-01').count(), 1)
        self.assertEqual(ScheduleEvent.objects.filter(title='온라인 위크', start_at__date='2026-06-03').count(), 0)

    def test_repair_calendar_events_keeps_only_jan24_ssafy_day(self):
        RawSsafyData.objects.create(source_type='notice', title='15기 1학기 전체 일정', raw_text='calendar')

        call_command('repair_calendar_events', '--use-manual-fallback')
        call_command('repair_calendar_events', '--use-manual-fallback')

        self.assertEqual(ScheduleEvent.objects.filter(title='SSAFY DAY', start_at__date='2026-01-24').count(), 1)
        for day in [25, 26, 27]:
            self.assertEqual(ScheduleEvent.objects.filter(title='SSAFY DAY', start_at__date=f'2026-01-{day}').count(), 0)

    def test_repair_calendar_events_dedupes_ai_lecture_roman_titles(self):
        raw_data = RawSsafyData.objects.create(source_type='notice', title='15기 1학기 전체 일정', raw_text='calendar')
        for title, event_type in [('AI 강의 2', 'study'), ('AI 강의 II', 'lecture'), ('AI 강의 Ⅱ', 'study')]:
            ScheduleEvent.objects.create(
                raw_data=raw_data,
                title=title,
                start_at=timezone.datetime(2026, 3, 16, tzinfo=timezone.get_current_timezone()),
                end_at=timezone.datetime(2026, 3, 17, tzinfo=timezone.get_current_timezone()),
                is_all_day=True,
                event_type=event_type,
                source_type='notice',
            )

        call_command('repair_calendar_events')
        call_command('repair_calendar_events')

        self.assertEqual(ScheduleEvent.objects.filter(title='AI 강의 2').count(), 0)
        self.assertEqual(ScheduleEvent.objects.filter(title='AI 강의 II').count(), 0)
        self.assertEqual(ScheduleEvent.objects.filter(title='AI 강의 Ⅱ', start_at__date='2026-03-16').count(), 1)

    def test_repair_calendar_events_keeps_one_ai_lecture_per_api_date(self):
        raw_data = RawSsafyData.objects.create(source_type='notice', title='15기 1학기 전체 일정', raw_text='calendar')
        for event_type in ['lecture', 'study']:
            ScheduleEvent.objects.create(
                raw_data=raw_data,
                title='AI 강의 Ⅱ',
                start_at=timezone.datetime(2026, 3, 17, 9, tzinfo=timezone.get_current_timezone()),
                end_at=timezone.datetime(2026, 3, 17, 10, tzinfo=timezone.get_current_timezone()),
                is_all_day=False,
                event_type=event_type,
                source_type='notice',
            )

        call_command('repair_calendar_events')

        response = self.client.get('/api/schedules/events/?start=2026-03-01&end=2026-04-02')
        ai_events = [
            event for event in response.json()
            if event['title'] == 'AI 강의 Ⅱ' and event['start_at'].startswith('2026-03-17')
        ]
        self.assertEqual(len(ai_events), 1)

    def test_repair_calendar_events_creates_manual_exam_corrections_without_evaluation_raw(self):
        RawSsafyData.objects.create(source_type='notice', title='15기 1학기 전체 일정', raw_text='calendar')
        output = StringIO()

        call_command('repair_calendar_events', '--dry-run', stdout=output)
        self.assertIn('use_manual_fallback=false', output.getvalue())
        self.assertEqual(ScheduleEvent.objects.filter(event_type='exam').count(), 0)

        call_command('repair_calendar_events', '--use-manual-fallback')
        first_count = ScheduleEvent.objects.filter(event_type='exam').count()
        call_command('repair_calendar_events', '--use-manual-fallback')
        second_count = ScheduleEvent.objects.filter(event_type='exam').count()

        self.assertGreater(first_count, 0)
        self.assertEqual(first_count, second_count)
        self.assertEqual(ScheduleEvent.objects.filter(title='과목평가2', start_at__date='2026-02-09').count(), 1)
        self.assertEqual(ScheduleEvent.objects.filter(title='SW역량테스트(IM형/A형)', start_at__date='2026-02-19').count(), 1)
        self.assertEqual(ScheduleEvent.objects.filter(title='과목평가3(일타싸피)', start_at__date='2026-02-23').count(), 1)
        self.assertEqual(ScheduleEvent.objects.filter(title='AI 강의 1', start_at__date='2026-02-24').count(), 1)
        self.assertEqual(ScheduleEvent.objects.filter(title='AI 강의 1', start_at__date='2026-02-27').count(), 1)
        exam = ScheduleEvent.objects.get(title='과목평가2')
        self.assertEqual(exam.metadata_json['repair_source'], 'manual_exam_correction')
        self.assertEqual(exam.metadata_json['source_reason'], 'evaluation_notice_missing_manual_mvp_seed')

    def test_repair_calendar_events_dedupes_pjt_and_fills_project_focus_days(self):
        raw_data = RawSsafyData.objects.create(source_type='notice', title='15기 1학기 전체 일정', raw_text='calendar')
        for title in ['관통 PJT', '관통PJT']:
            ScheduleEvent.objects.create(
                raw_data=raw_data,
                title=title,
                start_at=timezone.datetime(2026, 5, 22, tzinfo=timezone.get_current_timezone()),
                end_at=timezone.datetime(2026, 5, 23, tzinfo=timezone.get_current_timezone()),
                is_all_day=True,
                event_type='project',
                source_type='notice',
            )

        call_command('repair_calendar_events', '--use-manual-fallback')

        self.assertEqual(ScheduleEvent.objects.filter(title='관통 PJT', start_at__date='2026-05-22').count(), 1)
        for day in [22, 23, 24]:
            self.assertEqual(
                ScheduleEvent.objects.filter(title='관통 프로젝트 집중기간', start_at__date=f'2026-06-{day}').count(),
                1,
            )

    def test_repair_calendar_events_reports_manual_ratio_and_raw_candidates(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='학습 시간표',
            raw_text='학습 시간표',
            metadata_json={'ocr_text_length': 0},
        )
        output = StringIO()

        call_command('repair_calendar_events', '--dry-run', '--use-manual-fallback', stdout=output)

        value = output.getvalue()
        self.assertIn('raw_candidate_count=1', value)
        self.assertIn('manual_correction_ratio=', value)
        self.assertIn('parser_improvement_unavailable', value)
        self.assertIn('use_manual_fallback=true', value)

    def test_repair_calendar_events_ocr_candidate_creates_raw_linked_event(self):
        raw_data = RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/full-calendar',
            title='15기 1학기 전체 일정',
            raw_text='AI 강의 2026.03.16',
            metadata_json={'ocr_text_length': len('AI 강의 2026.03.16')},
        )

        call_command('repair_calendar_events')

        event = ScheduleEvent.objects.get(raw_data=raw_data)
        self.assertEqual(event.source_id, str(raw_data.pk))
        self.assertNotEqual(event.metadata_json.get('repair_source'), 'manual_calendar_correction')

    def test_repair_calendar_events_detects_evaluation_notice_metadata(self):
        RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notices/evaluation',
            title='1학기 과목월말평가 안내',
            raw_text='마이스터고 2026.03.03 월말평가 알고리즘 기본',
            metadata_json={'category': 'exam', 'document_type': 'evaluation_notice', 'ocr_text_length': 20},
        )
        output = StringIO()

        call_command('repair_calendar_events', '--dry-run', stdout=output)

        value = output.getvalue()
        self.assertIn('raw_candidate_count=1', value)
        self.assertIn('missing_evaluation_notice_raw_data=false', value)

    def test_repair_calendar_events_notice_sentence_is_not_created(self):
        RawSsafyData.objects.create(
            source_type='notice',
            title='전체 일정',
            raw_text='공지 안내 문장입니다. 캘린더 일정으로 만들 내용이 아닙니다.',
            metadata_json={'ocr_text_length': 30},
        )

        call_command('repair_calendar_events')

        self.assertEqual(ScheduleEvent.objects.count(), 0)

    def test_repair_calendar_events_february_items_are_in_api_response(self):
        RawSsafyData.objects.create(source_type='notice', title='15기 1학기 전체 일정', raw_text='calendar')

        call_command('repair_calendar_events', '--use-manual-fallback')

        response = self.client.get('/api/schedules/events/?start=2026-02-01&end=2026-03-01')
        titles = {event['title'] for event in response.json()}
        self.assertIn('과목평가2', titles)
        self.assertIn('설날', titles)
        self.assertIn('SW역량테스트(IM형/A형)', titles)
        self.assertIn('과목평가3(일타싸피)', titles)
        self.assertIn('AI 강의 1', titles)

    def test_repair_calendar_events_removes_promotional_ocr_events(self):
        raw_data = RawSsafyData.objects.create(source_type='notice', title='15湲?1?숆린 ?꾩껜 ?쇱젙', raw_text='calendar')
        for title in ['운영자', '♥알림신청♥', '[싸피티비] 박슬기랑 수다 타임', '치킨세트 이벤트 출연']:
            ScheduleEvent.objects.create(
                raw_data=raw_data,
                title=title,
                start_at=timezone.datetime(2026, 2, 23, tzinfo=timezone.get_current_timezone()),
                end_at=timezone.datetime(2026, 2, 24, tzinfo=timezone.get_current_timezone()),
                is_all_day=True,
                event_type='notice',
                source_type='notice',
            )
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='SW역량테스트(IM형/A형)',
            start_at=timezone.datetime(2026, 2, 19, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 2, 20, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='exam',
            source_type='notice',
        )

        call_command('repair_calendar_events')

        titles = set(ScheduleEvent.objects.values_list('title', flat=True))
        self.assertIn('SW역량테스트(IM형/A형)', titles)
        self.assertNotIn('운영자', titles)
        self.assertNotIn('♥알림신청♥', titles)
        self.assertFalse(any('싸피티비' in title or '치킨세트' in title for title in titles))

    def test_repair_calendar_events_removes_meetup_duplicate(self):
        raw_data = RawSsafyData.objects.create(source_type='notice', title='15기 1학기 전체 일정', raw_text='calendar')
        for title in ['밋업', '상반기 밋업']:
            ScheduleEvent.objects.create(
                raw_data=raw_data,
                title=title,
                start_at=timezone.datetime(2026, 4, 10, tzinfo=timezone.get_current_timezone()),
                end_at=timezone.datetime(2026, 4, 11, tzinfo=timezone.get_current_timezone()),
                is_all_day=True,
                event_type='etc',
                source_type='notice',
            )

        call_command('repair_calendar_events')

        self.assertEqual(list(ScheduleEvent.objects.filter(start_at__date='2026-04-10').values_list('title', flat=True)), ['상반기 밋업'])

    def test_repair_calendar_events_corrects_explicit_weekday_mismatch(self):
        raw_data = RawSsafyData.objects.create(source_type='notice', title='학습 시간표', raw_text='화) HashSet / HashMap')
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='화) HashSet / HashMap',
            start_at=timezone.datetime(2026, 5, 20, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 5, 21, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='study',
            source_type='notice',
        )

        output = StringIO()
        call_command('repair_calendar_events', stdout=output)

        event = ScheduleEvent.objects.get()
        self.assertEqual(timezone.localdate(event.start_at).isoformat(), '2026-05-19')
        self.assertIn('weekday_corrected_count=1', output.getvalue())

    def test_repair_calendar_events_keeps_date_without_explicit_weekday(self):
        raw_data = RawSsafyData.objects.create(source_type='notice', title='학습 시간표', raw_text='HashSet / HashMap')
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='HashSet / HashMap',
            start_at=timezone.datetime(2026, 5, 20, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 5, 21, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='study',
            source_type='notice',
        )

        call_command('repair_calendar_events')

        self.assertEqual(timezone.localdate(ScheduleEvent.objects.get().start_at).isoformat(), '2026-05-20')

    def test_repair_calendar_events_prefixes_all_track_titles(self):
        raw_data = RawSsafyData.objects.create(source_type='notice', title='학습 시간표', raw_text='calendar')
        tracks = ['Python', 'Java비전공', 'Java전공', 'Embedded', 'Mobile', 'Embedded Robot', 'Data', '마이스터고']
        for index, track in enumerate(tracks, start=1):
            ScheduleEvent.objects.create(
                raw_data=raw_data,
                title='Front-End',
                start_at=timezone.datetime(2026, 7, index, tzinfo=timezone.get_current_timezone()),
                end_at=timezone.datetime(2026, 7, index + 1, tzinfo=timezone.get_current_timezone()),
                is_all_day=True,
                event_type='study',
                source_type='notice',
                metadata_json={'track': track},
            )

        output = StringIO()
        call_command('repair_calendar_events', stdout=output)

        titles = set(ScheduleEvent.objects.values_list('title', flat=True))
        for track in tracks:
            self.assertIn(f'{track}) Front-End', titles)
        self.assertIn('track_prefixed_count=8', output.getvalue())

    def test_repair_calendar_events_does_not_prefix_common_event(self):
        raw_data = RawSsafyData.objects.create(source_type='notice', title='학습 시간표', raw_text='calendar')
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='과목평가',
            start_at=timezone.datetime(2026, 4, 6, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 4, 7, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='exam',
            source_type='notice',
            metadata_json={'track': 'Python'},
        )

        call_command('repair_calendar_events')

        self.assertEqual(ScheduleEvent.objects.get().title, '과목평가')

    def test_repair_calendar_events_removes_manual_subject_exam_duplicate_when_ocr_exists(self):
        raw_data = RawSsafyData.objects.create(source_type='notice', title='평가 안내', raw_text='과목평가')
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='과목평가',
            start_at=timezone.datetime(2026, 4, 6, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 4, 7, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='exam',
            source_type='notice',
        )
        ScheduleEvent.objects.create(
            title='과목평가6',
            start_at=timezone.datetime(2026, 4, 6, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 4, 7, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='exam',
            source_type='repair',
            metadata_json={'repair_source': 'manual_exam_correction'},
        )

        output = StringIO()
        call_command('repair_calendar_events', stdout=output)

        self.assertEqual(ScheduleEvent.objects.filter(start_at__date='2026-04-06', event_type='exam').count(), 1)
        self.assertEqual(ScheduleEvent.objects.get().title, '과목평가')
        self.assertIn('semantic_exam_duplicate_removed_count=1', output.getvalue())

    def test_repair_calendar_events_removes_manual_monthly_exam_duplicate_when_ocr_exists(self):
        raw_data = RawSsafyData.objects.create(source_type='notice', title='평가 안내', raw_text='월말평가')
        ScheduleEvent.objects.create(
            raw_data=raw_data,
            title='월말평가',
            start_at=timezone.datetime(2026, 4, 6, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 4, 7, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='exam',
            source_type='notice',
        )
        ScheduleEvent.objects.create(
            title='월말평가3',
            start_at=timezone.datetime(2026, 4, 6, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 4, 7, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='exam',
            source_type='repair',
            metadata_json={'repair_source': 'manual_exam_correction'},
        )

        call_command('repair_calendar_events')

        self.assertEqual(ScheduleEvent.objects.filter(start_at__date='2026-04-06', event_type='exam').count(), 1)
        self.assertEqual(ScheduleEvent.objects.get().title, '월말평가')

    def test_repair_calendar_events_keeps_manual_exam_without_ocr_duplicate(self):
        ScheduleEvent.objects.create(
            title='과목평가6',
            start_at=timezone.datetime(2026, 4, 6, tzinfo=timezone.get_current_timezone()),
            end_at=timezone.datetime(2026, 4, 7, tzinfo=timezone.get_current_timezone()),
            is_all_day=True,
            event_type='exam',
            source_type='repair',
            metadata_json={'repair_source': 'manual_exam_correction'},
        )

        call_command('repair_calendar_events')

        self.assertEqual(ScheduleEvent.objects.filter(title='과목평가6').count(), 1)


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


def _source_item(source_type, source_url, title, notice_id, raw_text=''):
    return {
        'source_type': source_type,
        'source_url': source_url,
        'title': title,
        'raw_text': raw_text,
        'raw_html': f'<main>{raw_text or title}</main>',
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


def _timetable_ocr_boxes():
    return [
        {'text': '5월', 'x1': 20, 'y1': 20, 'x2': 52, 'y2': 40},
        {'text': 'MON', 'x1': 110, 'y1': 60, 'x2': 140, 'y2': 80},
        {'text': 'TUE', 'x1': 210, 'y1': 60, 'x2': 240, 'y2': 80},
        {'text': 'WED', 'x1': 310, 'y1': 60, 'x2': 340, 'y2': 80},
        {'text': 'THU', 'x1': 410, 'y1': 60, 'x2': 440, 'y2': 80},
        {'text': 'FRI', 'x1': 510, 'y1': 60, 'x2': 540, 'y2': 80},
        {'text': '11', 'x1': 110, 'y1': 100, 'x2': 124, 'y2': 120},
        {'text': '12', 'x1': 210, 'y1': 100, 'x2': 224, 'y2': 120},
        {'text': '13', 'x1': 310, 'y1': 100, 'x2': 324, 'y2': 120},
        {'text': '14', 'x1': 410, 'y1': 100, 'x2': 424, 'y2': 120},
        {'text': '15', 'x1': 510, 'y1': 100, 'x2': 524, 'y2': 120},
        {'text': 'Pandas 실습', 'x1': 205, 'y1': 132, 'x2': 290, 'y2': 152, 'confidence': 0.98},
    ]


def _timetable_notice_title_ocr_boxes(source_title):
    boxes = _timetable_ocr_boxes()
    boxes[-1] = {'text': source_title, 'x1': 205, 'y1': 132, 'x2': 260, 'y2': 152, 'confidence': 0.98}
    return boxes


def _timetable_multiline_ocr_boxes():
    boxes = _timetable_ocr_boxes()
    boxes[-1] = {'text': '[Live 방송]\nDjango :\nDRF 1', 'x1': 205, 'y1': 132, 'x2': 290, 'y2': 172, 'confidence': 0.98}
    return boxes


def _timetable_non_learning_ocr_boxes():
    boxes = _timetable_ocr_boxes()[:-1]
    boxes.extend(
        [
            {'text': '중식', 'x1': 205, 'y1': 132, 'x2': 245, 'y2': 152, 'confidence': 0.98},
            {'text': '과목평가', 'x1': 305, 'y1': 132, 'x2': 370, 'y2': 152, 'confidence': 0.98},
        ]
    )
    return boxes


def _timetable_practice_marker_ocr_boxes():
    boxes = _timetable_ocr_boxes()
    boxes[-1] = {'text': '[실습 및 Q&A] Django', 'x1': 205, 'y1': 132, 'x2': 330, 'y2': 152, 'confidence': 0.98}
    return boxes


def _timetable_noise_ocr_boxes():
    boxes = _timetable_ocr_boxes()[:-1]
    boxes.extend(
        [
            {'text': '12:00 13:00', 'x1': 205, 'y1': 132, 'x2': 290, 'y2': 152, 'confidence': 0.98},
            {'text': '중식', 'x1': 305, 'y1': 132, 'x2': 345, 'y2': 152, 'confidence': 0.98},
            {'text': 'Lunch', 'x1': 405, 'y1': 132, 'x2': 455, 'y2': 152, 'confidence': 0.98},
            {'text': 'There is no change', 'x1': 505, 'y1': 132, 'x2': 640, 'y2': 152, 'confidence': 0.98},
        ]
    )
    return boxes


def _timetable_time_prefixed_class_ocr_boxes():
    boxes = _timetable_ocr_boxes()[:-1]
    boxes.append(
        {
            'text': '9: 00 10: 00 [ Live 방송 ] JS Basic Syntax1',
            'x1': 205,
            'y1': 132,
            'x2': 290,
            'y2': 152,
            'confidence': 0.98,
        }
    )
    return boxes


def _timetable_date_header_ocr_boxes():
    return [
        {'text': '5월 11일', 'x1': 110, 'y1': 100, 'x2': 170, 'y2': 120},
        {'text': '5월 12일', 'x1': 210, 'y1': 100, 'x2': 270, 'y2': 120},
        {'text': '5월 13일', 'x1': 310, 'y1': 100, 'x2': 370, 'y2': 120},
        {'text': '5월 14일', 'x1': 410, 'y1': 100, 'x2': 470, 'y2': 120},
        {'text': '5월 15일', 'x1': 510, 'y1': 100, 'x2': 570, 'y2': 120},
        {'text': '[Live 방송]\nDjango :\nDRF 1', 'x1': 105, 'y1': 132, 'x2': 190, 'y2': 172, 'confidence': 0.98},
        {'text': '[Live 방송]\nDjango :\nDRF 2', 'x1': 205, 'y1': 132, 'x2': 290, 'y2': 172, 'confidence': 0.98},
        {'text': 'JS: DOM', 'x1': 305, 'y1': 132, 'x2': 370, 'y2': 152, 'confidence': 0.98},
        {'text': 'JS: Basic Syntax 1', 'x1': 405, 'y1': 132, 'x2': 540, 'y2': 152, 'confidence': 0.98},
        {'text': '과목평가 9', 'x1': 505, 'y1': 132, 'x2': 585, 'y2': 152, 'confidence': 0.98},
    ]


def _timetable_holiday_ocr_boxes():
    return [
        {'text': '5월 5일', 'x1': 210, 'y1': 100, 'x2': 270, 'y2': 120},
        {'text': 'Django DRF', 'x1': 205, 'y1': 132, 'x2': 290, 'y2': 152, 'confidence': 0.98},
    ]


def _timetable_many_cell_ocr_boxes():
    boxes = _timetable_ocr_boxes()[:-1]
    boxes.extend(
        [
            {'text': '[Live 방송]\nDjango :\nDRF 1', 'x1': 205, 'y1': 132, 'x2': 290, 'y2': 172, 'confidence': 0.98},
            {'text': '[Live 방송]\nDjango :\nDRF 2', 'x1': 305, 'y1': 132, 'x2': 390, 'y2': 172, 'confidence': 0.98},
            {'text': 'JS: DOM', 'x1': 405, 'y1': 132, 'x2': 470, 'y2': 152, 'confidence': 0.98},
            {'text': 'JS: Basic Syntax 1', 'x1': 505, 'y1': 132, 'x2': 640, 'y2': 152, 'confidence': 0.98},
            {'text': '[실습 및 Q&A]', 'x1': 205, 'y1': 180, 'x2': 310, 'y2': 200, 'confidence': 0.98},
            {'text': '중식', 'x1': 305, 'y1': 180, 'x2': 345, 'y2': 200, 'confidence': 0.98},
            {'text': '과목평가 9', 'x1': 405, 'y1': 180, 'x2': 485, 'y2': 200, 'confidence': 0.98},
        ]
    )
    return boxes


def _timetable_wrapper_and_cell_ocr_boxes(source_title):
    boxes = _timetable_ocr_boxes()[:-1]
    boxes.extend(
        [
            {'text': source_title, 'x1': 205, 'y1': 132, 'x2': 290, 'y2': 152, 'confidence': 0.98},
            {'text': 'JS: DOM', 'x1': 305, 'y1': 132, 'x2': 370, 'y2': 152, 'confidence': 0.98},
        ]
    )
    return boxes


def _review_required_exam_ocr_boxes():
    boxes = _calendar_ocr_boxes()
    boxes[-1] = {'text': '배틀싸피과목평가', 'x1': 500, 'y1': 212, 'x2': 600, 'y2': 232, 'confidence': 0.96}
    return boxes


def _combined_exam_ocr_boxes():
    boxes = _calendar_ocr_boxes()
    boxes[-1] = {'text': '과목평가1/ 월말평가1', 'x1': 505, 'y1': 212, 'x2': 610, 'y2': 232, 'confidence': 0.96}
    return boxes


def _march_exam_run_ocr_boxes(day_count=3):
    boxes = [
        {'text': '3', 'x1': 20, 'y1': 20, 'x2': 32, 'y2': 40},
        {'text': '월', 'x1': 31, 'y1': 20, 'x2': 50, 'y2': 40},
        {'text': 'SUN', 'x1': 10, 'y1': 60, 'x2': 40, 'y2': 80},
        {'text': 'MON', 'x1': 110, 'y1': 60, 'x2': 140, 'y2': 80},
        {'text': 'TUE', 'x1': 210, 'y1': 60, 'x2': 240, 'y2': 80},
        {'text': 'WED', 'x1': 310, 'y1': 60, 'x2': 340, 'y2': 80},
        {'text': 'THU', 'x1': 410, 'y1': 60, 'x2': 440, 'y2': 80},
        {'text': 'FRI', 'x1': 510, 'y1': 60, 'x2': 540, 'y2': 80},
        {'text': 'SAT', 'x1': 610, 'y1': 60, 'x2': 640, 'y2': 80},
        {'text': '1', 'x1': 10, 'y1': 100, 'x2': 24, 'y2': 120},
        {'text': '2', 'x1': 110, 'y1': 100, 'x2': 124, 'y2': 120},
        {'text': '3', 'x1': 210, 'y1': 100, 'x2': 224, 'y2': 120},
        {'text': '4', 'x1': 310, 'y1': 100, 'x2': 324, 'y2': 120},
        {'text': '과목평가', 'x1': 110, 'y1': 132, 'x2': 170, 'y2': 152, 'confidence': 0.96},
        {'text': '과목평가', 'x1': 210, 'y1': 132, 'x2': 270, 'y2': 152, 'confidence': 0.96},
        {'text': '과목평가', 'x1': 310, 'y1': 132, 'x2': 370, 'y2': 152, 'confidence': 0.96},
    ]
    if day_count >= 4:
        boxes.extend(
            [
                {'text': '5', 'x1': 410, 'y1': 100, 'x2': 424, 'y2': 120},
                {'text': '과목평가', 'x1': 410, 'y1': 132, 'x2': 470, 'y2': 152, 'confidence': 0.96},
            ]
        )
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
    def __init__(self, html, url='https://example.com/list', title=''):
        self.html = html
        self.url = url
        self._title = title

    def goto(self, *args, **kwargs):
        return None

    def content(self):
        return self.html

    def title(self):
        return self._title

    def locator(self, selector):
        return _StaticLocator(1 if 'userId' in self.html or 'userPwd' in self.html else 0)


class _StaticLocator:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


class _AcademicTogglePage:
    def __init__(self, buttons):
        self.buttons = buttons
        self.waits = []

    def locator(self, selector):
        return _AcademicToggleLocator(self.buttons)

    def wait_for_timeout(self, timeout):
        self.waits.append(timeout)


class _AcademicToggleLocator:
    def __init__(self, buttons):
        self.buttons = buttons

    def count(self):
        return len(self.buttons)

    def nth(self, index):
        return self.buttons[index]


class _AcademicToggle:
    def __init__(self, aria_expanded):
        self.aria_expanded = aria_expanded
        self.click_count = 0

    def get_attribute(self, name):
        return self.aria_expanded if name == 'aria-expanded' else ''

    def click(self):
        self.click_count += 1
        self.aria_expanded = 'true'


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


def _fake_playwright_modules():
    class _FakeLocator:
        def count(self):
            return 0

    class _FakePage:
        def goto(self, *args, **kwargs):
            return None

        def fill(self, *args, **kwargs):
            return None

        def click(self, *args, **kwargs):
            return None

        def wait_for_load_state(self, *args, **kwargs):
            return None

        def locator(self, *args, **kwargs):
            return _FakeLocator()

        def set_default_timeout(self, *args, **kwargs):
            return None

        def content(self):
            return '<main></main>'

    class _FakeContext:
        def new_page(self):
            return _FakePage()

        def close(self):
            return None

    class _FakeBrowser:
        def new_context(self):
            return _FakeContext()

        def close(self):
            return None

    class _FakeChromium:
        def launch(self, headless=True):
            return _FakeBrowser()

    class _FakePlaywright:
        chromium = _FakeChromium()

    class _FakePlaywrightContextManager:
        def __enter__(self):
            return _FakePlaywright()

        def __exit__(self, exc_type, exc, tb):
            return False

    sync_api_module = types.ModuleType('playwright.sync_api')
    sync_api_module.TimeoutError = TimeoutError
    sync_api_module.sync_playwright = lambda: _FakePlaywrightContextManager()
    playwright_module = types.ModuleType('playwright')
    playwright_module.sync_api = sync_api_module
    return {
        'playwright': playwright_module,
        'playwright.sync_api': sync_api_module,
    }
