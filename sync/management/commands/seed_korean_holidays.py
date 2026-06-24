import re
from datetime import date, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from schedules.models import ScheduleEvent


NATIONAL_HOLIDAY_SOURCE_TYPE = 'national_holiday'
HOLIDAY_EVENT_TYPE = 'holiday'


SUPPORTED_KOREAN_HOLIDAYS = {
    2026: [
        ('신정', 1, 1),
        ('설날 연휴', 2, 16),
        ('설날', 2, 17),
        ('설날 연휴', 2, 18),
        ('삼일절 대체공휴일', 3, 2),
        ('어린이날', 5, 5),
        ('부처님 오신 날 대체공휴일', 5, 25),
        ('전국동시지방선거', 6, 3),
        ('현충일', 6, 6),
        ('광복절', 8, 15),
        ('추석 연휴', 9, 24),
        ('추석', 9, 25),
        ('추석 연휴', 9, 26),
        ('개천절', 10, 3),
        ('한글날', 10, 9),
        ('성탄절', 12, 25),
    ],
    2027: [
        ('신정', 1, 1),
        ('설날 연휴', 2, 6),
        ('설날', 2, 7),
        ('설날 연휴', 2, 8),
        ('삼일절', 3, 1),
        ('어린이날', 5, 5),
        ('부처님 오신 날', 5, 13),
        ('현충일', 6, 6),
        ('광복절 대체공휴일', 8, 16),
        ('추석 연휴', 9, 14),
        ('추석', 9, 15),
        ('추석 연휴', 9, 16),
        ('개천절 대체공휴일', 10, 4),
        ('한글날', 10, 9),
        ('성탄절', 12, 25),
    ],
}


class Command(BaseCommand):
    help = 'Seed Korean public holidays as calendar events.'

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, default=2026, help='Holiday year to seed.')
        parser.add_argument('--dry-run', action='store_true', help='Print changes without writing rows.')

    @transaction.atomic
    def handle(self, *args, **options):
        year = options['year']
        try:
            holidays = get_korean_holidays(year)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        created_count = 0
        skipped_count = 0
        removed_ssafy_duplicate_count = 0
        for title, holiday_date in holidays:
            start_at = _aware(holiday_date)
            end_at = start_at + timedelta(days=1)
            existing = _find_existing_national_holiday(title, holiday_date)
            if existing:
                metadata = dict(existing.metadata_json or {})
                metadata.update(_holiday_metadata(year, title, holiday_date))
                changed = (
                    existing.description != ''
                    or existing.source_type != NATIONAL_HOLIDAY_SOURCE_TYPE
                    or existing.event_type != HOLIDAY_EVENT_TYPE
                    or existing.end_at != end_at
                    or existing.metadata_json != metadata
                )
                if changed and not options['dry_run']:
                    existing.description = ''
                    existing.source_type = NATIONAL_HOLIDAY_SOURCE_TYPE
                    existing.event_type = HOLIDAY_EVENT_TYPE
                    existing.end_at = end_at
                    existing.metadata_json = metadata
                    existing.save(update_fields=['description', 'source_type', 'event_type', 'end_at', 'metadata_json', 'updated_at'])
                skipped_count += 1
                removed_ssafy_duplicate_count += _remove_ssafy_holiday_duplicates(
                    title,
                    holiday_date,
                    keep_id=existing.id,
                    dry_run=options['dry_run'],
                )
                continue

            created_count += 1
            removed_ssafy_duplicate_count += _remove_ssafy_holiday_duplicates(
                title,
                holiday_date,
                keep_id=None,
                dry_run=options['dry_run'],
            )
            if options['dry_run']:
                continue
            ScheduleEvent.objects.create(
                title=title,
                description='',
                start_at=start_at,
                end_at=end_at,
                is_all_day=True,
                event_type=HOLIDAY_EVENT_TYPE,
                source_type=NATIONAL_HOLIDAY_SOURCE_TYPE,
                metadata_json=_holiday_metadata(year, title, holiday_date),
            )

        if options['dry_run']:
            transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                'Korean holidays seed completed.\n'
                f'year={year}\n'
                f'created_count={created_count}\n'
                f'skipped_count={skipped_count}\n'
                f'removed_ssafy_duplicate_count={removed_ssafy_duplicate_count}\n'
                f'dry_run={str(options["dry_run"]).lower()}'
            )
        )


def get_korean_holidays(year):
    fixtures = SUPPORTED_KOREAN_HOLIDAYS.get(year)
    if fixtures is None:
        supported_years = ', '.join(str(value) for value in sorted(SUPPORTED_KOREAN_HOLIDAYS))
        raise ValueError(f'Unsupported Korean holiday year: {year}. Supported years: {supported_years}')
    return [(title, date(year, month, day)) for title, month, day in fixtures]


def _aware(day):
    return timezone.make_aware(timezone.datetime.combine(day, time.min), timezone.get_current_timezone())


def _holiday_metadata(year, title, holiday_date):
    return {
        'provider': 'fixture',
        'provider_name': 'project_supported_korean_holidays',
        'source': 'korean_public_holiday_provider',
        'source_type': NATIONAL_HOLIDAY_SOURCE_TYPE,
        'year': year,
        'date': holiday_date.isoformat(),
        'canonical_key': _holiday_key(title, holiday_date),
        'is_public_holiday': True,
        'description_policy': 'empty',
    }


def _find_existing_national_holiday(title, holiday_date):
    candidates = ScheduleEvent.objects.filter(
        start_at__date=holiday_date,
        event_type=HOLIDAY_EVENT_TYPE,
        source_type__in=[NATIONAL_HOLIDAY_SOURCE_TYPE, 'seed', 'holiday'],
    ).order_by('id')
    target_key = _holiday_key(title, holiday_date)
    for event in candidates:
        if _holiday_key(event.title, timezone.localdate(event.start_at)) == target_key:
            return event
    return None


def _remove_ssafy_holiday_duplicates(title, holiday_date, keep_id=None, dry_run=False):
    target_key = _holiday_key(title, holiday_date)
    candidates = ScheduleEvent.objects.filter(
        start_at__date=holiday_date,
        event_type=HOLIDAY_EVENT_TYPE,
    ).exclude(source_type__in=[NATIONAL_HOLIDAY_SOURCE_TYPE, 'seed', 'holiday'])
    if keep_id:
        candidates = candidates.exclude(id=keep_id)

    duplicate_ids = [
        event.id
        for event in candidates
        if _holiday_key(event.title, timezone.localdate(event.start_at)) == target_key
    ]
    if duplicate_ids and not dry_run:
        ScheduleEvent.objects.filter(id__in=duplicate_ids).delete()
    return len(duplicate_ids)


def _holiday_key(title, holiday_date):
    normalized_title = _normalize_holiday_title(title)
    substitute = 'substitute' if _is_substitute_holiday_title(title) else 'regular'
    date_value = holiday_date.isoformat() if holiday_date else ''
    return f'{date_value}:{normalized_title}:{substitute}'


def _normalize_holiday_title(title):
    text = re.sub(r'\s+', ' ', str(title or '')).strip()
    text = re.sub(r'\((?:공휴일|휴일)\)', '', text)
    text = re.sub(r'(?:공휴일|휴일)$', '', text).strip()
    return re.sub(r'[\s()]+', '', text).lower()


def _is_substitute_holiday_title(title):
    text = str(title or '')
    return '대체' in text
