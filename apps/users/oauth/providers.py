import requests
import logging
from django.conf import settings
from urllib.parse import urlencode

from apps.users.oauth.dto import OAuthUserInfo
from apps.users.oauth.exceptions import OAuthConfigurationError, OAuthTokenVerificationError


logger = logging.getLogger(__name__)


class GoogleOAuthProvider:
    provider_name = 'google'
    userinfo_url = 'https://www.googleapis.com/oauth2/v3/userinfo'
    authorize_url = 'https://accounts.google.com/o/oauth2/v2/auth'
    token_url = 'https://oauth2.googleapis.com/token'

    def get_authorization_url(self, state: str, redirect_uri: str | None = None) -> str:
        self._validate_browser_login_config()
        params = {
            'client_id': settings.GOOGLE_OAUTH_CLIENT_ID,
            'redirect_uri': self._redirect_uri(redirect_uri),
            'response_type': 'code',
            'scope': 'openid email profile',
            'state': state,
            'access_type': 'offline',
            'prompt': 'select_account',
        }
        return f'{self.authorize_url}?{urlencode(params)}'

    def exchange_code(self, code: str, redirect_uri: str | None = None) -> str:
        token_redirect_uri = self._redirect_uri(redirect_uri)
        response = requests.post(
            self.token_url,
            data={
                'client_id': settings.GOOGLE_OAUTH_CLIENT_ID,
                'client_secret': settings.GOOGLE_OAUTH_CLIENT_SECRET,
                'redirect_uri': token_redirect_uri,
                'grant_type': 'authorization_code',
                'code': code,
            },
            timeout=5,
        )
        if response.status_code != 200:
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = {'raw': response.text}
            logger.error(
                'Google token exchange failed status=%s redirect_uri=%s error=%s',
                response.status_code,
                token_redirect_uri,
                error_payload,
            )
            error = error_payload.get('error', 'unknown_error')
            description = error_payload.get('error_description', '')
            raise OAuthTokenVerificationError(f'Google token exchange failed: {error} {description}'.strip())
        access_token = response.json().get('access_token')
        if not access_token:
            logger.error('Google token exchange response missing access_token redirect_uri=%s response=%s', token_redirect_uri, response.json())
            raise OAuthTokenVerificationError('Google did not return an access token.')
        return access_token

    def get_user_info(self, access_token: str) -> OAuthUserInfo:
        profile = self._request_profile(access_token)
        provider_user_id = str(profile.get('sub') or '')
        if not provider_user_id:
            raise OAuthTokenVerificationError('Google token did not return a subject.')

        allowed_audience = getattr(settings, 'GOOGLE_OAUTH_CLIENT_ID', '')
        audience = profile.get('aud')
        if allowed_audience and audience and audience != allowed_audience:
            raise OAuthTokenVerificationError('Google token audience mismatch.')

        return OAuthUserInfo(
            provider=self.provider_name,
            provider_user_id=provider_user_id,
            email=profile.get('email') or '',
            name=profile.get('name') or '',
            profile_image=profile.get('picture') or '',
            raw_profile=profile,
        )

    def _request_profile(self, access_token: str) -> dict:
        response = requests.get(
            self.userinfo_url,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=5,
        )
        if response.status_code != 200:
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = {'raw': response.text}
            logger.error('Google userinfo request failed status=%s error=%s', response.status_code, error_payload)
            raise OAuthTokenVerificationError(f'Google userinfo request failed: {error_payload}')
        return response.json()

    def _redirect_uri(self, redirect_uri: str | None = None):
        return redirect_uri or settings.GOOGLE_OAUTH_REDIRECT_URI

    def _validate_browser_login_config(self):
        if not settings.GOOGLE_OAUTH_CLIENT_ID:
            raise OAuthConfigurationError('GOOGLE_OAUTH_CLIENT_ID 또는 GOOGLE_CLIENT_ID 환경변수가 설정되지 않았습니다.')
        if not settings.GOOGLE_OAUTH_CLIENT_ID.endswith('.apps.googleusercontent.com'):
            raise OAuthConfigurationError(
                'Google OAuth Client ID 형식이 올바르지 않습니다. '
                'Google Cloud Console의 OAuth 2.0 클라이언트 ID 값을 사용해야 하며 '
                '형식은 xxx.apps.googleusercontent.com 이어야 합니다.'
            )
        if not settings.GOOGLE_OAUTH_CLIENT_SECRET:
            raise OAuthConfigurationError('GOOGLE_OAUTH_CLIENT_SECRET 또는 GOOGLE_CLIENT_SECRET 환경변수가 설정되지 않았습니다.')
        if not settings.GOOGLE_OAUTH_REDIRECT_URI:
            raise OAuthConfigurationError('GOOGLE_OAUTH_REDIRECT_URI 환경변수가 설정되지 않았습니다.')


