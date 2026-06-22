from django.conf import settings
from django.db import models


class AiDocument(models.Model):
    EMBEDDING_PENDING = 'PENDING'
    EMBEDDING_SUCCESS = 'SUCCESS'
    EMBEDDING_FAILED = 'FAILED'

    raw_data = models.ForeignKey(
        'notices.RawSsafyData',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='ai_documents',
    )
    sync_raw_data = models.ForeignKey(
        'sync.RawSsafyData',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='ai_documents',
    )
    schedule_event = models.ForeignKey(
        'schedules.ScheduleEvent',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='ai_documents',
    )
    title = models.CharField(max_length=255)
    content = models.TextField()
    document_type = models.CharField(max_length=50)
    metadata_json = models.JSONField(default=dict, blank=True)
    embedding_status = models.CharField(max_length=30, default=EMBEDDING_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

    @property
    def canonical_raw_data_id(self):
        return self.sync_raw_data_id or self.raw_data_id


class ChatSession(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='chat_sessions')
    title = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)


class ChatMessage(models.Model):
    ROLE_SYSTEM = 'SYSTEM'
    ROLE_USER = 'USER'
    ROLE_ASSISTANT = 'ASSISTANT'

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=20)
    content = models.TextField()
    prompt = models.TextField(blank=True)
    usage_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AiChatReference(models.Model):
    chat_message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name='references')
    ai_document = models.ForeignKey(AiDocument, on_delete=models.CASCADE, related_name='chat_references')
    relevance_score = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class AiPipelineRun(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='ai_pipeline_runs')
    session = models.ForeignKey(ChatSession, null=True, blank=True, on_delete=models.SET_NULL, related_name='pipeline_runs')
    question = models.TextField()
    answer = models.TextField(blank=True)
    intent = models.CharField(max_length=100, blank=True)
    query_type = models.CharField(max_length=100, blank=True)
    answer_policy = models.CharField(max_length=100, blank=True)
    route_stage = models.CharField(max_length=100, blank=True)
    failure_type = models.CharField(max_length=100, blank=True)
    is_success = models.BooleanField(default=True)
    retrieved_context_json = models.JSONField(default=dict, blank=True)
    references_json = models.JSONField(default=list, blank=True)
    usage_json = models.JSONField(default=dict, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['created_at'], name='ai_aipipeli_created_034326_idx'),
            models.Index(fields=['intent'], name='ai_aipipeli_intent_184f80_idx'),
            models.Index(fields=['answer_policy'], name='ai_aipipeli_answer__e28803_idx'),
            models.Index(fields=['route_stage'], name='ai_aipipeli_route_s_369394_idx'),
            models.Index(fields=['is_success'], name='ai_aipipeli_is_succ_6d2c7f_idx'),
        ]


class AiQualityLog(models.Model):
    FEEDBACK_NONE = 'none'
    FEEDBACK_POSITIVE = 'positive'
    FEEDBACK_NEGATIVE = 'negative'
    FEEDBACK_CORRECTED = 'corrected'

    pipeline_run = models.OneToOneField(AiPipelineRun, on_delete=models.CASCADE, related_name='quality_log')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='ai_quality_logs')
    evaluator = models.CharField(max_length=50, default='auto')
    quality_score = models.FloatField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    retrieved_count = models.PositiveIntegerField(default=0)
    is_fallback = models.BooleanField(default=False)
    is_error = models.BooleanField(default=False)
    is_no_context = models.BooleanField(default=False)
    feedback_type = models.CharField(max_length=30, default=FEEDBACK_NONE)
    feedback_note = models.TextField(blank=True)
    metrics_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['created_at'], name='ai_aiqualit_created_915bfc_idx'),
            models.Index(fields=['evaluator'], name='ai_aiqualit_evaluat_37de9f_idx'),
            models.Index(fields=['feedback_type'], name='ai_aiqualit_feedbac_e2732e_idx'),
            models.Index(fields=['quality_score'], name='ai_aiqualit_quality_51b759_idx'),
            models.Index(fields=['is_error'], name='ai_aiqualit_is_erro_0fe0b2_idx'),
        ]

