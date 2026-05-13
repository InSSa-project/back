from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common.utils.api_response import success_response

from .services import OcrService


class OcrUploadView(APIView):
    permission_classes = [IsAuthenticated]
    service_class = OcrService

    def post(self, request):
        result = self.service_class().extract_schedule_candidates(
            user=request.user,
            image=request.FILES.get('image'),
        )
        return success_response(result)
