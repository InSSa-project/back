from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/sync/', include('sync.urls')),
    path('api/schedules/', include('schedules.urls')),
    path('', include('core.urls')),
]
