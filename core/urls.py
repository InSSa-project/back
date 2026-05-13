from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('signup/', views.signup, name='signup'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('data/', views.crawl_data_list, name='crawl_data_list'),
    path('data/<int:pk>/', views.crawl_data_detail, name='crawl_data_detail'),
    path('api/data/', views.api_crawl_data, name='api_crawl_data'),
    path('api/user/', views.api_user_info, name='api_user_info'),
]
