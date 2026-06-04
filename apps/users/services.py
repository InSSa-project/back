from django.contrib.auth import authenticate
from django.core import signing
from django.db import transaction
from django.urls import reverse
from django.utils.crypto import get_random_string
import logging

from apps.users.jwt.service import JwtService
from apps.users.oauth.registry import OAuthProviderRegistry

from .models import OAuthAccount, User


logger = logging.getLogger(__name__)
OAUTH_STATE_SALT = 'inssa.oauth.state'
OAUTH_STATE_MAX_AGE_SECONDS = 10 * 60


class UserService:
    def get_profile(self, user):
        return user

    def authenticate_by_identifier(self, request, identifier, password):
        if not identifier or not password:
            return None

        user = self._find_user_by_identifier(identifier)
        if user is None:
            return None

        return authenticate(request, username=user.email, password=password)

    def _find_user_by_identifier(self, identifier):
        try:
            if '@' in identifier:
                return User.objects.get(email=identifier)
            return User.objects.get(username=identifier)
        except User.DoesNotExist:
            return None


class OAuthLoginService:
    def __init__(self, provider_registry=None, jwt_service=None):
        self.provider_registry = provider_registry or OAuthProviderRegistry()
        self.jwt_service = jwt_service or JwtService()

    @transaction.atomic
    def login(self, provider_name, provider_access_token, current_user=None):
        provider = self.provider_registry.get_provider(provider_name)
        oauth_user = provider.get_user_info(provider_access_token)
        user, oauth_account, is_created = self._get_or_create_user(oauth_user, current_user)
        tokens = self.jwt_service.issue_pair(user)
        return {
            'user': user,
            'oauth_account': oauth_account,
            'is_created': is_created,
            **tokens,
        }

    def build_authorization_url(self, request, provider_name, frontend_next=''):
        provider = self.provider_registry.get_provider(provider_name)
        redirect_uri = self._build_callback_uri(request, provider_name)
        frontend_next = self._normalize_frontend_next(frontend_next)
        state = self._build_signed_state(provider_name, redirect_uri, frontend_next)
        state_session_key = self._state_session_key(provider_name)
        redirect_uri_session_key = self._redirect_uri_session_key(provider_name)
        frontend_next_session_key = self._frontend_next_session_key(provider_name)
        request.session[state_session_key] = state
        request.session[redirect_uri_session_key] = redirect_uri
        request.session[frontend_next_session_key] = frontend_next
        request.session.modified = True
        authorization_url = provider.get_authorization_url(state, redirect_uri=redirect_uri)
        logger.info(
            'OAuth authorize generated provider=%s host=%s has_client_id=%s',
            provider_name,
            request.get_host(),
            'client_id=' in authorization_url and 'client_id=&' not in authorization_url,
        )
        return authorization_url

    def login_with_authorization_code(self, request, provider_name, code, state):
        state_session_key = self._state_session_key(provider_name)
        redirect_uri_session_key = self._redirect_uri_session_key(provider_name)
        frontend_next_session_key = self._frontend_next_session_key(provider_name)
        expected_state = request.session.pop(state_session_key, None)
        redirect_uri = request.session.pop(redirect_uri_session_key, None)
        frontend_next = request.session.pop(frontend_next_session_key, '')
        signed_state_payload = self._load_signed_state(state)
        signed_state_valid = bool(
            signed_state_payload
            and signed_state_payload.get('provider') == provider_name
        )
        if not redirect_uri and signed_state_payload:
            redirect_uri = signed_state_payload.get('redirect_uri')
        if not frontend_next and signed_state_payload:
            frontend_next = signed_state_payload.get('frontend_next', '')
        request.session.modified = True
        logger.info(
            'OAuth callback received provider=%s host=%s state_matched=%s signed_state_valid=%s',
            provider_name,
            request.get_host(),
            bool(expected_state and expected_state == state),
            signed_state_valid,
        )
        if not ((expected_state and expected_state == state) or signed_state_valid):
            from apps.users.oauth.exceptions import OAuthTokenVerificationError

            raise OAuthTokenVerificationError('Invalid OAuth state.')

        provider = self.provider_registry.get_provider(provider_name)
        access_token = provider.exchange_code(code, redirect_uri=redirect_uri)
        current_user = request.user if request.user.is_authenticated else None
        return {
            **self.login(provider_name, access_token, current_user=current_user),
            'frontend_next': self._normalize_frontend_next(frontend_next),
        }

    def _build_callback_uri(self, request, provider_name):
        from django.conf import settings

        configured_redirect_uri = getattr(settings, f'{provider_name.upper()}_OAUTH_REDIRECT_URI', '')
        if configured_redirect_uri:
            return configured_redirect_uri
        callback_path = reverse('users-oauth-callback', kwargs={'provider': provider_name})
        return request.build_absolute_uri(callback_path)

    def _build_signed_state(self, provider_name, redirect_uri, frontend_next=''):
        return signing.dumps(
            {
                'provider': provider_name,
                'redirect_uri': redirect_uri,
                'frontend_next': self._normalize_frontend_next(frontend_next),
                'nonce': get_random_string(32),
            },
            salt=OAUTH_STATE_SALT,
        )

    def _load_signed_state(self, state):
        try:
            return signing.loads(
                state,
                salt=OAUTH_STATE_SALT,
                max_age=OAUTH_STATE_MAX_AGE_SECONDS,
            )
        except signing.BadSignature:
            return None
        except signing.SignatureExpired:
            return None

    def _state_session_key(self, provider_name):
        return f'oauth_state_{provider_name}'

    def _redirect_uri_session_key(self, provider_name):
        return f'oauth_redirect_uri_{provider_name}'

    def _frontend_next_session_key(self, provider_name):
        return f'oauth_frontend_next_{provider_name}'

    def _normalize_frontend_next(self, frontend_next):
        if not frontend_next or not isinstance(frontend_next, str):
            return ''
        if not frontend_next.startswith('/') or frontend_next.startswith('//'):
            return ''
        return frontend_next

    def _get_or_create_user(self, oauth_user, current_user=None):
        oauth_account = OAuthAccount.objects.select_related('user').filter(
            provider=oauth_user.provider,
            provider_user_id=oauth_user.provider_user_id,
        ).first()
        if oauth_account:
            self._sync_oauth_account(oauth_account, oauth_user)
            return oauth_account.user, oauth_account, False

        user = current_user or self._find_user_by_email(oauth_user.email)
        is_created = False
        if user is None:
            user = self._create_user_from_oauth(oauth_user)
            is_created = True

        oauth_account = OAuthAccount.objects.create(
            user=user,
            provider=oauth_user.provider,
            provider_user_id=oauth_user.provider_user_id,
            email=oauth_user.email,
            name=oauth_user.name,
            profile_image=oauth_user.profile_image,
            raw_profile=oauth_user.raw_profile,
        )
        self._sync_user_profile(user, oauth_user)
        return user, oauth_account, is_created

    def _find_user_by_email(self, email):
        if not email:
            return None
        return User.objects.filter(email=email).first()

    def _create_user_from_oauth(self, oauth_user):
        base_username = oauth_user.name or oauth_user.email.split('@')[0] if oauth_user.email else oauth_user.provider
        user = User(
            email=oauth_user.email or f'{oauth_user.provider}_{oauth_user.provider_user_id}@oauth.local',
            username=self._build_unique_username(base_username),
            name=oauth_user.name,
            profile_image=oauth_user.profile_image,
        )
        user.set_unusable_password()
        user.save()
        return user

    def _sync_oauth_account(self, oauth_account, oauth_user):
        oauth_account.email = oauth_user.email
        oauth_account.name = oauth_user.name
        oauth_account.profile_image = oauth_user.profile_image
        oauth_account.raw_profile = oauth_user.raw_profile
        oauth_account.save(update_fields=['email', 'name', 'profile_image', 'raw_profile', 'updated_at'])

    def _sync_user_profile(self, user, oauth_user):
        update_fields = []
        if oauth_user.name and not user.name:
            user.name = oauth_user.name
            update_fields.append('name')
        if oauth_user.profile_image and not user.profile_image:
            user.profile_image = oauth_user.profile_image
            update_fields.append('profile_image')
        if update_fields:
            update_fields.append('updated_at')
            user.save(update_fields=update_fields)

    def _build_unique_username(self, base_username):
        normalized = ''.join(char for char in base_username if char.isalnum() or char in ['_', '-']) or 'oauth_user'
        candidate = normalized[:120]
        suffix = 1
        while User.objects.filter(username=candidate).exists():
            suffix += 1
            candidate = f'{normalized[:110]}_{suffix}'
        return candidate
