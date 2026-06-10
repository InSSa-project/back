from rest_framework import serializers

from schedules.models import ScheduleEvent

from .models import UserScheduleEvent


class ScheduleEventSerializer(serializers.ModelSerializer):
    metadata = serializers.JSONField(source='metadata_json', read_only=True)
    raw_data_id = serializers.IntegerField(read_only=True)
    source_url = serializers.SerializerMethodField()
    source_title = serializers.SerializerMethodField()
    owner_id = serializers.IntegerField(read_only=True)
    created_by_id = serializers.IntegerField(source='owner_id', read_only=True)
    is_common = serializers.SerializerMethodField()
    track = serializers.SerializerMethodField()
    track_key = serializers.SerializerMethodField()

    class Meta:
        model = ScheduleEvent
        fields = [
            'id',
            'title',
            'description',
            'start_at',
            'end_at',
            'is_all_day',
            'event_type',
            'source_type',
            'source_id',
            'raw_data_id',
            'metadata',
            'metadata_json',
            'source_url',
            'source_title',
            'track',
            'track_key',
            'is_common',
            'owner_id',
            'created_by_id',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_source_url(self, event):
        raw_data = getattr(event, 'raw_data', None)
        return raw_data.source_url if raw_data else None

    def get_source_title(self, event):
        raw_data = getattr(event, 'raw_data', None)
        if raw_data:
            return raw_data.title
        return (event.metadata_json or {}).get('source_title')

    def get_is_common(self, event):
        metadata = event.metadata_json or {}
        return bool(metadata.get('is_common') or metadata.get('is_global') or not self.get_track_key(event))

    def get_track(self, event):
        return self.get_track_key(event)

    def get_track_key(self, event):
        metadata = event.metadata_json or {}
        audience = metadata.get('audience') or {}
        return metadata.get('track_key') or audience.get('track_key') or metadata.get('track') or audience.get('track')


class UserScheduleEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserScheduleEvent
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']
