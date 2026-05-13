from django.urls import path

from . import views


urlpatterns = [
    path('crawl/run/', views.run_crawl, name='sync-crawl-run'),
]

