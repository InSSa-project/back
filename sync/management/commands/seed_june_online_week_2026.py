from datetime import date, time, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from schedules.models import ScheduleEvent


ONLINE_WEEK_DATES = [
    date(2026, 6, 2),
    date(2026, 6, 5),
    date(2026, 6, 8),
    date(2026, 6, 9),
    date(2026, 6, 10),
    date(2026, 6, 11),
    date(2026, 6, 12),
]
TITLE = '온라인 위크'
SEED_SOURCE_TYPE = 'manual_seed'
SEED_REASON = 'online_week_recovery'


class Command(BaseCommand):
    help = 'Seed the trusted June 2026 online week recovery events.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Print changes without writing rows.')

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        created_count = 0
        skipped_count = 0
        duplicate_seed_removed_count = 0
        for event_date in ONLINE_WEEK_DATES:
            start_at = _aware(event_date)
            existing_non_seed = ScheduleEvent.objects.filter(
                title=TITLE,
                start_at=start_at,
            ).exclude(source_type=SEED_SOURCE_TYPE)
            seed_rows = ScheduleEvent.objects.filter(
                title=TITLE,
                start_at=start_at,
                event_type='etc',
                source_type=SEED_SOURCE_TYPE,
                metadata_json__reason=SEED_REASON,
            )
            if existing_non_seed.exists():
                duplicate_seed_removed_count += seed_rows.count()
                skipped_count += 1
                if not dry_run and seed_rows.exists():
                    seed_rows.delete()
                continue
            if seed_rows.exists():
                skipped_count += 1
                continue

            created_count += 1
            if dry_run:
                continue
            ScheduleEvent.objects.create(
                title=TITLE,
                description='관리자 복구 seed로 생성한 2026년 6월 온라인 위크 일정',
                start_at=start_at,
                end_at=start_at + timedelta(days=1),
                is_all_day=True,
                event_type='etc',
                source_type=SEED_SOURCE_TYPE,
                source_id='online_week_2026_06',
                metadata_json={
                    'source_type': SEED_SOURCE_TYPE,
                    'reason': SEED_REASON,
                    'date_mapping_source': 'manual_seed',
                    'confidence': 1.0,
                },
            )

        if dry_run:
            transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                'June online week seed completed.\n'
                f'created_count={created_count}\n'
                f'skipped_count={skipped_count}\n'
                f'duplicate_seed_removed_count={duplicate_seed_removed_count}\n'
                f'dry_run={str(dry_run).lower()}'
            )
        )


def _aware(day):
    return timezone.make_aware(timezone.datetime.combine(day, time.min), timezone.get_current_timezone())