class KakaoOAuthProvider:
    provider_name = 'kakao'
    userinfo_url = 'https://kapi.kakao.com/v2/user/me'
    authorize_url = 'https://kauth.kakao.com/oauth/authorize'
    token_url = 'https://kauth.kakao.com/oauth/token'

    def get_authorization_url(self, state: str, redirect_uri: str | None = None) -> str:
        self._validate_browser_login_config()
        params = {
            'client_id': settings.KAKAO_REST_API_KEY,
            'redirect_uri': self._redirect_uri(redirect_uri),
            'response_type': 'code',
            'state': state,
        }
        return f'{self.authorize_url}?{urlencode(params)}'

    def exchange_code(self, code: str, redirect_uri: str | None = None) -> str:
        data = {
            'grant_type': 'authorization_code',
            'client_id': settings.KAKAO_REST_API_KEY,
            'redirect_uri': self._redirect_uri(redirect_uri),
            'code': code,
        }
        if settings.KAKAO_CLIENT_SECRET:
            data['client_secret'] = settings.KAKAO_CLIENT_SECRET

        response = requests.post(self.token_url, data=data, timeout=5)
        if response.status_code != 200:
            raise OAuthTokenVerificationError('Kakao authorization code exchange failed.')
        access_token = response.json().get('access_token')
        if not access_token:
            raise OAuthTokenVerificationError('Kakao did not return an access token.')
        return access_token

    def get_user_info(self, access_token: str) -> OAuthUserInfo:
        profile = self._request_profile(access_token)
        provider_user_id = str(profile.get('id') or '')
        if not provider_user_id:
            raise OAuthTokenVerificationError('Kakao token did not return a user id.')

        kakao_account = profile.get('kakao_account') or {}
        kakao_profile = kakao_account.get('profile') or {}
        return OAuthUserInfo(
            provider=self.provider_name,
            provider_user_id=provider_user_id,
            email=kakao_account.get('email') or '',
            name=kakao_profile.get('nickname') or '',
            profile_image=kakao_profile.get('profile_image_url') or '',
            raw_profile=profile,
        )

    def _request_profile(self, access_token: str) -> dict:
        response = requests.get(
            self.userinfo_url,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=5,
        )
        if response.status_code != 200:
            raise OAuthTokenVerificationError('Kakao token verification failed.')
        return response.json()

    def _redirect_uri(self, redirect_uri: str | None = None):
        return redirect_uri or settings.KAKAO_OAUTH_REDIRECT_URI

    def _validate_browser_login_config(self):
        if not settings.KAKAO_REST_API_KEY:
            raise OAuthConfigurationError('KAKAO_REST_API_KEY 환경변수가 설정되지 않았습니다.')
        if not settings.KAKAO_OAUTH_REDIRECT_URI:
            raise OAuthConfigurationError('KAKAO_OAUTH_REDIRECT_URI 환경변수가 설정되지 않았습니다.')
