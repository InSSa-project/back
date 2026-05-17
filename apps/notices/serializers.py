from rest_framework import serializers

from .models import RawSsafyData, SsafyDataImportLog


class SsafyDataImportLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = SsafyDataImportLog
        fields = '__all__'
        read_only_fields = ['id', 'created_at']


class RawSsafyDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = RawSsafyData
        fields = '__all__'
        read_only_fields = ['id', 'created_at']
