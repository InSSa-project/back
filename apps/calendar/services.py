from .models import ScheduleEvent


class CalendarService:
    def list_events(self, user, start=None, end=None):
        queryset = ScheduleEvent.objects.filter(user_schedule_events__user=user)
        if start:
            queryset = queryset.filter(start_at__date__gte=start)
        if end:
            queryset = queryset.filter(start_at__date__lte=end)
        return queryset.distinct()
