from apps.users.oauth.exceptions import UnsupportedOAuthProviderError
from apps.users.oauth.providers import GoogleOAuthProvider, KakaoOAuthProvider


class OAuthProviderRegistry:
    def __init__(self):
        self._providers = {
            GoogleOAuthProvider.provider_name: GoogleOAuthProvider,
            KakaoOAuthProvider.provider_name: KakaoOAuthProvider,
        }

    def get_provider(self, provider_name):
        provider_class = self._providers.get(provider_name)
        if provider_class is None:
            raise UnsupportedOAuthProviderError(f'Unsupported OAuth provider: {provider_name}')
        return provider_class()
