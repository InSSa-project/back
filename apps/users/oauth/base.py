from typing import Protocol

from apps.users.oauth.dto import OAuthUserInfo


class OAuthProvider(Protocol):
    provider_name: str

    def get_user_info(self, access_token: str) -> OAuthUserInfo:
        """Verify provider token and return normalized user info."""

    def get_authorization_url(self, state: str, redirect_uri: str | None = None) -> str:
        """Return provider authorization URL for browser-based login."""

    def exchange_code(self, code: str, redirect_uri: str | None = None) -> str:
        """Exchange authorization code for provider access token."""
