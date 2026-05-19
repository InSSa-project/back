from dataclasses import dataclass, field
from datetime import date, time, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from schedules.models import ScheduleEvent
from sync.models import RawSsafyData
from sync.services.reparse_service import reparse_raw_data_to_events


REPAIR_SOURCE = 'manual_calendar_correction'
MANUAL_EXAM_REPAIR_SOURCE = 'manual_exam_correction'
MANUAL_EXAM_REASON = 'evaluation_notice_missing_manual_mvp_seed'
NOISE_TITLES = {'시간', 'ViewModel', 'without questions', 'Live 방송'}
NOISE_TITLES.update({'운영자', '♥알림신청♥', '공지사항 상세', '목록'})
PROMOTIONAL_TITLE_KEYWORDS = [
    '싸피티비',
    '치킨세트',
    '박슬기',
    '수다 타임',
    '중요! 방송인',
    '기타] [싸피티비]',
    '알림신청',
    '이벤트 게시물',
    '영상에 댓글',
    '삼성청년SW',
    '사무국입니다',
    '첨부 파일',
]
OCR_CANDIDATE_KEYWORDS = [
    '15기 1학기 전체 일정',
    '전체 일정',
    '과목월말평가',
    '과목평가',
    '월말평가',
    '학습 시간표',
    'AI 강의',
    '온라인 위크',
    '관통 프로젝트',
]


@dataclass
class RepairSummary:
    deleted_count: int = 0
    created_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    dry_run: bool = False
    use_manual_fallback: bool = False
    raw_candidate_count: int = 0
    parser_created_count: int = 0
    parser_skipped_count: int = 0
    parser_review_required_count: int = 0
    total_events: int = 0
    ocr_generated_count: int = 0
    manual_correction_count: int = 0
    manual_correction_ratio: float = 0.0
    review_required_count: int = 0
    missing_evaluation_notice_raw_data: bool = False
    deleted_titles: list = field(default_factory=list)
    key_events: list = field(default_factory=list)
    february_events: list = field(default_factory=list)
    ai_lecture_counts: list = field(default_factory=list)
    raw_candidates: list = field(default_factory=list)


class Command(BaseCommand):
    help = 'Repair generated SSAFY calendar events without touching user-created events.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Print changes without writing them.')
        parser.add_argument(
            '--use-manual-fallback',
            action='store_true',
            help='Apply MVP manual calendar/exam fallback corrections after OCR parsing.',
        )

    def handle(self, *args, **options):
        summary = repair_calendar_events(
            dry_run=options['dry_run'],
            use_manual_fallback=options['use_manual_fallback'],
        )
        self.stdout.write(
            self.style.SUCCESS(
                'Calendar repair completed.\n'
                f'use_manual_fallback={str(summary.use_manual_fallback).lower()}\n'
                f'deleted_count={summary.deleted_count}\n'
                f'updated_count={summary.updated_count}\n'
                f'created_count={summary.created_count}\n'
                f'skipped_count={summary.skipped_count}\n'
                f'raw_candidate_count={summary.raw_candidate_count}\n'
                f'raw_candidates={"; ".join(summary.raw_candidates[:20]) or "none"}\n'
                f'parser_created_count={summary.parser_created_count}\n'
                f'parser_skipped_count={summary.parser_skipped_count}\n'
                f'parser_review_required_count={summary.parser_review_required_count}\n'
                f'total_events={summary.total_events}\n'
                f'ocr_generated_count={summary.ocr_generated_count}\n'
                f'manual_correction_count={summary.manual_correction_count}\n'
                f'manual_correction_ratio={summary.manual_correction_ratio:.2f}\n'
                f'review_required_count={summary.review_required_count}\n'
                f'missing_evaluation_notice_raw_data={str(summary.missing_evaluation_notice_raw_data).lower()}\n'
                f'deleted_titles={"; ".join(summary.deleted_titles[:20]) or "none"}\n'
                f'key_events={"; ".join(summary.key_events[:40]) or "none"}\n'
                f'february_events={"; ".join(summary.february_events[:40]) or "none"}\n'
                f'ai_lecture_counts={"; ".join(summary.ai_lecture_counts[:40]) or "none"}\n'
                f'dry_run={str(summary.dry_run).lower()}'
            )
        )


