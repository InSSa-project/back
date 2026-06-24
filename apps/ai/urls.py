from django.urls import path

from .views import (
    AiChatSessionListView,
    AiChatSessionMessageListView,
    AiChatView,
    AiQualityByIntentView,
    AiQualitySummaryView,
    AiQualityTimeseriesView,
)

urlpatterns = [
    path('chat', AiChatView.as_view(), name='ai-chat'),
    path('chat/sessions', AiChatSessionListView.as_view(), name='ai-chat-sessions'),
    path('chat/sessions/<int:session_id>/messages', AiChatSessionMessageListView.as_view(), name='ai-chat-session-messages'),
    path('quality/summary', AiQualitySummaryView.as_view(), name='ai-quality-summary'),
    path('quality/timeseries', AiQualityTimeseriesView.as_view(), name='ai-quality-timeseries'),
    path('quality/by-intent', AiQualityByIntentView.as_view(), name='ai-quality-by-intent'),
]
