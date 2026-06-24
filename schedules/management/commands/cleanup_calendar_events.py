"""
캘린더 일정 정리 관리 명령어.

--dry-run: 삭제/수정 예정 항목만 출력하고 실제 변경하지 않음
--apply  : 실제 변경 수행

정리 대상:
1. 국가공휴일 날짜에 생성된 timetable_grid 파서 일정
   (generated_class_on_korean_holiday 경고를 blocking으로 전환 후에도
    이전에 생성된 잘못된 일정이 DB에 남아 있을 수 있음)

보존 대상:
- 국가공휴일(national_holiday) 일정
- 사용자 직접 입력 일정 (owner_id != None)
- 공지 원본 (RawSsafyData)
- 사용자 직접 생성 일정과 무관한 일반 공지 일정
"""
import logging
import re
from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from schedules.models import ScheduleEvent
from sync.management.commands.seed_korean_holidays import get_korean_holidays

logger = logging.getLogger(__name__)

TIMETABLE_PARSER_TYPES = {'timetable_grid', 'ocr_timetable_grid'}


class Command(BaseCommand):
    help = '??? ?? ??: ????? timetable ? OCR ??PJT ?? ??'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='변경 사항을 출력만 하고 실제로 적용하지 않습니다.',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            default=False,
            help='실제로 정리를 적용합니다.',
        )
        parser.add_argument(
            '--year',
            type=int,
            default=2026,
            help='국가공휴일 기준 연도 (기본값: 2026)',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options['dry_run'] or not options['apply']
        year = options['year']

        if dry_run:
            self.stdout.write('=== DRY-RUN 모드 (변경 없음) ===\n')
        else:
            self.stdout.write('=== APPLY 모드 (실제 변경) ===\n')

        tz = timezone.get_current_timezone()

        # ── 1. 국가공휴일 날짜 수집 ────────────────────────────────────────
        try:
            korean_holidays = get_korean_holidays(year)
        except ValueError as exc:
            self.stderr.write(f'국가공휴일 데이터 오류: {exc}')
            return

        holiday_dates = {d for _title, d in korean_holidays}
        holiday_by_date = {}
        for title, d in korean_holidays:
            holiday_by_date.setdefault(d, []).append(title)

        # ── 2. 전체 일정 통계 ──────────────────────────────────────────────
        all_events = ScheduleEvent.objects.all()
        total = all_events.count()

        notice_gen = list(
            ScheduleEvent.objects.filter(raw_data__isnull=False, source_type__in=['notice', 'ssafy_notice'])
            .select_related('raw_data')
        )
        national_holidays = list(ScheduleEvent.objects.filter(source_type='national_holiday'))

        # 토요일 통계
        def wd_local(dt):
            return dt.astimezone(tz).weekday()

        sat_start = [e for e in all_events.select_related('raw_data') if wd_local(e.start_at) == 5]
        sat_notice_gen = [e for e in sat_start if e.raw_data_id is not None]
        sat_national_hol = [e for e in sat_start if e.source_type == 'national_holiday']
        sat_user = [e for e in sat_start if e.owner_id is not None]

        # 금→토 (exclusive-end 저장, API >= 버그 수정 전 토요일 표시되던 것)
        fri_to_sat = [e for e in all_events.select_related('raw_data')
                      if wd_local(e.start_at) == 4 and wd_local(e.end_at) == 5]

        self.stdout.write(f'\n=== 전체 일정 현황 ===')
        self.stdout.write(f'검사한 일정 수             : {total}개')
        self.stdout.write(f'국가공휴일 일정             : {len(national_holidays)}개')
        self.stdout.write(f'공지 기반 generated 일정    : {len(notice_gen)}개')
        self.stdout.write(f'토요일 시작 일정            : {len(sat_start)}개')
        self.stdout.write(f'  └ 토요일 공식 공휴일      : {len(sat_national_hol)}개 (유지)')
        self.stdout.write(f'  └ 토요일 공지 기반 일정   : {len(sat_notice_gen)}개')
        self.stdout.write(f'  └ 토요일 사용자 직접 입력 : {len(sat_user)}개 (유지)')
        self.stdout.write(f'금→토 이어지는 일정         : {len(fri_to_sat)}개 (API >= 수정으로 해결됨)')

        # ── 3. 국가공휴일 날짜의 timetable 일정 식별 ──────────────────────
        timetable_on_holiday = []
        for e in notice_gen:
            event_date = e.start_at.astimezone(tz).date()
            if event_date not in holiday_dates:
                continue
            parser_type = (e.metadata_json or {}).get('parser_type') or (e.metadata_json or {}).get('parser')
            if parser_type not in TIMETABLE_PARSER_TYPES:
                continue
            if e.owner_id is not None:
                continue
            timetable_on_holiday.append(e)

        # ── 4. 기타 통계 ──────────────────────────────────────────────────
        # 공지 기반인데 공휴일 날짜의 일정 전체 (파서 무관)
        notice_on_holiday = [
            e for e in notice_gen
            if e.start_at.astimezone(tz).date() in holiday_dates
        ]
        timetable_project_duplicates = _find_timetable_project_duplicates(notice_gen, tz)

        self.stdout.write(f'\n=== 국가공휴일 중복 분석 ===')
        self.stdout.write(f'공식 국가공휴일 날짜 수     : {len(holiday_dates)}개')
        self.stdout.write(f'공지 기반 공휴일 날짜 일정  : {len(notice_on_holiday)}개 (전체)')
        self.stdout.write(f'  └ timetable 파서 일정     : {len(timetable_on_holiday)}개 ← 삭제 대상')
        self.stdout.write(f'  └ 기타 파서 일정          : {len(notice_on_holiday)-len(timetable_on_holiday)}개 (유지)')

        if timetable_on_holiday:
            self.stdout.write(f'\n--- 삭제 대상 상세 (timetable 파서 × 국가공휴일 날짜) ---')
            by_date = {}
            for e in timetable_on_holiday:
                d = e.start_at.astimezone(tz).date()
                by_date.setdefault(d, []).append(e)
            for d in sorted(by_date.keys()):
                hol_names = ', '.join(holiday_by_date.get(d, []))
                evs = by_date[d]
                self.stdout.write(f'  {d} ({hol_names}): {len(evs)}개')
                for e in evs[:5]:
                    self.stdout.write(f'    id={e.id} | {e.title[:60]}')
                if len(evs) > 5:
                    self.stdout.write(f'    ... 외 {len(evs)-5}개')

        # ── 5. 실행 ────────────────────────────────────────────────────────
        self.stdout.write(f'\n=== OCR timetable project duplicate analysis ===')
        self.stdout.write(f'generic project duplicates in same raw/date/track: {len(timetable_project_duplicates)}')
        if timetable_project_duplicates:
            for e in timetable_project_duplicates[:20]:
                self.stdout.write(f'  id={e.id} | {e.start_at.astimezone(tz).date()} | {e.title[:80]} | raw={e.raw_data_id}')
            if len(timetable_project_duplicates) > 20:
                self.stdout.write(f'  ... and {len(timetable_project_duplicates) - 20} more')

        delete_ids = list(dict.fromkeys([e.id for e in timetable_on_holiday] + [e.id for e in timetable_project_duplicates]))

        self.stdout.write(f'\n=== 예정된 작업 ===')
        self.stdout.write(f'delete_plan_count: {len(delete_ids)} (holiday timetable + OCR project duplicates)')
        self.stdout.write(f'수정 예정: 0개')
        self.stdout.write(f'재생성 예정: 0개 (재파싱 별도 수행 시 holiday blocking으로 자동 제외)')

        if dry_run:
            self.stdout.write(f'\n=== 예상 최종 현황 ===')
            self.stdout.write(f'적용 후 예상 총 일정 수  : {total - len(delete_ids)}개')
            self.stdout.write(f'expected_notice_generated_count: {len(notice_gen) - len(delete_ids)}')
            self.stdout.write(f'국가공휴일 일정 (유지)   : {len(national_holidays)}개')
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING('\n[DRY-RUN] 실제 변경 없이 종료합니다.'))
            self.stdout.write('적용하려면 --apply 옵션을 사용하세요.')
        else:
            if delete_ids:
                deleted_count, _ = ScheduleEvent.objects.filter(id__in=delete_ids).delete()
                self.stdout.write(self.style.SUCCESS(f'\n[APPLY] {deleted_count}개 삭제 완료'))
            else:
                self.stdout.write(self.style.SUCCESS('\n[APPLY] 삭제할 항목 없음'))

            # ── 6. 적용 후 통계 ──────────────────────────────────────────
            after_total = ScheduleEvent.objects.count()
            remaining_notice_gen = ScheduleEvent.objects.filter(raw_data__isnull=False).count()
            remaining_national_hol = ScheduleEvent.objects.filter(source_type='national_holiday').count()
            self.stdout.write(f'\n=== 적용 후 최종 현황 ===')
            self.stdout.write(f'총 일정 수               : {after_total}개')
            self.stdout.write(f'공지 기반 generated 일정 : {remaining_notice_gen}개')
            self.stdout.write(f'국가공휴일 일정          : {remaining_national_hol}개')

        self.stdout.write(self.style.SUCCESS('\n완료'))


