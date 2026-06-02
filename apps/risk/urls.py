from django.urls import path

from .views import EvaluationResultDetailView, EvaluationResultListView, RiskStatusView

urlpatterns = [
    path('status', RiskStatusView.as_view(), name='risk-status'),
    path('evaluations', EvaluationResultListView.as_view(), name='risk-evaluation-list'),
    path('evaluations/<int:evaluation_id>', EvaluationResultDetailView.as_view(), name='risk-evaluation-detail'),
]