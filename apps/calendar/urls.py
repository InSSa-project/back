from django.urls import path

from .views import ScheduleEventListView

urlpatterns = [
    path('events', ScheduleEventListView.as_view(), name='calendar-events'),
]
