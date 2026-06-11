from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from rest_framework.views import APIView

from common.utils.api_response import error_response, success_response
from schedules.models import ScheduleEvent

from .serializers import ScheduleEventSerializer
from .services import CalendarService


class ScheduleEventListView(APIView):
    permission_classes = [IsAuthenticated]
    service_class = CalendarService

    def get(self, request):
        events = self.service_class().list_events(
            user=request.user,
            start=request.query_params.get('start'),
            end=request.query_params.get('end'),
            track=request.query_params.get('track'),
            event_type=request.query_params.get('event_type'),
        )
        return success_response(ScheduleEventSerializer(events, many=True).data)


class ScheduleEventDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, event_id):
        try:
            event = ScheduleEvent.objects.get(pk=event_id)
        except ScheduleEvent.DoesNotExist:
            return error_response('Schedule event not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)

        permission_error = self._delete_permission_error(event, request.user)
        if permission_error:
            return permission_error

        event.delete()
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
        if getattr(user, 'is_staff', False):
            return None
        return error_response(
            'Public schedule events require admin permission.',
            code=403,
            status_code=status.HTTP_403_FORBIDDEN,
        )
