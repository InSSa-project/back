from django.urls import path

from . import views


urlpatterns = [
    path('', views.notice_list, name='notice-list'),
    path('<int:raw_data_id>/', views.notice_detail, name='notice-detail'),
]
