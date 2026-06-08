import json

from django.http import Http404
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST
from rest_framework.exceptions import AuthenticationFailed

from apps.users.authentication import JwtAuthentication

from schedules.models import ScheduleEvent
from sync.models import CrawlJobLog, RawSsafyData
from sync.services.notice_normalizer import (
    CATEGORIES,
    SOURCE_TYPES,
    TRACKS,
    infer_notice_category,
    infer_notice_track,
    normalize_notice_category,
    notice_matches_search,
)
from sync.services.import_service import run_notice_import
from sync.services.manual_ocr_service import apply_manual_ocr_text


@require_GET
def raw_data_list(request):
    permission_error = _staff_permission_error(request)
    if permission_error:
        return permission_error
    queryset = RawSsafyData.objects.all().order_by('-collected_at')
    source_type = request.GET.get('source_type')
    category = request.GET.get('category')
    if source_type:
        queryset = queryset.filter(source_type=source_type)
    if category:
        queryset = queryset.filter(metadata_json__category=category)
    return JsonResponse(
        [
            {
                'id': raw_data.id,
                'source_type': raw_data.source_type,
                'source_url': raw_data.source_url,
                'title': raw_data.title,
                'category': (raw_data.metadata_json or {}).get('category'),
                'document_type': (raw_data.metadata_json or {}).get('document_type'),
                'ocr_status': (raw_data.metadata_json or {}).get('ocr_status'),
                'ocr_text_length': (raw_data.metadata_json or {}).get('ocr_text_length', 0),
            }
            for raw_data in queryset[:100]
        ],
        safe=False,
    )


def notice_list(request):
    queryset = RawSsafyData.objects.filter(source_type__in=SOURCE_TYPES)
    source_type = request.GET.get('source_type')
    category = request.GET.get('category')
    track = request.GET.get('track')
    search = request.GET.get('search', '').strip()

    if source_type:
        if source_type not in SOURCE_TYPES:
            return JsonResponse({'detail': 'Unsupported source_type.'}, status=400)
        queryset = queryset.filter(source_type=source_type)

    rows = list(queryset)
    category = normalize_notice_category(category)
    if category and category != 'all':
        if category not in CATEGORIES:
            return JsonResponse({'detail': 'Unsupported category.'}, status=400)
        rows = [row for row in rows if infer_notice_category(row) == category]
    if track:
        if track not in TRACKS:
            return JsonResponse({'detail': 'Unsupported track.'}, status=400)
        if category != 'mentoring':
            rows = [row for row in rows if infer_notice_track(row) in {track, 'common'}]
    if search:
        rows = [row for row in rows if notice_matches_search(row, search)]

    page = _positive_int(request.GET.get('page'), default=1)
    page_size = _positive_int(request.GET.get('page_size'), default=20, maximum=100)
    start = (page - 1) * page_size
    end = start + page_size

    return JsonResponse(
        {
            'count': len(rows),
            'page': page,
            'page_size': page_size,
            'results': [_serialize_notice(row) for row in rows[start:end]],
        }
    )


@require_GET
def notice_detail(request, raw_data_id):
    raw_data = RawSsafyData.objects.filter(pk=raw_data_id, source_type__in=SOURCE_TYPES).first()
    if raw_data is None:
        raise Http404('Notice not found.')
    return JsonResponse(_serialize_notice(raw_data, include_detail=True))


@require_POST
def run_crawl(request):
    permission_error = _staff_permission_error(request)
    if permission_error:
        return permission_error
    mode = _parse_mode(request)
    job_log = run_notice_import(mode=mode)
    response_status = 'error' if job_log.status == CrawlJobLog.STATUS_FAILED else job_log.status
    return JsonResponse(
        {
            'status': response_status,
            'message': job_log.message,
            'raw_count': job_log.raw_count,
            'event_count': job_log.event_count,
            'failed_count': job_log.failed_count,
            'skipped_count': job_log.skipped_count,
            'notice_count': job_log.notice_count,
            'academic_rule_count': job_log.academic_rule_count,
            'no_schedule_count': job_log.no_schedule_count,
            'image_count': job_log.image_count,
            'ocr_processed_count': job_log.ocr_processed_count,
            'ocr_failed_count': job_log.ocr_failed_count,
            'crawler_mode': job_log.crawler_mode,
        }
    )


@require_POST
def set_manual_ocr_text(request, raw_data_id):
    permission_error = _staff_permission_error(request)
    if permission_error:
        return permission_error

    try:
        payload = _parse_json_body(request)
    except ValueError as exc:
        return JsonResponse({'detail': str(exc)}, status=400)

    raw_data = RawSsafyData.objects.filter(pk=raw_data_id).first()
    if raw_data is None:
        raise Http404('RawSsafyData not found.')

    try:
        result = apply_manual_ocr_text(
            raw_data,
            payload.get('ocr_text', ''),
            reparse=bool(payload.get('reparse')),
        )
    except ValueError as exc:
        return JsonResponse({'detail': str(exc)}, status=400)

    return JsonResponse(
        {
            'status': 'success',
            'raw_data_id': result.raw_data_id,
            'ocr_provider': result.ocr_provider,
            'ocr_status': result.ocr_status,
            'ocr_text_length': result.ocr_text_length,
            'updated': result.updated,
            'reparse_created_count': result.reparse_created_count,
        }
    )


def _staff_permission_error(request):
    user = request.user
    if not getattr(user, 'is_authenticated', False) and request.META.get('HTTP_AUTHORIZATION'):
        try:
            authenticated = JwtAuthentication().authenticate(request)
        except AuthenticationFailed:
            authenticated = None
        if authenticated:
            user, _auth = authenticated
            request.user = user
    if not getattr(user, 'is_authenticated', False):
        return JsonResponse({'detail': 'Authentication credentials were not provided.'}, status=401)
    if not getattr(user, 'is_staff', False):
        return JsonResponse({'detail': 'Admin permission is required.'}, status=403)
    return None

def _parse_mode(request):
    if not request.body:
        return 'sample'

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return 'sample'

    return payload.get('mode') or 'sample'


def _parse_json_body(request):
    if not request.body:
        return {}

    try:
        return json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError as exc:
        raise ValueError('Invalid JSON body.') from exc


def _serialize_notice(raw_data, include_detail=False):
    payload = {
        'id': raw_data.id,
        'title': raw_data.title,
        'source_type': raw_data.source_type,
        'category': infer_notice_category(raw_data),
        'track': infer_notice_track(raw_data),
        'source_url': raw_data.source_url,
        'metadata_json': raw_data.metadata_json,
        'created_at': raw_data.collected_at.isoformat(),
        'updated_at': raw_data.collected_at.isoformat(),
    }
    if include_detail:
        payload.update(
            {
                'raw_json': raw_data.metadata_json,
                'ocr_text': raw_data.raw_text,
                'schedule_events': [
                    _serialize_notice_event(event)
                    for event in ScheduleEvent.objects.filter(raw_data=raw_data).order_by('start_at', 'id')
                ],
            }
        )
    return payload


def _serialize_notice_event(event):
    raw_data = event.raw_data
    return {
        'id': event.id,
        'title': event.title,
        'event_type': event.event_type,
        'start_at': event.start_at.isoformat(),
        'end_at': event.end_at.isoformat(),
        'source_url': raw_data.source_url if raw_data else None,
        'metadata_json': event.metadata_json,
    }


def _positive_int(value, default, maximum=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 1:
        return default
    return min(parsed, maximum) if maximum else parsed
