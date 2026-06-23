from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.users.jwt.service import JwtService
from apps.users.models import JwtSession


@override_settings(
    JWT_ACCESS_SLIDING_EXPIRATION=True,
    JWT_ACCESS_LIFETIME_SECONDS=10,
    JWT_ACCESS_MAX_LIFETIME_SECONDS=100,
    SECURE_SSL_REDIRECT=False,
)
class JwtSlidingExpirationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='jwt-user',
            email='jwt-user@example.com',
            password='password',
        )

    def test_access_token_idle_expiration_is_extended_on_verify(self):
        with patch('apps.users.jwt.service.time.time', return_value=1000):
            token = JwtService().issue_token(self.user, token_type='access')

        session = JwtSession.objects.get(user=self.user, token_type='access')
        first_idle_expires_at = session.idle_expires_at

        with patch('apps.users.jwt.service.time.time', return_value=1005):
            payload = JwtService().verify(token, expected_type='access')

        session.refresh_from_db()
        self.assertEqual(payload['user_id'], self.user.id)
        self.assertGreater(session.idle_expires_at, first_idle_expires_at)

    def test_access_token_fails_after_inactivity_window(self):
        with patch('apps.users.jwt.service.time.time', return_value=1000):
            token = JwtService().issue_token(self.user, token_type='access')

        with patch('apps.users.jwt.service.time.time', return_value=1011):
            with self.assertRaisesMessage(ValueError, 'Token expired by inactivity.'):
                JwtService().verify(token, expected_type='access')

    def test_sliding_access_token_is_capped_by_max_lifetime(self):
        with patch('apps.users.jwt.service.time.time', return_value=1000):
            token = JwtService().issue_token(self.user, token_type='access')

        with patch('apps.users.jwt.service.time.time', return_value=1005):
            JwtService().verify(token, expected_type='access')

        with patch('apps.users.jwt.service.time.time', return_value=1014):
            JwtService().verify(token, expected_type='access')

        session = JwtSession.objects.get(user=self.user, token_type='access')
        self.assertLessEqual(session.idle_expires_at, session.max_expires_at)
