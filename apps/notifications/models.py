from django.conf import settings
from django.db import models


class Notification(models.Model):
    TYPE_D_DAY = 'D_DAY'
    TYPE_EXAM = 'EXAM'
    TYPE_WARNING = 'WARNING'
    TYPE_PROJECT = 'PROJECT'
    TYPE_SYSTEM = 'SYSTEM'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    schedule_event = models.ForeignKey(
        'calendar.ScheduleEvent',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='notifications',
    )
    notification_type = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True)
    is_read = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
