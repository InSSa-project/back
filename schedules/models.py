from django.conf import settings
from django.db import models


class ScheduleEvent(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='owned_schedule_events',
    )
    raw_data = models.ForeignKey(
        'sync.RawSsafyData',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='schedule_events',
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    is_all_day = models.BooleanField(default=False)
    event_type = models.CharField(max_length=50, default='notice')
    source_type = models.CharField(max_length=50, default='notice')
    source_id = models.CharField(max_length=100, blank=True)
    metadata_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['start_at', 'id']
        indexes = [
            models.Index(fields=['start_at', 'end_at']),
            models.Index(fields=['event_type']),
            models.Index(fields=['source_type', 'source_id']),
        ]

    def __str__(self):
        return self.title

