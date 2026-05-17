class OAuthError(Exception):
    pass


class UnsupportedOAuthProviderError(OAuthError):
    pass


class OAuthTokenVerificationError(OAuthError):
    pass


class OAuthConfigurationError(OAuthError):
    pass
