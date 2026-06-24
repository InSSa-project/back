from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from rest_framework.views import APIView

from common.utils.api_response import error_response, success_response
from schedules.models import ScheduleEvent

from .models import HiddenCalendarEvent, UserScheduleEvent
from .serializers import ScheduleEventSerializer
from .services import CalendarService, ProfileIncompleteError


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

        try:
            visible_ids = {
                item.id
                for item in CalendarService().list_events(user=request.user)
            }
        except ProfileIncompleteError as exc:
            return error_response(exc.message, code=exc.code, status_code=status.HTTP_400_BAD_REQUEST)
        if event.id not in visible_ids:
            return error_response('Schedule event not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)

        invalid_fields = set(request.data) - {'memo'}
        if invalid_fields:
            return error_response(
                f'Unsupported fields: {", ".join(sorted(invalid_fields))}',
                code=400,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        memo = str(request.data.get('memo') or '')
        link, _created = UserScheduleEvent.objects.get_or_create(user=request.user, schedule_event=event)
        link.memo = memo
        link.save(update_fields=['memo', 'updated_at'])
        return success_response(ScheduleEventSerializer(event, context={'request': request}).data)

    def delete(self, request, event_id):
        try:
            event = ScheduleEvent.objects.get(pk=event_id)
        except ScheduleEvent.DoesNotExist:
            return error_response('Schedule event not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)

        permission_error = self._delete_permission_error(event, request.user)
        if permission_error:
            return permission_error

        if event.owner_id == request.user.id:
            event.delete()
        else:
            HiddenCalendarEvent.objects.get_or_create(user=request.user, schedule_event=event)
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
