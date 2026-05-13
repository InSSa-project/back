from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from sync.services.import_service import run_sample_notice_import


@csrf_exempt
@require_POST
def run_crawl(request):
    job_log = run_sample_notice_import()
    return JsonResponse(
        {
            'status': job_log.status,
            'message': job_log.message,
            'raw_count': job_log.raw_count,
            'event_count': job_log.event_count,
            'failed_count': job_log.failed_count,
        }
    )

