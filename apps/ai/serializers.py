from rest_framework import serializers

from .models import AiChatReference, AiDocument, ChatMessage, ChatSession


class AiDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = AiDocument
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class ChatSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatSession
        fields = '__all__'
        read_only_fields = ['id', 'created_at']


class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = '__all__'
        read_only_fields = ['id', 'created_at']


class AiChatReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = AiChatReference
        fields = '__all__'
        read_only_fields = ['id', 'created_at']
