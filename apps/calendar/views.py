from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common.utils.api_response import success_response

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
