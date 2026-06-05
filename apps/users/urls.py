from django.conf import settings
from django.urls import path

from .views import (
    MattermostLoginView,
    MeView,
    MyProfileImageView,
    MyProfileView,
    OAuthAuthorizeView,
    OAuthCallbackView,
    OAuthDebugView,
    OAuthLoginView,
)

urlpatterns = [
    path('auth/mattermost/login/', MattermostLoginView.as_view(), name='users-mattermost-login'),
    path('oauth/login', OAuthLoginView.as_view(), name='users-oauth-login'),
    path('oauth/<str:provider>/authorize', OAuthAuthorizeView.as_view(), name='users-oauth-authorize'),
    path('oauth/<str:provider>/callback', OAuthCallbackView.as_view(), name='users-oauth-callback'),
    path('me', MeView.as_view(), name='users-me'),
    path('me/profile/', MyProfileView.as_view(), name='users-me-profile'),
    path('me/profile-image/', MyProfileImageView.as_view(), name='users-me-profile-image'),
]

if settings.DEBUG:
    urlpatterns.append(path('oauth/<str:provider>/debug', OAuthDebugView.as_view(), name='users-oauth-debug'))