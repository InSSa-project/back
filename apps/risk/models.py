from django.conf import settings
from django.db import models


class RiskStatus(models.Model):
    LEVEL_SAFE = 'SAFE'
    LEVEL_CAUTION = 'CAUTION'
    LEVEL_DANGER = 'DANGER'

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='risk_status')
    risk_level = models.CharField(max_length=20, default=LEVEL_SAFE)
    absent_count = models.IntegerField(default=0)
    fail_count = models.IntegerField(default=0)
    message = models.TextField(blank=True)
    calculated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
