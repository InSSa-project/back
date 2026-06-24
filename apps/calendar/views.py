from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from rest_framework.views import APIView
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from common.utils.api_response import error_response, success_response
from schedules.models import ScheduleEvent

from .models import UserScheduleEvent
from .serializers import ScheduleEventSerializer
from .services import CalendarService, ProfileIncompleteError, is_event_hidden_for_user, is_holiday_event


PATCH_FIELDS = {'title', 'description', 'event_type', 'start_at', 'end_at', 'is_all_day'}


class ScheduleEventListView(APIView):
    permission_classes = [IsAuthenticated]
    service_class = CalendarService

    def get(self, request):
        try:
            events = self.service_class().list_events(
                user=request.user,
                start=request.query_params.get('start'),
                end=request.query_params.get('end'),
                track=request.query_params.get('track'),
                event_type=request.query_params.get('event_type'),
            )
        except ProfileIncompleteError as exc:
            return error_response(exc.message, code=exc.code, status_code=status.HTTP_400_BAD_REQUEST)
        return success_response(ScheduleEventSerializer(events, many=True, context={'request': request}).data)


class ScheduleEventDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, event_id):
        try:
            event = ScheduleEvent.objects.select_related('raw_data').get(pk=event_id)
        except ScheduleEvent.DoesNotExist:
            return error_response('Schedule event not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)

        try:
            visible_ids = {
                item.id
                for item in CalendarService().list_events(user=request.user)
            }
        except ProfileIncompleteError as exc:
            return error_response(exc.message, code=exc.code, status_code=status.HTTP_400_BAD_REQUEST)
        if event.id not in visible_ids:
            return error_response('Schedule event not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)
        return success_response(ScheduleEventSerializer(event, context={'request': request}).data)

    def patch(self, request, event_id):
        try:
            event = ScheduleEvent.objects.get(pk=event_id)
        except ScheduleEvent.DoesNotExist:
            return error_response('Schedule event not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)

        visibility_error = self._visibility_error(event, request.user)
        if visibility_error:
            return visibility_error
        if is_holiday_event(event):
            return error_response('Holiday events are read-only.', code='HOLIDAY_READ_ONLY', status_code=status.HTTP_403_FORBIDDEN)

        invalid_fields = set(request.data) - PATCH_FIELDS
        if invalid_fields:
            return error_response(
                f'Unsupported fields: {", ".join(sorted(invalid_fields))}',
                code=400,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        parsed_values, parse_error = self._parse_patch_payload(request.data, event)
        if parse_error:
            return parse_error

        if event.owner_id == request.user.id:
            for field, value in parsed_values.items():
                setattr(event, field, value)
            if parsed_values:
                event.save(update_fields=[*parsed_values.keys(), 'updated_at'])
        else:
            link, _created = UserScheduleEvent.objects.get_or_create(user=request.user, schedule_event=event)
            for field, value in parsed_values.items():
                setattr(link, f'override_{field}', value)
            if parsed_values:
                link.is_hidden = False
                link.save(update_fields=[*(f'override_{field}' for field in parsed_values), 'is_hidden', 'updated_at'])
        return success_response(ScheduleEventSerializer(event, context={'request': request}).data)

    def delete(self, request, event_id):
        try:
            event = ScheduleEvent.objects.get(pk=event_id)
        except ScheduleEvent.DoesNotExist:
            return error_response('Schedule event not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)

        permission_error = self._delete_permission_error(event, request.user)
        if permission_error:
            return permission_error
        already_hidden = UserScheduleEvent.objects.filter(user=request.user, schedule_event=event, is_hidden=True).exists()
        if not already_hidden:
            visibility_error = self._visibility_error(event, request.user)
            if visibility_error:
                return visibility_error
        if is_holiday_event(event):
            return error_response('Holiday events are read-only.', code='HOLIDAY_READ_ONLY', status_code=status.HTTP_403_FORBIDDEN)

        if event.owner_id == request.user.id:
            event.delete()
        else:
            link, _created = UserScheduleEvent.objects.get_or_create(user=request.user, schedule_event=event)
            if not link.is_hidden:
                link.is_hidden = True
                link.save(update_fields=['is_hidden', 'updated_at'])
        return success_response(message='Schedule event deleted.')

    def _delete_permission_error(self, event, user):
        if event.owner_id:
            if event.owner_id == user.id:
                return None
            return error_response(
                'You do not have permission to delete this schedule event.',
                code=403,
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return None

    def _visibility_error(self, event, user):
        if event.owner_id and event.owner_id != user.id:
            return error_response(
                'You do not have permission to modify this schedule event.',
                code=403,
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if is_event_hidden_for_user(event, user):
            return error_response('Schedule event not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)
        try:
            visible_ids = {item.id for item in CalendarService().list_events(user=user)}
        except ProfileIncompleteError as exc:
            return error_response(exc.message, code=exc.code, status_code=status.HTTP_400_BAD_REQUEST)
        if event.id not in visible_ids:
            return error_response('Schedule event not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)
        return None

    def _parse_patch_payload(self, payload, event):
        parsed = {}
        for field in ['title', 'description', 'event_type']:
            if field in payload:
                parsed[field] = str(payload.get(field) or '')
        if 'title' in parsed and not parsed['title'].strip():
            return None, error_response('Title is required.', code=400, status_code=status.HTTP_400_BAD_REQUEST)
        for field in ['start_at', 'end_at']:
            if field in payload:
                value = _parse_datetime(payload.get(field))
                if value is None:
                    return None, error_response(f'Invalid datetime format: {field}', code=400, status_code=status.HTTP_400_BAD_REQUEST)
                parsed[field] = value
        if 'is_all_day' in payload:
            parsed['is_all_day'] = _parse_bool(payload.get('is_all_day'))

        effective_start_at = parsed.get('start_at', event.start_at)
        effective_end_at = parsed.get('end_at', event.end_at)
        if effective_start_at and effective_end_at and effective_end_at < effective_start_at:
            return None, error_response('End time must be after start time.', code=400, status_code=status.HTTP_400_BAD_REQUEST)
        return parsed, None


def _parse_datetime(value):
    if not isinstance(value, str):
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _parse_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'y', 'on'}
    return bool(value)
