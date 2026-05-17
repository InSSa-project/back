from .models import Notification


class NotificationService:
    def list_notifications(self, user):
        return Notification.objects.filter(user=user).order_by('-created_at')
