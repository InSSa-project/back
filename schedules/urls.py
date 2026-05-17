from django.urls import path

from . import views


urlpatterns = [
    path('events/', views.event_list, name='schedule-event-list'),
    path('events/<int:event_id>/', views.event_detail, name='schedule-event-detail'),
]

