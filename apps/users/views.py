from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common.utils.api_response import success_response

from .serializers import UserSerializer
from .services import UserService


class MeView(APIView):
    permission_classes = [IsAuthenticated]
    service_class = UserService

    def get(self, request):
        user = self.service_class().get_profile(request.user)
        return success_response(UserSerializer(user).data)
