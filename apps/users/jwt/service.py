import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone

from django.conf import settings
from apps.users.models import JwtSession


class JwtService:
    algorithm = 'HS256'

    def issue_pair(self, user):
        return {
            'access_token': self.issue_token(user, token_type='access'),
            'refresh_token': self.issue_token(user, token_type='refresh'),
        }

    def issue_token(self, user, token_type):
        now = int(time.time())
        lifetime = self._get_token_lifetime(token_type)
        payload = {
            'token_type': token_type,
            'user_id': user.id,
            'email': user.email,
            'iat': now,
            'exp': now + lifetime,
            'jti': str(uuid.uuid4()),
        }
        if token_type == 'access' and self._sliding_access_enabled():
            self._create_access_session(user, payload)
        return self._encode(payload)

    def verify(self, token, expected_type='access'):
        payload = self._decode(token)
        if payload.get('token_type') != expected_type:
            raise ValueError('Invalid token type.')
        if int(payload.get('exp', 0)) < int(time.time()):
            raise ValueError('Token expired.')
        if expected_type == 'access' and self._sliding_access_enabled():
            self._verify_access_session(payload)
        return payload

    def _get_lifetime(self, token_type):
        if token_type == 'refresh':
            return int(getattr(settings, 'JWT_REFRESH_LIFETIME_SECONDS', 60 * 60 * 24 * 14))
        return int(getattr(settings, 'JWT_ACCESS_LIFETIME_SECONDS', 60 * 15))

    def _get_token_lifetime(self, token_type):
        if token_type == 'access' and self._sliding_access_enabled():
            return int(getattr(settings, 'JWT_ACCESS_MAX_LIFETIME_SECONDS', self._get_lifetime('refresh')))
        return self._get_lifetime(token_type)

    def _sliding_access_enabled(self):
        return bool(getattr(settings, 'JWT_ACCESS_SLIDING_EXPIRATION', False))

    def _create_access_session(self, user, payload):
        issued_at = self._datetime_from_timestamp(payload['iat'])
        max_expires_at = self._datetime_from_timestamp(payload['exp'])
        idle_expires_at = min(
            self._datetime_from_timestamp(payload['iat'] + self._get_lifetime('access')),
            max_expires_at,
        )
        JwtSession.objects.create(
            user=user,
            jti=payload['jti'],
            token_type='access',
            issued_at=issued_at,
            last_seen_at=issued_at,
            idle_expires_at=idle_expires_at,
            max_expires_at=max_expires_at,
        )

    def _verify_access_session(self, payload):
        now = int(time.time())
        now_dt = self._datetime_from_timestamp(now)

        session = (
            JwtSession.objects
            .filter(
                jti=payload.get('jti'),
                user_id=payload.get('user_id'),
                token_type='access',
            )
            .first()
        )
        if session is None:
            session = self._create_legacy_access_session(payload, now_dt)
        if session.revoked_at:
            raise ValueError('Token revoked.')
        if session.max_expires_at < now_dt:
            raise ValueError('Token expired.')
        if session.idle_expires_at < now_dt:
            raise ValueError('Token expired by inactivity.')

        new_idle_expires_at = min(
            self._datetime_from_timestamp(now + self._get_lifetime('access')),
            session.max_expires_at,
        )
        JwtSession.objects.filter(pk=session.pk).update(
            last_seen_at=now_dt,
            idle_expires_at=new_idle_expires_at,
            updated_at=now_dt,
        )

    def _create_legacy_access_session(self, payload, now_dt):
        max_expires_at = self._datetime_from_timestamp(int(payload.get('exp', 0)))
        session, _ = JwtSession.objects.get_or_create(
            jti=payload['jti'],
            user_id=payload['user_id'],
            defaults={
                'token_type': 'access',
                'issued_at': self._datetime_from_timestamp(int(payload.get('iat', time.time()))),
                'last_seen_at': now_dt,
                'idle_expires_at': min(
                    self._datetime_from_timestamp(int(time.time()) + self._get_lifetime('access')),
                    max_expires_at,
                ),
                'max_expires_at': max_expires_at,
            },
        )
        return session

    def _datetime_from_timestamp(self, value):
        return datetime.fromtimestamp(int(value), tz=timezone.utc)

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
