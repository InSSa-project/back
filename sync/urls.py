from django.urls import path

from . import views


urlpatterns = [
    path('crawl/run/', views.run_crawl, name='sync-crawl-run'),
    path('raw-data/', views.raw_data_list, name='sync-raw-data-list'),
    path('raw-data/<int:raw_data_id>/ocr/manual/', views.set_manual_ocr_text, name='sync-raw-data-manual-ocr'),
]

