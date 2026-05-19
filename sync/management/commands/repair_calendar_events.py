from dataclasses import dataclass, field
from datetime import date, time, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from schedules.models import ScheduleEvent
from sync.models import RawSsafyData


REPAIR_SOURCE = 'manual_calendar_correction'
MANUAL_EXAM_REPAIR_SOURCE = 'manual_exam_correction'
MANUAL_EXAM_REASON = 'evaluation_notice_missing_manual_mvp_seed'
NOISE_TITLES = {'시간', 'ViewModel', 'without questions', 'Live 방송'}


@dataclass
class RepairSummary:
    deleted_count: int = 0
    created_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    dry_run: bool = False
    deleted_titles: list = field(default_factory=list)
    key_events: list = field(default_factory=list)


class Command(BaseCommand):
    help = 'Repair generated SSAFY calendar events without touching user-created events.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Print changes without writing them.')

    def handle(self, *args, **options):
        summary = repair_calendar_events(dry_run=options['dry_run'])
        self.stdout.write(
            self.style.SUCCESS(
                'Calendar repair completed.\n'
                f'deleted_count={summary.deleted_count}\n'
                f'updated_count={summary.updated_count}\n'
                f'created_count={summary.created_count}\n'
                f'skipped_count={summary.skipped_count}\n'
                f'deleted_titles={"; ".join(summary.deleted_titles[:20]) or "none"}\n'
                f'key_events={"; ".join(summary.key_events[:40]) or "none"}\n'
                f'dry_run={str(summary.dry_run).lower()}'
            )
        )


@transaction.atomic
def repair_calendar_events(dry_run=False):
    summary = RepairSummary(dry_run=dry_run)
    base_raw = _find_base_schedule_raw()
    evaluation_raw = _find_evaluation_raw()

    _delete_false_positives(summary, dry_run=dry_run)
    _normalize_existing_titles(summary, dry_run=dry_run)
    _upsert_base_corrections(summary, base_raw, dry_run=dry_run)
    _upsert_exam_corrections(summary, evaluation_raw or base_raw, dry_run=dry_run)

    _dedupe_generated(summary, dry_run=dry_run)
    summary.key_events = _key_events()
    if dry_run:
        transaction.set_rollback(True)
    return summary


def _delete_false_positives(summary, dry_run=False):
    qs = ScheduleEvent.objects.filter(raw_data__isnull=False)
    targets = qs.filter(title__in=NOISE_TITLES)
    targets = targets | qs.filter(start_at__date__in=[date(2026, 1, 31), date(2026, 5, 2), date(2026, 5, 3)])
    targets = targets | qs.filter(start_at__date=date(2026, 5, 5)).exclude(title='어린이날')
    targets = targets | qs.filter(start_at__date=date(2026, 1, 7)).filter(title__contains='소명')
    targets = targets | qs.filter(title__contains='오후 ) AI 강의')
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
        ('SSAFY DAY', date(2026, 1, 25), date(2026, 1, 26), 'etc', {}),
        ('SSAFY DAY', date(2026, 1, 26), date(2026, 1, 27), 'etc', {}),
        ('SSAFY DAY', date(2026, 1, 27), date(2026, 1, 28), 'etc', {}),
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
        event_type = '' if event_date == date(2026, 1, 15) and event.title == '15기 SW AI 스타트 캠프' else event.event_type
        key = (event_date, event_type, _normalize_event_title(event.title), track)
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
    for event in ScheduleEvent.objects.filter(start_at=_aware(start_date), event_type=event_type):
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


def _find_base_schedule_raw():
    return RawSsafyData.objects.filter(title__contains='15기 1학기 전체 일정').order_by('id').first()


def _find_evaluation_raw():
    return (
        RawSsafyData.objects.filter(title__contains='과목월말평가 안내').order_by('id').first()
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
