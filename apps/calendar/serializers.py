from rest_framework import serializers

from .models import ScheduleEvent, UserScheduleEvent


class ScheduleEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduleEvent
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class UserScheduleEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserScheduleEvent
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']
