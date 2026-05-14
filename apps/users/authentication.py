from rest_framework import authentication, exceptions

from apps.users.jwt.service import JwtService
from apps.users.models import User


class JwtAuthentication(authentication.BaseAuthentication):
    keyword = 'Bearer'

    def authenticate(self, request):
        auth_header = authentication.get_authorization_header(request).decode('utf-8')
        if not auth_header:
            return None

        parts = auth_header.split()
        if len(parts) != 2 or parts[0] != self.keyword:
            return None

        try:
            payload = JwtService().verify(parts[1], expected_type='access')
            user = User.objects.get(id=payload['user_id'])
        except (ValueError, KeyError, User.DoesNotExist) as exc:
            raise exceptions.AuthenticationFailed('Invalid authentication token.') from exc

        return user, payload
