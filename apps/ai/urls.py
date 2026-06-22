from django.urls import path

from .views import AiChatView, AiQualityByIntentView, AiQualitySummaryView, AiQualityTimeseriesView

urlpatterns = [
    path('chat', AiChatView.as_view(), name='ai-chat'),
    path('quality/summary', AiQualitySummaryView.as_view(), name='ai-quality-summary'),
    path('quality/timeseries', AiQualityTimeseriesView.as_view(), name='ai-quality-timeseries'),
    path('quality/by-intent', AiQualityByIntentView.as_view(), name='ai-quality-by-intent'),
]
