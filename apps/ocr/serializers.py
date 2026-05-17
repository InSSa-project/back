from rest_framework import serializers

from .models import OcrResult


class OcrResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = OcrResult
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']
