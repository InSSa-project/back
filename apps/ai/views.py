from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common.utils.api_response import success_response

from .models import AiQualityLog
from .services import AIService


class AiChatView(APIView):
    permission_classes = [IsAuthenticated]
    service_class = AIService

    def post(self, request):
        result = self.service_class().answer(
            user=request.user,
            message=request.data.get('message', ''),
            session_id=request.data.get('session_id'),
        )
        return success_response(result)


class AiQualitySummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = _quality_queryset(request)
        aggregate = queryset.aggregate(
            total_runs=Count('id'),
            avg_quality_score=Avg('quality_score'),
            avg_latency_ms=Avg('latency_ms'),
            success_count=Count('id', filter=Q(pipeline_run__is_success=True)),
            fallback_count=Count('id', filter=Q(is_fallback=True)),
            error_count=Count('id', filter=Q(is_error=True)),
            no_context_count=Count('id', filter=Q(is_no_context=True)),
            avg_retrieved_count=Avg('retrieved_count'),
        )
        total = aggregate['total_runs'] or 0
        return success_response(
            {
                'window_days': _days_param(request),
                'total_runs': total,
                'avg_quality_score': _round_metric(aggregate['avg_quality_score']),
                'avg_latency_ms': _round_metric(aggregate['avg_latency_ms']),
                'avg_retrieved_count': _round_metric(aggregate['avg_retrieved_count']),
                'success_rate': _ratio(aggregate['success_count'], total),
                'fallback_rate': _ratio(aggregate['fallback_count'], total),
                'error_rate': _ratio(aggregate['error_count'], total),
                'no_context_rate': _ratio(aggregate['no_context_count'], total),
            }
        )


class AiQualityTimeseriesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = (
            _quality_queryset(request)
            .annotate(day=TruncDate('created_at'))
            .values('day')
            .annotate(
                total_runs=Count('id'),
                avg_quality_score=Avg('quality_score'),
                avg_latency_ms=Avg('latency_ms'),
                success_count=Count('id', filter=Q(pipeline_run__is_success=True)),
                fallback_count=Count('id', filter=Q(is_fallback=True)),
                error_count=Count('id', filter=Q(is_error=True)),
            )
            .order_by('day')
        )
        items = []
        for row in rows:
            total = row['total_runs'] or 0
            items.append(
                {
                    'date': row['day'].isoformat() if row['day'] else '',
                    'total_runs': total,
                    'avg_quality_score': _round_metric(row['avg_quality_score']),
                    'avg_latency_ms': _round_metric(row['avg_latency_ms']),
                    'success_rate': _ratio(row['success_count'], total),
                    'fallback_rate': _ratio(row['fallback_count'], total),
                    'error_rate': _ratio(row['error_count'], total),
                }
            )
        return success_response({'window_days': _days_param(request), 'items': items})


class AiQualityByIntentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = (
            _quality_queryset(request)
            .values('pipeline_run__intent')
            .annotate(
                total_runs=Count('id'),
                avg_quality_score=Avg('quality_score'),
                avg_latency_ms=Avg('latency_ms'),
                success_count=Count('id', filter=Q(pipeline_run__is_success=True)),
                fallback_count=Count('id', filter=Q(is_fallback=True)),
                error_count=Count('id', filter=Q(is_error=True)),
            )
            .order_by('-total_runs', 'pipeline_run__intent')
        )
        items = []
        for row in rows:
            total = row['total_runs'] or 0
            items.append(
                {
                    'intent': row['pipeline_run__intent'] or 'unknown',
                    'total_runs': total,
                    'avg_quality_score': _round_metric(row['avg_quality_score']),
                    'avg_latency_ms': _round_metric(row['avg_latency_ms']),
                    'success_rate': _ratio(row['success_count'], total),
                    'fallback_rate': _ratio(row['fallback_count'], total),
                    'error_rate': _ratio(row['error_count'], total),
                }
            )
        return success_response({'window_days': _days_param(request), 'items': items})


def _quality_queryset(request):
    days = _days_param(request)
    since = timezone.now() - timezone.timedelta(days=days)
    queryset = AiQualityLog.objects.select_related('pipeline_run', 'user').filter(created_at__gte=since)
    if not request.user.is_staff:
        queryset = queryset.filter(user=request.user)
    return queryset


def _days_param(request):
    try:
        days = int(request.query_params.get('days', 30))
    except (TypeError, ValueError):
        days = 30
    return max(1, min(days, 180))


def _ratio(count, total):
    if not total:
        return 0.0
    return round((count or 0) / total, 4)


def _round_metric(value):
    if value is None:
        return 0.0
    return round(float(value), 3)
