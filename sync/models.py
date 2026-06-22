from django.conf import settings
from django.db import models
from django.utils import timezone


class RawSsafyData(models.Model):
    STATUS_COLLECTED = 'collected'
    STATUS_PARSED = 'parsed'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_COLLECTED, 'Collected'),
        (STATUS_PARSED, 'Parsed'),
        (STATUS_FAILED, 'Failed'),
    ]

    source_type = models.CharField(max_length=50)
    source_url = models.URLField(blank=True)
    title = models.CharField(max_length=255)
    raw_text = models.TextField(blank=True)
    raw_html = models.TextField(blank=True)
    ocr_boxes = models.JSONField(default=list, blank=True)
    collected_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_COLLECTED)
    metadata_json = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-collected_at']
        indexes = [
            models.Index(fields=['source_type', 'status']),
            models.Index(fields=['collected_at']),
        ]

    def __str__(self):
        return self.title


class CrawlJobLog(models.Model):
    STATUS_RUNNING = 'running'
    STATUS_SUCCESS = 'success'
    STATUS_PARTIAL_SUCCESS = 'partial_success'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_RUNNING, 'Running'),
        (STATUS_SUCCESS, 'Success'),
        (STATUS_FAILED, 'Failed'),
    ]

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    message = models.TextField(blank=True)
    raw_count = models.PositiveIntegerField(default=0)
    event_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    notice_count = models.PositiveIntegerField(default=0)
    academic_rule_count = models.PositiveIntegerField(default=0)
    no_schedule_count = models.PositiveIntegerField(default=0)
    image_count = models.PositiveIntegerField(default=0)
    ocr_processed_count = models.PositiveIntegerField(default=0)
    ocr_failed_count = models.PositiveIntegerField(default=0)
    crawler_mode = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f'{self.status} ({self.started_at:%Y-%m-%d %H:%M:%S})'


class UserNoticeReadStatus(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notice_read_statuses',
    )
    raw_data = models.ForeignKey(
        RawSsafyData,
        on_delete=models.CASCADE,
        related_name='read_statuses',
    )
    read_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'raw_data'], name='unique_user_notice_read_status'),
        ]
        indexes = [
            models.Index(fields=['user', 'read_at']),
        ]

    def __str__(self):
        return f'{self.user_id}:{self.raw_data_id}'

