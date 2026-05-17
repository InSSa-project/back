from django.urls import path

from .views import SsafySyncView

urlpatterns = [
    path('sync', SsafySyncView.as_view(), name='ssafy-sync'),
]
