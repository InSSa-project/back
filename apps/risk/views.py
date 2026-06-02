from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.utils.api_response import success_response

from .models import EvaluationResult
from .serializers import EvaluationResultSerializer
from .services import RiskService


class RiskStatusView(APIView):
    permission_classes = [IsAuthenticated]
    service_class = RiskService

    def get(self, request):
        dashboard = self.service_class().calculate_dashboard(request.user)
        dashboard.pop('status_model', None)
        return success_response(dashboard)


class EvaluationResultListView(APIView):
    permission_classes = [IsAuthenticated]
    service_class = RiskService

    def get(self, request):
        evaluations = EvaluationResult.objects.filter(user=request.user).order_by('evaluation_type', 'round_number')
        return success_response(EvaluationResultSerializer(evaluations, many=True).data)

    def post(self, request):
        serializer = EvaluationResultSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        evaluation = self.service_class().upsert_evaluation(request.user, serializer.validated_data)
        return success_response(EvaluationResultSerializer(evaluation).data, status_code=status.HTTP_201_CREATED)


class EvaluationResultDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, evaluation_id):
        evaluation = self._get_evaluation(request.user, evaluation_id)
        if evaluation is None:
            return Response({'detail': 'Evaluation result not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = EvaluationResultSerializer(evaluation, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        RiskService().calculate_dashboard(request.user)
        return success_response(EvaluationResultSerializer(evaluation).data)

    def delete(self, request, evaluation_id):
        evaluation = self._get_evaluation(request.user, evaluation_id)
        if evaluation is None:
            return Response({'detail': 'Evaluation result not found.'}, status=status.HTTP_404_NOT_FOUND)
        evaluation.delete()
        RiskService().calculate_dashboard(request.user)
        return success_response({'detail': 'Evaluation result deleted.'})

    def _get_evaluation(self, user, evaluation_id):
        try:
            return EvaluationResult.objects.get(id=evaluation_id, user=user)
        except EvaluationResult.DoesNotExist:
            return None