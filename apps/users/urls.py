from django.conf import settings
from django.urls import path

from .views import MeView, OAuthAuthorizeView, OAuthCallbackView, OAuthDebugView, OAuthLoginView

urlpatterns = [
    path('oauth/login', OAuthLoginView.as_view(), name='users-oauth-login'),
    path('oauth/<str:provider>/authorize', OAuthAuthorizeView.as_view(), name='users-oauth-authorize'),
    path('oauth/<str:provider>/callback', OAuthCallbackView.as_view(), name='users-oauth-callback'),
    path('me', MeView.as_view(), name='users-me'),
]

if settings.DEBUG:
    urlpatterns.append(path('oauth/<str:provider>/debug', OAuthDebugView.as_view(), name='users-oauth-debug'))