@transaction.atomic
def repair_calendar_events(dry_run=False, use_manual_fallback=False):
    summary = RepairSummary(dry_run=dry_run, use_manual_fallback=use_manual_fallback)
    base_raw = _find_base_schedule_raw()
    evaluation_raw = _find_evaluation_raw()
    candidate_qs = _ocr_candidate_queryset()
    summary.raw_candidate_count = candidate_qs.count()
    summary.raw_candidates = _raw_candidate_logs(candidate_qs)

    _delete_false_positives(summary, dry_run=dry_run)
    _normalize_existing_titles(summary, dry_run=dry_run)
    _reparse_ocr_candidates(summary, candidate_qs, dry_run=dry_run)
    _normalize_existing_titles(summary, dry_run=dry_run)
    _delete_false_positives(summary, dry_run=dry_run)
    if not evaluation_raw:
        summary.missing_evaluation_notice_raw_data = True
    if use_manual_fallback:
        _upsert_base_corrections(summary, base_raw, dry_run=dry_run)
        _upsert_exam_corrections(summary, evaluation_raw or base_raw, dry_run=dry_run)

    _dedupe_generated(summary, dry_run=dry_run)
    _fill_metrics(summary)
    summary.key_events = _key_events()
    summary.february_events = _february_events()
    summary.ai_lecture_counts = _ai_lecture_counts()
    if dry_run:
        transaction.set_rollback(True)
    return summary


def _delete_false_positives(summary, dry_run=False):
    qs = ScheduleEvent.objects.filter(raw_data__isnull=False)
    targets = qs.filter(title__in=NOISE_TITLES)
    for keyword in PROMOTIONAL_TITLE_KEYWORDS:
        targets = targets | qs.filter(title__contains=keyword)
    targets = targets | qs.filter(title__endswith='운영자')
    targets = targets | qs.filter(title__contains='출연').exclude(title__contains='과목평가').exclude(title__contains='월말평가')
    targets = targets | qs.filter(title__contains='시간표 운영자')
    targets = targets | qs.filter(start_at__date__in=[date(2026, 1, 31), date(2026, 5, 2), date(2026, 5, 3)])
    targets = targets | qs.filter(start_at__date=date(2026, 5, 5)).exclude(title='어린이날')
    targets = targets | qs.filter(start_at__date=date(2026, 1, 7)).filter(title__contains='소명')
    targets = targets | qs.filter(title__contains='오후 ) AI 강의')
    generated_qs = ScheduleEvent.objects.filter(raw_data__isnull=False) | ScheduleEvent.objects.filter(
        metadata_json__repair_source=REPAIR_SOURCE
    )
    targets = targets | generated_qs.filter(
        title='SSAFY DAY',
        start_at__date__in=[date(2026, 1, 25), date(2026, 1, 26), date(2026, 1, 27)],
    )
    targets = targets.distinct()
    for event in targets:
        summary.deleted_count += 1
        summary.deleted_titles.append(f'{timezone.localdate(event.start_at).isoformat()} {event.title}')
    if not dry_run:
        ScheduleEvent.objects.filter(id__in=list(targets.values_list('id', flat=True))).delete()

    jan15 = qs.filter(start_at__date=date(2026, 1, 15)).exclude(title='15기 SW AI 스타트 캠프')
    for event in jan15:
        summary.deleted_count += 1
        summary.deleted_titles.append(f'{timezone.localdate(event.start_at).isoformat()} {event.title}')
    if not dry_run:
        jan15.delete()

    meetup = qs.filter(start_at__date=date(2026, 4, 10), title='밋업')
    for event in meetup:
        summary.deleted_count += 1
        summary.deleted_titles.append(f'{timezone.localdate(event.start_at).isoformat()} {event.title}')
    if not dry_run:
        meetup.delete()


