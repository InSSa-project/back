from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from schedules.models import ScheduleEvent
from sync.models import RawSsafyData


class Command(BaseCommand):
    help = 'Link unlinked ScheduleEvent rows to matching RawSsafyData rows.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Show linkable rows without saving changes.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        checked_count = 0
        linked_count = 0
        skipped_count = 0

        queryset = ScheduleEvent.objects.filter(raw_data__isnull=True).order_by('id')
        with transaction.atomic():
            for event in queryset:
                checked_count += 1
                raw_data = _find_matching_raw_data(event)
                if raw_data is None:
                    skipped_count += 1
                    continue

                linked_count += 1
                self.stdout.write(f'link event_id={event.id} raw_data_id={raw_data.id} title={event.title}')
                if not dry_run:
                    event.raw_data = raw_data
                    event.source_type = raw_data.source_type
                    event.source_id = str(raw_data.pk)
                    event.save(update_fields=['raw_data', 'source_type', 'source_id', 'updated_at'])

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                'Link completed.\n'
                f'checked_count={checked_count}\n'
                f'linked_count={linked_count}\n'
                f'skipped_count={skipped_count}\n'
                f'dry_run={str(dry_run).lower()}'
            )
        )


def _find_matching_raw_data(event):
    candidates = RawSsafyData.objects.filter(source_type=event.source_type).order_by('id')
    source_id_match = _match_by_source_id(event, candidates)
    if source_id_match:
        return source_id_match

    scored = []
    for raw_data in candidates:
        if _is_sample_raw(raw_data) and not _is_sample_event(event):
            continue
        score = _match_score(event, raw_data)
        if score >= 2:
            scored.append((score, raw_data.id, raw_data))

    if not scored:
        return None

    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][2]


def _match_by_source_id(event, candidates):
    if not event.source_id or not event.source_id.isdigit():
        return None
    raw_data = candidates.filter(pk=int(event.source_id)).first()
    if raw_data and (_is_sample_raw(raw_data) == _is_sample_event(event) or not _is_sample_raw(raw_data)):
        return raw_data
    return None


def _match_score(event, raw_data):
    text = f'{raw_data.title}\n{raw_data.raw_text}'
    score = 0
    if event.title and event.title in text:
        score += 2
    if any(date_string in raw_data.raw_text for date_string in _event_date_strings(event)):
        score += 1
    if event.source_type == raw_data.source_type:
        score += 1
    if _is_sample_event(event) and _is_sample_raw(raw_data):
        score += 2
    return score


def _event_date_strings(event):
    local_start = timezone.localtime(event.start_at)
    return {
        local_start.strftime('%Y.%m.%d'),
        local_start.strftime('%Y-%m-%d'),
        local_start.strftime('%Y/%m/%d'),
        f'{local_start.month}.{local_start.day}',
        f'{local_start.month}월 {local_start.day}일',
        f'{local_start.month}월{local_start.day}일',
    }


def _is_sample_raw(raw_data):
    return raw_data.source_url.startswith('https://sample.ssafy.local') or raw_data.metadata_json.get('collected_from') == 'sample'


def _is_sample_event(event):
    return event.title in {'월말평가', '프로젝트 제출 마감', '취업 특강'} or event.source_id in {'1', '2', '3'}
