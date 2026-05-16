from django.conf import settings
from django.db import models


class SsafyDataImportLog(models.Model):
    IMPORT_JSON_UPLOAD = 'JSON_UPLOAD'
    IMPORT_CHROME_EXTENSION = 'CHROME_EXTENSION'
    IMPORT_MANUAL_INPUT = 'MANUAL_INPUT'

    STATUS_PENDING = 'PENDING'
    STATUS_PROCESSING = 'PROCESSING'
    STATUS_SUCCESS = 'SUCCESS'
    STATUS_FAILED = 'FAILED'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ssafy_data_import_logs')
    import_type = models.CharField(max_length=50)
    status = models.CharField(max_length=30, default=STATUS_PENDING)
    total_count = models.IntegerField(default=0)
    success_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class RawSsafyData(models.Model):
    SOURCE_NOTICE = 'NOTICE'
    SOURCE_EXAM = 'EXAM'
    SOURCE_ASSIGNMENT = 'ASSIGNMENT'
    SOURCE_REGULATION = 'REGULATION'
    SOURCE_SCORE = 'SCORE'
    SOURCE_MENTORING = 'MENTORING'

    import_log = models.ForeignKey(SsafyDataImportLog, on_delete=models.CASCADE, related_name='raw_ssafy_data')
    source_type = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    raw_json = models.JSONField(default=dict, blank=True)
    raw_text = models.TextField(blank=True)
    parsed_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title
