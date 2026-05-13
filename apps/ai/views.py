from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common.utils.api_response import success_response

from .services import AiChatService


class AiChatView(APIView):
    permission_classes = [IsAuthenticated]
    service_class = AiChatService

    def post(self, request):
        result = self.service_class().answer(
            user=request.user,
            message=request.data.get('message', ''),
        )
        return success_response(result)