def _reparse_ocr_candidates(summary, candidate_qs, dry_run=False):
    if not candidate_qs.exists():
        summary.raw_candidates = ['parser_improvement_unavailable:no_matching_ocr_raw_data']
        return

    reparse_summary = reparse_raw_data_to_events(candidate_qs, dry_run=dry_run)
    summary.parser_created_count = reparse_summary.created_count
    summary.parser_skipped_count = reparse_summary.skipped_count
    summary.parser_review_required_count = _review_required_raw_count(candidate_qs)


def _normalize_existing_titles(summary, dry_run=False):
    for event in ScheduleEvent.objects.filter(raw_data__isnull=False):
        normalized_title = _normalize_event_title(event.title)
        if normalized_title == event.title:
            continue
        summary.updated_count += 1
        if not dry_run:
            event.title = normalized_title
            event.save(update_fields=['title'])


def _upsert_base_corrections(summary, raw_data, dry_run=False):
    corrections = [
        ('15기 SW AI 캠프', date(2026, 1, 7), date(2026, 1, 10), 'study', {}),
        ('15기 SW AI 스타트 캠프', date(2026, 1, 12), date(2026, 1, 13), 'study', {}),
        ('15기 SW AI 스타트 캠프', date(2026, 1, 15), date(2026, 1, 16), 'study', {}),
        ('AI 창의 캠프', date(2026, 1, 19), date(2026, 1, 22), 'study', {'track': 'meister'}),
        ('SSAFY DAY', date(2026, 1, 24), date(2026, 1, 25), 'etc', {}),
        ('설날', date(2026, 2, 16), date(2026, 2, 19), 'etc', {}),
        ('SW역량테스트(IM형/A형)', date(2026, 2, 19), date(2026, 2, 20), 'etc', {}),
        ('AI 강의 1', date(2026, 2, 24), date(2026, 2, 28), 'study', {}),
        ('SSAFY DAY', date(2026, 3, 26), date(2026, 3, 27), 'etc', {}),
        ('AI 챌린지', date(2026, 4, 2), date(2026, 4, 4), 'etc', {}),
        ('상반기 밋업', date(2026, 4, 10), date(2026, 4, 11), 'etc', {}),
        ('관통 PJT', date(2026, 5, 22), date(2026, 5, 23), 'project', {'track': 'common'}),
        ('관통 프로젝트 집중기간', date(2026, 6, 22), date(2026, 6, 25), 'project', {}),
    ]
    for title, start_date, end_date, event_type, metadata in corrections:
        _upsert_event(summary, raw_data, title, start_date, end_date, event_type, metadata, dry_run)

    for day in _weekdays(date(2026, 3, 16), date(2026, 4, 2)):
        _upsert_event(summary, raw_data, 'AI 강의 Ⅱ', day, day + timedelta(days=1), 'study', {}, dry_run)
    for day in _weekdays(date(2026, 6, 1), date(2026, 6, 13), excluded={date(2026, 6, 3)}):
        _upsert_event(summary, raw_data, '온라인 위크', day, day + timedelta(days=1), 'study', {}, dry_run)


def _upsert_exam_corrections(summary, raw_data, dry_run=False):
    exams = [
        ('과목평가/월말평가', date(2026, 1, 28)),
        ('과목평가2', date(2026, 2, 9)),
        ('과목평가3(일타싸피)', date(2026, 2, 23)),
        ('월말평가2', date(2026, 3, 3)),
        ('과목평가4', date(2026, 3, 16)),
        ('과목평가5', date(2026, 3, 26)),
        ('과목평가6', date(2026, 4, 6)),
        ('월말평가3', date(2026, 4, 6)),
        ('과목평가8', date(2026, 4, 27)),
        ('월말평가4', date(2026, 4, 27)),
        ('과목평가9', date(2026, 5, 11)),
        ('과목평가10', date(2026, 5, 26)),
        ('월말평가5', date(2026, 5, 26)),
        ('월말평가6', date(2026, 6, 25)),
    ]
    for title, start_date in exams:
        _upsert_event(
            summary,
            raw_data,
            title,
            start_date,
            start_date + timedelta(days=1),
            'exam',
            {
                'repair_source': MANUAL_EXAM_REPAIR_SOURCE,
                'source_reason': MANUAL_EXAM_REASON,
            },
            dry_run,
        )


