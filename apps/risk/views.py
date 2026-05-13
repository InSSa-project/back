from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common.utils.api_response import success_response

from .serializers import RiskStatusSerializer
from .services import RiskService


class RiskStatusView(APIView):
    permission_classes = [IsAuthenticated]
    service_class = RiskService

    def get(self, request):
        status = self.service_class().get_status(request.user)
        return success_response(RiskStatusSerializer(status).data)
