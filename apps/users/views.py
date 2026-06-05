from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from django.contrib.auth import login
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect
from urllib.parse import parse_qs, urlencode, urlparse

from common.utils.api_response import error_response, success_response

from .oauth.exceptions import OAuthError
from .oauth.registry import OAuthProviderRegistry
from .models import UserProfile
from .serializers import MattermostLoginSerializer, OAuthLoginSerializer, ProfileImageUploadSerializer, UserProfileSerializer, UserSerializer
from .services import (
    MattermostAuthError,
    MattermostConfigError,
    MattermostLoginService,
    MattermostUnavailableError,
    OAuthLoginService,
    UserService,
)


class MeView(APIView):
    permission_classes = [IsAuthenticated]
    service_class = UserService

    def get(self, request):
        user = self.service_class().get_profile(request.user)
        return success_response(UserSerializer(user).data)


class MyProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile = self._get_or_create_profile(request.user)
        return success_response(UserProfileSerializer(profile, context={'request': request}).data)

    def patch(self, request):
        profile = self._get_or_create_profile(request.user)
        serializer = UserProfileSerializer(profile, data=request.data, partial=True, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(serializer.data)

    def _get_or_create_profile(self, user):
        profile, _created = UserProfile.objects.get_or_create(
            user=user,
            defaults={'notification_email': user.email or None},
        )
        return profile


class MyProfileImageView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        profile = self._get_or_create_profile(request.user)
        serializer = ProfileImageUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile.profile_image = serializer.validated_data['image']
        profile.save(update_fields=['profile_image', 'updated_at'])
        return success_response(UserProfileSerializer(profile, context={'request': request}).data)

    def delete(self, request):
        profile = self._get_or_create_profile(request.user)
        profile.profile_image = None
        profile.save(update_fields=['profile_image', 'updated_at'])
        return success_response(UserProfileSerializer(profile, context={'request': request}).data)

    def _get_or_create_profile(self, user):
        profile, _created = UserProfile.objects.get_or_create(
            user=user,
            defaults={'notification_email': user.email or None},
        )
        return profile


class OAuthLoginView(APIView):
    permission_classes = [AllowAny]
    serializer_class = OAuthLoginSerializer
    service_class = OAuthLoginService

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        current_user = request.user if request.user.is_authenticated else None
        try:
            result = self.service_class().login(
                provider_name=serializer.validated_data['provider'],
                provider_access_token=serializer.validated_data['access_token'],
                current_user=current_user,
            )
        except OAuthError as exc:
            return error_response(str(exc), code=status.HTTP_400_BAD_REQUEST)

        return success_response({
            'access_token': result['access_token'],
            'refresh_token': result['refresh_token'],
            'is_created': result['is_created'],
            'user': UserSerializer(result['user']).data,
        })


class MattermostLoginView(APIView):
    permission_classes = [AllowAny]
    serializer_class = MattermostLoginSerializer
    service_class = MattermostLoginService

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = self.service_class().login(
                login_id=serializer.validated_data['login_id'],
                password=serializer.validated_data['password'],
            )
        except MattermostAuthError:
            return error_response('Mattermost 인증에 실패했습니다.', code=status.HTTP_401_UNAUTHORIZED, status_code=status.HTTP_401_UNAUTHORIZED)
        except MattermostConfigError:
            return error_response('Mattermost 연동 설정이 필요합니다.', code=status.HTTP_503_SERVICE_UNAVAILABLE, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        except MattermostUnavailableError:
            return error_response(
                'Mattermost 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.',
                code=status.HTTP_503_SERVICE_UNAVAILABLE,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        profile = result['profile']
        return success_response(
            {
                'access_token': result['access_token'],
                'refresh_token': result['refresh_token'],
                'user': {
                    'id': result['user'].id,
                    'email': result['user'].email,
                    'name': result['user'].name,
                    'mattermost_user_id': profile.mattermost_user_id,
                    'mattermost_username': profile.mattermost_username,
                    'mattermost_nickname': profile.mattermost_nickname,
                },
            }
        )


class OAuthAuthorizeView(APIView):
    permission_classes = [AllowAny]
    service_class = OAuthLoginService

    def get(self, request, provider):
        try:
            authorization_url = self.service_class().build_authorization_url(
                request,
                provider,
                frontend_next=request.query_params.get('next', ''),
            )
        except OAuthError as exc:
            return error_response(str(exc), code=status.HTTP_400_BAD_REQUEST)
        return redirect(authorization_url)


class OAuthCallbackView(APIView):
    permission_classes = [AllowAny]
    service_class = OAuthLoginService

    def get(self, request, provider):
        code = request.query_params.get('code')
        state = request.query_params.get('state')
        if not code or not state:
            return redirect(self._build_frontend_callback_url(error='oauth_failed'))

        try:
            result = self.service_class().login_with_authorization_code(request, provider, code, state)
        except OAuthError as exc:
            return redirect(self._build_frontend_callback_url(error='oauth_failed'))

        login(request, result['user'])
        return redirect(self._build_frontend_callback_url(result))

    def _build_frontend_callback_url(self, result=None, error=''):
        frontend_base_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173').rstrip('/')
        callback_path = '/auth/callback'

        if error:
            fragment = urlencode({'error': error})
            return f'{frontend_base_url}{callback_path}#{fragment}'

        # TODO: Replace URL fragment token handoff with httpOnly cookies before production.
        fragment = urlencode(
            {
                'access_token': result['access_token'],
                'refresh_token': result['refresh_token'],
                'redirect': result.get('frontend_next') or '/calendar',
                'is_created': 'true' if result.get('is_created') else 'false',
            }
        )
        return f'{frontend_base_url}{callback_path}#{fragment}'


class OAuthDebugView(APIView):
    permission_classes = [AllowAny]
    provider_registry_class = OAuthProviderRegistry

    def get(self, request, provider):
        try:
            provider_instance = self.provider_registry_class().get_provider(provider)
            redirect_uri = getattr(settings, f'{provider.upper()}_OAUTH_REDIRECT_URI', '')
            authorization_url = provider_instance.get_authorization_url('debug-state', redirect_uri=redirect_uri)
        except OAuthError as exc:
            return error_response(str(exc), code=status.HTTP_400_BAD_REQUEST)

        parsed_url = urlparse(authorization_url)
        query = parse_qs(parsed_url.query)
        client_id = query.get('client_id', [''])[0]
        return JsonResponse({
            'provider': provider,
            'request_host': request.get_host(),
            'settings_redirect_uri_repr': repr(redirect_uri),
            'generated_redirect_uri_repr': repr(query.get('redirect_uri', [''])[0]),
            'redirect_uri_equal': redirect_uri == query.get('redirect_uri', [''])[0],
            'client_id_repr': repr(client_id),
            'client_id_valid_shape': client_id.endswith('.apps.googleusercontent.com'),
            'authorization_url': authorization_url,
        }, json_dumps_params={'ensure_ascii': False, 'indent': 2})