def _upsert_event(summary, raw_data, title, start_date, end_date, event_type, extra_metadata, dry_run=False):
    title = _normalize_event_title(title)
    metadata = {'repair_source': REPAIR_SOURCE}
    metadata.update(extra_metadata)
    event_filter = {
        'start_at': _aware(start_date),
    }
    existing = _find_existing_event(start_date, title, event_type, extra_metadata)
    if existing:
        changed = (
            existing.title != title
            or existing.end_at != _aware(end_date)
            or existing.event_type != event_type
        )
        if changed:
            summary.updated_count += 1
            if not dry_run:
                existing.title = title
                existing.end_at = _aware(end_date)
                existing.event_type = event_type
                existing.metadata_json = {**(existing.metadata_json or {}), **metadata}
                existing.save(update_fields=['title', 'end_at', 'event_type', 'metadata_json'])
        else:
            summary.skipped_count += 1
        return

    summary.created_count += 1
    if dry_run:
        return
    ScheduleEvent.objects.create(
        raw_data=raw_data,
        title=title,
        start_at=_aware(start_date),
        end_at=_aware(end_date),
        is_all_day=True,
        event_type=event_type,
        source_type=raw_data.source_type if raw_data else 'repair',
        source_id=str(raw_data.pk) if raw_data else '',
        metadata_json=metadata,
    )


def _dedupe_generated(summary, dry_run=False):
    seen = {}
    events = ScheduleEvent.objects.filter(raw_data__isnull=False).order_by('start_at', '-id')
    for event in events:
        track = (event.metadata_json or {}).get('track', '')
        event_date = timezone.localdate(event.start_at)
        normalized_title = _normalize_event_title(event.title)
        event_type = '' if event_date == date(2026, 1, 15) and normalized_title == '15기 SW AI 스타트 캠프' else event.event_type
        if normalized_title == 'AI 강의 Ⅱ':
            event_type = ''
        key = (event_date, event_type, normalized_title, track)
        if key not in seen:
            seen[key] = event.id
            continue
        summary.deleted_count += 1
        summary.deleted_titles.append(f'{timezone.localdate(event.start_at).isoformat()} {event.title}')
        if not dry_run:
            event.delete()


def _find_existing_event(start_date, title, event_type, metadata):
    track = metadata.get('track', '')
    normalized_title = _normalize_event_title(title)
    events = ScheduleEvent.objects.filter(start_at__date=start_date)
    if normalized_title != 'AI 강의 Ⅱ':
        events = events.filter(event_type=event_type)
    for event in events:
        event_track = (event.metadata_json or {}).get('track', '')
        if event_track != track:
            continue
        if _normalize_event_title(event.title) == normalized_title:
            return event
    return None


def _normalize_event_title(title):
    normalized = str(title or '').strip()
    compact = normalized.replace(' ', '').upper().replace('Ⅱ', 'II')
    if compact in {'AI강의2', 'AI강의II'}:
        return 'AI 강의 Ⅱ'
    return normalized


def _ocr_candidate_queryset():
    text_query = Q()
    for keyword in OCR_CANDIDATE_KEYWORDS:
        text_query |= Q(title__contains=keyword) | Q(raw_text__contains=keyword)

    ids = set(RawSsafyData.objects.filter(text_query).values_list('id', flat=True))
    for raw_data in RawSsafyData.objects.all().only('id', 'metadata_json'):
        metadata_text = str(raw_data.metadata_json or '')
        metadata = raw_data.metadata_json or {}
        if (
            any(keyword in metadata_text for keyword in OCR_CANDIDATE_KEYWORDS)
            or metadata.get('document_type') == 'evaluation_notice'
        ):
            ids.add(raw_data.id)
    return RawSsafyData.objects.filter(id__in=ids).order_by('id')


