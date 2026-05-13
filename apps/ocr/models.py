from django.conf import settings
from django.db import models


class OcrResult(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ocr_results')
    raw_data = models.ForeignKey(
        'notices.RawSsafyData',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='ocr_results',
    )
    image = models.FileField(upload_to='ocr/%Y/%m/%d/', null=True, blank=True)
    raw_text = models.TextField(blank=True)
    parsed_json = models.JSONField(default=dict, blank=True)
    is_confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
