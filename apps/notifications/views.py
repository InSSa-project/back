from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common.utils.api_response import success_response

from .serializers import NotificationSerializer
from .services import NotificationService


class NotificationListView(APIView):
    permission_classes = [IsAuthenticated]
    service_class = NotificationService

    def get(self, request):
        notifications = self.service_class().list_notifications(request.user)
        return success_response(NotificationSerializer(notifications, many=True).data)


class NotificationMarkAllReadView(APIView):
    permission_classes = [IsAuthenticated]
    service_class = NotificationService

    def post(self, request):
        self.service_class().mark_all_read(request.user)
        return success_response({'marked_read': True})


class NotificationMarkReadView(APIView):
    permission_classes = [IsAuthenticated]
    service_class = NotificationService

    def post(self, request, notification_id):
        updated = self.service_class().mark_read(request.user, notification_id)
        return success_response({'marked_read': bool(updated), 'id': notification_id})
