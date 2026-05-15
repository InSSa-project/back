import json

from django.http import Http404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from sync.models import CrawlJobLog, RawSsafyData
from sync.services.import_service import run_notice_import
from sync.services.manual_ocr_service import apply_manual_ocr_text


@csrf_exempt
@require_POST
def run_crawl(request):
    mode = _parse_mode(request)
    # TODO: ssafy_notice mode should be restricted to administrators before production use.
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


@csrf_exempt
@require_POST
def set_manual_ocr_text(request, raw_data_id):
    if not request.user.is_authenticated or not request.user.is_staff:
        return JsonResponse({'detail': 'Admin permission is required.'}, status=403)

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
