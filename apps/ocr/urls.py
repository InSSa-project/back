from django.urls import path

from .views import OcrUploadView

urlpatterns = [
    path('upload', OcrUploadView.as_view(), name='ocr-upload'),
]
