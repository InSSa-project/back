from rest_framework import serializers

from .models import RiskStatus


class RiskStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiskStatus
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']
