from django.urls import include, path

urlpatterns = [
    path('api/users/', include('apps.users.urls')),
    path('api/v1/users/', include('apps.users.urls')),
    path('api/v1/calendar/', include('apps.calendar.urls')),
    path('api/v1/ai/', include('apps.ai.urls')),
    path('api/v1/ssafy/', include('apps.notices.urls')),
    path('api/v1/ocr/', include('apps.ocr.urls')),
    path('api/v1/notifications/', include('apps.notifications.urls')),
    path('api/v1/risk/', include('apps.risk.urls')),
    path('api/v1/dashboard/', include('dashboard.urls')),
    path('', include('common.urls')),
]