def _raw_candidate_logs(candidate_qs):
    logs = []
    for raw_data in candidate_qs[:20]:
        metadata = raw_data.metadata_json or {}
        ocr_length = metadata.get('ocr_text_length', 0)
        sample = ' '.join((raw_data.raw_text or '').split())[:80] or 'no_ocr_text'
        if not ocr_length and '[OCR_TEXT]' not in (raw_data.raw_text or ''):
            sample = f'parser_improvement_unavailable:{sample}'
        logs.append(
            f'id={raw_data.id} title={raw_data.title} source_type={raw_data.source_type} '
            f'source_url={raw_data.source_url or "-"} ocr_text_length={ocr_length} sample={sample}'
        )
    if not logs:
        logs.append('parser_improvement_unavailable:no_matching_ocr_raw_data')
    return logs


def _fill_metrics(summary):
    total_events = ScheduleEvent.objects.count()
    manual_qs = ScheduleEvent.objects.filter(
        metadata_json__repair_source__in=[REPAIR_SOURCE, MANUAL_EXAM_REPAIR_SOURCE]
    )
    manual_ids = list(manual_qs.values_list('id', flat=True))
    ocr_qs = ScheduleEvent.objects.filter(raw_data__isnull=False).exclude(id__in=manual_ids)
    summary.total_events = total_events
    summary.manual_correction_count = manual_qs.count()
    summary.ocr_generated_count = ocr_qs.count()
    summary.manual_correction_ratio = (summary.manual_correction_count / total_events) if total_events else 0
    summary.review_required_count = _review_required_raw_count(RawSsafyData.objects.all())


def _review_required_raw_count(raw_queryset):
    return sum(
        1
        for raw_data in raw_queryset
        if (raw_data.metadata_json or {}).get('review_required_candidate_count', 0)
    )


def _find_base_schedule_raw():
    return RawSsafyData.objects.filter(title__contains='15기 1학기 전체 일정').order_by('id').first()


def _find_evaluation_raw():
    return (
        RawSsafyData.objects.filter(metadata_json__document_type='evaluation_notice').order_by('id').first()
        or RawSsafyData.objects.filter(metadata_json__category='exam', title__contains='평가').order_by('id').first()
        or RawSsafyData.objects.filter(title__contains='과목월말평가 안내').order_by('id').first()
        or RawSsafyData.objects.filter(raw_text__contains='과목월말평가 안내').order_by('id').first()
    )


def _weekdays(start_date, end_date, excluded=None):
    excluded = excluded or set()
    current = start_date
    while current < end_date:
        if current.weekday() < 5 and current not in excluded:
            yield current
        current += timedelta(days=1)


def _aware(day):
    return timezone.make_aware(timezone.datetime.combine(day, time.min), timezone.get_current_timezone())


def _key_events():
    wanted = [
        date(2026, 1, 7),
        date(2026, 1, 15),
        date(2026, 2, 24),
        date(2026, 3, 16),
        date(2026, 4, 10),
        date(2026, 6, 1),
        date(2026, 6, 22),
    ]
    return [
        f'{timezone.localdate(event.start_at).isoformat()} {event.title}'
        for event in ScheduleEvent.objects.filter(start_at__date__in=wanted).order_by('start_at', 'title')
    ]


def _february_events():
    wanted = [
        date(2026, 2, 9),
        date(2026, 2, 16),
        date(2026, 2, 19),
        date(2026, 2, 23),
        date(2026, 2, 24),
    ]
    return [
        f'{timezone.localdate(event.start_at).isoformat()} {event.title}'
        for event in ScheduleEvent.objects.filter(start_at__date__in=wanted).order_by('start_at', 'title')
    ]


def _ai_lecture_counts():
    counts = {}
    for event in ScheduleEvent.objects.filter(title='AI 강의 Ⅱ').order_by('start_at'):
        event_date = timezone.localdate(event.start_at).isoformat()
        counts[event_date] = counts.get(event_date, 0) + 1
    return [f'{event_date}={count}' for event_date, count in counts.items()]
