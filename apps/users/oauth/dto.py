from dataclasses import dataclass, field


@dataclass(frozen=True)
class OAuthUserInfo:
    provider: str
    provider_user_id: str
    email: str = ''
    name: str = ''
    profile_image: str = ''
    raw_profile: dict = field(default_factory=dict)
