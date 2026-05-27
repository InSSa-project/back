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
    created_at = models.DateTimeField(auto_now_add=True)


class AiChatReference(models.Model):
    chat_message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name='references')
    ai_document = models.ForeignKey(AiDocument, on_delete=models.CASCADE, related_name='chat_references')
    relevance_score = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
