from rest_framework import serializers
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from schedules.models import ScheduleEvent
from schedules.utils import normalize_schedule_display_title
from sync.models import RawSsafyData
from sync.services.tracks import COMMON_TRACK_KEY, normalize_track_key

from .models import UserScheduleEvent


COMMON_TRACK_VALUES = {'', COMMON_TRACK_KEY, 'common', 'all', 'global', '공통', '전체'}


class ScheduleEventSerializer(serializers.ModelSerializer):
    metadata = serializers.JSONField(source='metadata_json', read_only=True)
    raw_data_id = serializers.SerializerMethodField()
    source_url = serializers.SerializerMethodField()
    source_title = serializers.SerializerMethodField()
    owner_id = serializers.IntegerField(read_only=True)
    created_by_id = serializers.IntegerField(source='owner_id', read_only=True)
    is_common = serializers.SerializerMethodField()
    is_global = serializers.SerializerMethodField()
    is_generated = serializers.SerializerMethodField()
    display_title = serializers.SerializerMethodField()
    deadline_at = serializers.SerializerMethodField()
    display_memo = serializers.SerializerMethodField()
    user_friendly_description = serializers.SerializerMethodField()
    track = serializers.SerializerMethodField()
    track_key = serializers.SerializerMethodField()

    class Meta:
        model = ScheduleEvent
        fields = [
            'id',
            'title',
            'display_title',
            'description',
            'display_memo',
            'user_friendly_description',
            'start_at',
            'end_at',
            'deadline_at',
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
            'is_global',
            'is_generated',
            'owner_id',
            'created_by_id',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_raw_data_id(self, event):
        if event.raw_data_id:
            return event.raw_data_id
        return (event.metadata_json or {}).get('raw_data_id')

    def get_source_url(self, event):
        raw_data = self._raw_data(event)
        if raw_data:
            return raw_data.source_url
        return (event.metadata_json or {}).get('source_url')

    def get_source_title(self, event):
        raw_data = self._raw_data(event)
        if raw_data:
            return raw_data.title
        return (event.metadata_json or {}).get('source_title')

    def get_is_common(self, event):
        metadata = event.metadata_json or {}
        track_key = self._raw_track_key(event)
        return bool(
            metadata.get('is_common')
            or normalize_track_key(track_key) in COMMON_TRACK_VALUES
            or not track_key
        )

    def get_is_global(self, event):
        metadata = event.metadata_json or {}
        if metadata.get('is_global') is not None:
            return bool(metadata.get('is_global'))
        return event.owner_id is None

    def get_is_generated(self, event):
        metadata = event.metadata_json or {}
        if metadata.get('is_generated') is not None:
            return bool(metadata.get('is_generated'))
        return bool(
            self.get_raw_data_id(event)
            or event.raw_data_id
            or event.event_type == 'generated'
            or event.source_type in {'notice', 'ssafy', 'learning', 'evaluation'}
        )

    def get_display_title(self, event):
        metadata = event.metadata_json or {}
        display_title = str(metadata.get('display_title') or '').strip()
        if display_title:
            return display_title
        return normalize_schedule_display_title(event.title) or event.title

    def get_deadline_at(self, event):
        metadata = event.metadata_json or {}
        deadline_at = self._parse_metadata_datetime(
            metadata.get('deadline_at')
            or metadata.get('deadline')
            or metadata.get('due_at')
            or metadata.get('due_date')
        )
        return timezone.localtime(deadline_at).isoformat() if deadline_at else None

    def get_display_memo(self, event):
        user_memo = self._user_memo(event)
        if user_memo:
            return user_memo
        return ''

    def get_user_friendly_description(self, event):
        return self.get_display_memo(event)

    def get_track(self, event):
        return self.get_track_key(event)

    def get_track_key(self, event):
        track_key = self._raw_track_key(event)
        if self.get_is_common(event):
            return COMMON_TRACK_KEY
        return track_key

    def _raw_track_key(self, event):
        metadata = event.metadata_json or {}
        audience = metadata.get('audience') or {}
        return normalize_track_key(
            metadata.get('track_key')
            or audience.get('track_key')
            or metadata.get('track')
            or audience.get('track')
        )

    def _raw_data(self, event):
        raw_data = getattr(event, 'raw_data', None)
        if raw_data:
            return raw_data
        raw_data_id = (event.metadata_json or {}).get('raw_data_id')
        if not raw_data_id:
            return None
        try:
            return RawSsafyData.objects.filter(pk=raw_data_id).first()
        except (TypeError, ValueError):
            return None

    def _user_memo(self, event):
        request = self.context.get('request')
        user = getattr(request, 'user', None) if request is not None else None
        if user is None or not getattr(user, 'is_authenticated', False):
            return ''
        link = UserScheduleEvent.objects.filter(user=user, schedule_event=event).only('memo').first()
        return str(link.memo or '').strip() if link else ''

    def _parse_metadata_datetime(self, value):
        if not value:
            return None
        parsed = parse_datetime(str(value))
        if parsed is None:
            return None
        if timezone.is_naive(parsed):
            return timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed


class UserScheduleEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserScheduleEvent
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']
