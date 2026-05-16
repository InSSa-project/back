from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common.utils.api_response import success_response

from .serializers import SsafyDataImportLogSerializer
from .services import NoticeImportService


class SsafySyncView(APIView):
    permission_classes = [IsAuthenticated]
    service_class = NoticeImportService

    def post(self, request):
        import_log = self.service_class().create_import_log(
            user=request.user,
            import_type=request.data.get('type', 'MANUAL_INPUT'),
            items=request.data.get('items') or [],
            ingest=request.data.get('ingest', True),
        )
        return success_response(SsafyDataImportLogSerializer(import_log).data)
