from django.conf import settings
from django.db import models


class RiskStatus(models.Model):
    LEVEL_SAFE = 'SAFE'
    LEVEL_CAUTION = 'CAUTION'
    LEVEL_WARNING = 'WARNING'
    LEVEL_DANGER = 'DANGER'

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='risk_status')
    risk_level = models.CharField(max_length=20, default=LEVEL_SAFE)
    absent_count = models.IntegerField(default=0)
    fail_count = models.IntegerField(default=0)
    message = models.TextField(blank=True)
    calculated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class EvaluationResult(models.Model):
    TYPE_SUBJECT = 'subject'
    TYPE_MONTHLY = 'monthly'
    TYPE_CHOICES = [
        (TYPE_SUBJECT, 'Subject evaluation'),
        (TYPE_MONTHLY, 'Monthly evaluation'),
    ]

    STATUS_PASS = 'pass'
    STATUS_FAIL = 'fail'
    STATUS_RETAKE = 'retake'
    STATUS_ABSENT = 'absent'
    STATUS_SCHEDULED = 'scheduled'
    STATUS_CHOICES = [
        (STATUS_PASS, 'Pass'),
        (STATUS_FAIL, 'Fail'),
        (STATUS_RETAKE, 'Retake'),
        (STATUS_ABSENT, 'Absent'),
        (STATUS_SCHEDULED, 'Scheduled'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='evaluation_results')
    evaluation_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    round_number = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SCHEDULED)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['evaluation_type', 'round_number', 'id']
        constraints = [
            models.UniqueConstraint(fields=['user', 'evaluation_type', 'round_number'], name='unique_user_evaluation_round'),
        ]
        indexes = [
            models.Index(fields=['user', 'evaluation_type']),
            models.Index(fields=['scheduled_at']),
        ]

    def __str__(self):
        return self.title or f'{self.evaluation_type} #{self.round_number}'