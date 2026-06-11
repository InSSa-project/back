from django.urls import path

from . import views


urlpatterns = [
    path('', views.notice_list, name='notice-list'),
    path('<int:raw_data_id>/summary/', views.notice_summary, name='notice-summary'),
    path('<int:raw_data_id>/', views.notice_detail, name='notice-detail'),
]
