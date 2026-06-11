from django.urls import path

from .views import ScheduleEventDetailView, ScheduleEventListView

urlpatterns = [
    path('events', ScheduleEventListView.as_view(), name='calendar-events'),
    path('events/<int:event_id>', ScheduleEventDetailView.as_view(), name='calendar-event-detail'),
]
