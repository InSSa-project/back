from datetime import date, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from schedules.models import ScheduleEvent


HOLIDAYS_BY_YEAR = {
    2026: [
        ('신정', date(2026, 1, 1)),
        ('설날 연휴', date(2026, 2, 16)),
        ('설날', date(2026, 2, 17)),
        ('설날 연휴', date(2026, 2, 18)),
        ('삼일절 대체공휴일', date(2026, 3, 2)),
        ('어린이날', date(2026, 5, 5)),
        ('부처님 오신 날 대체공휴일', date(2026, 5, 25)),
        ('전국동시지방선거', date(2026, 6, 3)),
        ('현충일', date(2026, 6, 6)),
        ('광복절', date(2026, 8, 15)),
        ('추석 연휴', date(2026, 9, 24)),
        ('추석', date(2026, 9, 25)),
        ('추석 연휴', date(2026, 9, 26)),
        ('개천절', date(2026, 10, 3)),
        ('한글날', date(2026, 10, 9)),
        ('성탄절', date(2026, 12, 25)),
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
        holidays = HOLIDAYS_BY_YEAR.get(year)
        if holidays is None:
            raise CommandError(f'Unsupported holiday year: {year}')

        created_count = 0
        skipped_count = 0
        for title, holiday_date in holidays:
            start_at = _aware(holiday_date)
            exists = ScheduleEvent.objects.filter(
                title=title,
                start_at=start_at,
                event_type='holiday',
                source_type='seed',
            ).exists()
            if exists:
                skipped_count += 1
                continue

            created_count += 1
            if options['dry_run']:
                continue
            ScheduleEvent.objects.create(
                title=title,
                description='대한민국 공휴일 seed 데이터',
                start_at=start_at,
                end_at=start_at + timedelta(days=1),
                is_all_day=True,
                event_type='holiday',
                source_type='seed',
                metadata_json={'seed': 'korean_holidays', 'year': year},
            )

        if options['dry_run']:
            transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                'Korean holidays seed completed.\n'
                f'year={year}\n'
                f'created_count={created_count}\n'
                f'skipped_count={skipped_count}\n'
                f'dry_run={str(options["dry_run"]).lower()}'
            )
        )


def _aware(day):
    return timezone.make_aware(timezone.datetime.combine(day, time.min), timezone.get_current_timezone())
