import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from sync.models import CrawlJobLog
from sync.services.import_service import run_notice_import


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
