from rest_framework import serializers

from .models import AiChatReference, AiDocument, ChatMessage, ChatSession


class AiDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = AiDocument
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class ChatSessionSerializer(serializers.ModelSerializer):
    latest_message = serializers.SerializerMethodField()
    message_count = serializers.IntegerField(read_only=True)
    last_message_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = ChatSession
        fields = ['id', 'title', 'created_at', 'last_message_at', 'message_count', 'latest_message']
        read_only_fields = fields

    def get_latest_message(self, obj):
        message = getattr(obj, 'latest_prefetched_message', None)
        if message is None:
            message = obj.messages.order_by('-created_at', '-id').first()
        if not message:
            return ''
        return (message.content or '').replace('\n', ' ')[:120]


class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = ['id', 'session', 'role', 'content', 'usage_json', 'created_at']
        read_only_fields = fields


class AiChatReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = AiChatReference
        fields = '__all__'
        read_only_fields = ['id', 'created_at']
