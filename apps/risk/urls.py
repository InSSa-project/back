from django.urls import path

from .views import RiskStatusView

urlpatterns = [
    path('status', RiskStatusView.as_view(), name='risk-status'),
]
