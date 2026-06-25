from django.conf import settings
from django.db import models


class ScheduleEvent(models.Model):
    SOURCE_OCR = 'OCR'
    SOURCE_CRAWLING = 'CRAWLING'
    SOURCE_MANUAL = 'MANUAL'
    SOURCE_AI = 'AI'

    EVENT_MONTHLY_TEST = 'MONTHLY_TEST'
    EVENT_SUBJECT_TEST = 'SUBJECT_TEST'
    EVENT_PROJECT = 'PROJECT'
    EVENT_LECTURE = 'LECTURE'
    EVENT_APPLICATION = 'APPLICATION'
    EVENT_PERSONAL = 'PERSONAL'

    SOURCE_TYPE_CHOICES = [
        (SOURCE_OCR, 'OCR'),
        (SOURCE_CRAWLING, 'Crawling'),
        (SOURCE_MANUAL, 'Manual'),
        (SOURCE_AI, 'AI'),
    ]
    EVENT_TYPE_CHOICES = [
        (EVENT_MONTHLY_TEST, 'Monthly Test'),
        (EVENT_SUBJECT_TEST, 'Subject Test'),
        (EVENT_PROJECT, 'Project'),
        (EVENT_LECTURE, 'Lecture'),
        (EVENT_APPLICATION, 'Application'),
        (EVENT_PERSONAL, 'Personal'),
    ]

    raw_data = models.ForeignKey(
        'notices.RawSsafyData',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='schedule_events',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_schedule_events',
    )
    source_type = models.CharField(max_length=50, choices=SOURCE_TYPE_CHOICES)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    event_type = models.CharField(max_length=50, choices=EVENT_TYPE_CHOICES)
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)
    deadline_at = models.DateTimeField(null=True, blank=True)
    is_global = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['start_at', 'deadline_at', 'id']

    def __str__(self):
        return self.title


class UserScheduleEvent(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='user_schedule_events')
    schedule_event = models.ForeignKey('schedules.ScheduleEvent', on_delete=models.CASCADE, related_name='user_schedule_events')
    is_done = models.BooleanField(default=False)
    memo = models.TextField(blank=True)
    override_title = models.CharField(max_length=255, null=True, blank=True)
    override_description = models.TextField(null=True, blank=True)
    override_event_type = models.CharField(max_length=50, null=True, blank=True)
    override_start_at = models.DateTimeField(null=True, blank=True)
    override_end_at = models.DateTimeField(null=True, blank=True)
    override_is_all_day = models.BooleanField(null=True, blank=True)
    is_hidden = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'is_hidden']),
        ]
        constraints = [
            models.UniqueConstraint(fields=['user', 'schedule_event'], name='unique_user_schedule_event'),
        ]


class HiddenCalendarEvent(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='hidden_calendar_events')
    schedule_event = models.ForeignKey(
        'schedules.ScheduleEvent',
        on_delete=models.CASCADE,
        related_name='hidden_calendar_events',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'schedule_event'], name='unique_hidden_calendar_event'),
        ]