def _find_timetable_project_duplicates(events, tz):
    groups = {}
    for event in events:
        if event.owner_id is not None or event.event_type != 'project':
            continue
        metadata = event.metadata_json or {}
        parser_type = metadata.get('parser_type') or metadata.get('parser')
        if parser_type not in TIMETABLE_PARSER_TYPES:
            continue
        group = _project_dedupe_group(event.title)
        if group != 'PROJECT_PJT_GENERIC':
            continue
        audience = metadata.get('audience') or {}
        track = metadata.get('track_key') or audience.get('track_key') or metadata.get('track') or audience.get('track') or 'all'
        key = (
            event.raw_data_id,
            event.start_at.astimezone(tz).date().isoformat(),
            event.end_at.astimezone(tz).date().isoformat() if event.end_at else '',
            str(track),
            group,
        )
        groups.setdefault(key, []).append(event)

    duplicates = []
    for group_events in groups.values():
        if len(group_events) <= 1:
            continue
        keep = sorted(group_events, key=_project_keep_sort_key)[0]
        duplicates.extend(event for event in group_events if event.id != keep.id)
    return duplicates


def _project_keep_sort_key(event):
    title = str(event.title or '')
    compact = _compact_project_title(title)
    has_time_prefix = bool(re.match(r'^\s*\[[^\]]+\]\s*\d{1,2}\s*-\s*\d{1,2}', title))
    has_overview = 'OVERVIEW' in compact
    return (has_time_prefix, has_overview, len(compact), event.id)


def _project_dedupe_group(title):
    compact = _compact_project_title(title)
    if '\uad00\ud1b5' not in compact or ('PJT' not in compact and '\ud504\ub85c\uc81d\ud2b8' not in compact):
        return compact
    if '\uacbd\uc9c4\ub300\ud68c' in compact:
        return 'PROJECT_PJT_CONTEST'
    if 'OT' in compact:
        return 'PROJECT_PJT_OT'
    if '\uc81c\ucd9c' in compact or '\ub9c8\uac10' in compact:
        return 'PROJECT_PJT_DEADLINE'
    if '\ubc1c\ud45c' in compact:
        return 'PROJECT_PJT_PRESENTATION'
    return 'PROJECT_PJT_GENERIC'


def _compact_project_title(title):
    compact = re.sub(r'[\s.()_\-/~:\[\]]+', '', str(title or '')).upper()
    compact = compact.replace('PROJECT', 'PJT')
    return compact
