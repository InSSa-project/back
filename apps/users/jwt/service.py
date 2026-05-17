import base64
import hashlib
import hmac
import json
import time
import uuid

from django.conf import settings


class JwtService:
    algorithm = 'HS256'

    def issue_pair(self, user):
        return {
            'access_token': self.issue_token(user, token_type='access'),
            'refresh_token': self.issue_token(user, token_type='refresh'),
        }

    def issue_token(self, user, token_type):
        now = int(time.time())
        lifetime = self._get_lifetime(token_type)
        payload = {
            'token_type': token_type,
            'user_id': user.id,
            'email': user.email,
            'iat': now,
            'exp': now + lifetime,
            'jti': str(uuid.uuid4()),
        }
        return self._encode(payload)

    def verify(self, token, expected_type='access'):
        payload = self._decode(token)
        if payload.get('token_type') != expected_type:
            raise ValueError('Invalid token type.')
        if int(payload.get('exp', 0)) < int(time.time()):
            raise ValueError('Token expired.')
        return payload

    def _get_lifetime(self, token_type):
        if token_type == 'refresh':
            return int(getattr(settings, 'JWT_REFRESH_LIFETIME_SECONDS', 60 * 60 * 24 * 14))
        return int(getattr(settings, 'JWT_ACCESS_LIFETIME_SECONDS', 60 * 15))

    def _encode(self, payload):
        header = {'typ': 'JWT', 'alg': self.algorithm}
        signing_input = '.'.join([
            self._base64url_encode(header),
            self._base64url_encode(payload),
        ])
        signature = self._sign(signing_input)
        return f'{signing_input}.{signature}'

    def _decode(self, token):
        try:
            encoded_header, encoded_payload, signature = token.split('.')
        except ValueError as exc:
            raise ValueError('Malformed token.') from exc

        signing_input = f'{encoded_header}.{encoded_payload}'
        expected_signature = self._sign(signing_input)
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError('Invalid token signature.')

        payload_json = base64.urlsafe_b64decode(self._pad(encoded_payload)).decode('utf-8')
        return json.loads(payload_json)

    def _sign(self, signing_input):
        digest = hmac.new(
            key=settings.SECRET_KEY.encode('utf-8'),
            msg=signing_input.encode('utf-8'),
            digestmod=hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')

    def _base64url_encode(self, data):
        raw = json.dumps(data, separators=(',', ':'), sort_keys=True).encode('utf-8')
        return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')

    def _pad(self, value):
        return value + '=' * (-len(value) % 4)
