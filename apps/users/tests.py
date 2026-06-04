import tempfile
from io import BytesIO
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
import requests

from apps.users.jwt.service import JwtService
from apps.users.models import UserProfile


TEST_MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class UserProfileApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='user-a', email='user-a@example.com', password='password', name='김싸피')
        self.other_user = User.objects.create_user(username='user-b', email='user-b@example.com', password='password')

    def _auth(self, user):
        return {'HTTP_AUTHORIZATION': f"Bearer {JwtService().issue_token(user, token_type='access')}"}

    def _png_bytes(self):
        output = BytesIO()
        Image.new('RGB', (1, 1), color='white').save(output, format='PNG')
        return output.getvalue()

    def test_get_profile_auto_creates_profile_for_login_user(self):
        response = self.client.get(reverse('users-me-profile'), **self._auth(self.user))

        self.assertEqual(response.status_code, 200)
        payload = response.json()['data']
        self.assertEqual(payload['email'], 'user-a@example.com')
        self.assertEqual(payload['name'], '김싸피')
        self.assertEqual(payload['notification_email'], 'user-a@example.com')
        self.assertTrue(UserProfile.objects.filter(user=self.user).exists())

    def test_patch_profile_saves_ssafy_fields(self):
        response = self.client.patch(
            reverse('users-me-profile'),
            data={
                'generation': 12,
                'campus': '서울',
                'track': 'java_major',
                'class_number': 17,
            },
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 200)
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.generation, 12)
        self.assertEqual(profile.campus, '서울')
        self.assertEqual(profile.track, 'java_major')
        self.assertEqual(profile.class_number, 17)
        self.assertEqual(profile.notification_email, 'user-a@example.com')

    def test_notification_email_can_be_blank_when_notifications_enabled(self):
        response = self.client.patch(
            reverse('users-me-profile'),
            data={
                'notification_email': '',
                'notice_notification_enabled': True,
                'schedule_reminder_enabled': True,
                'ai_question_notification_enabled': True,
            },
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 200)
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.notification_email, '')
        self.assertTrue(profile.notice_notification_enabled)
        self.assertTrue(profile.schedule_reminder_enabled)
        self.assertTrue(profile.ai_question_notification_enabled)

    def test_notification_email_can_be_omitted_when_notifications_enabled(self):
        UserProfile.objects.create(user=self.user, notification_email=None)

        response = self.client.patch(
            reverse('users-me-profile'),
            data={
                'notice_notification_enabled': True,
                'schedule_reminder_enabled': True,
                'ai_question_notification_enabled': True,
            },
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 200)
        profile = UserProfile.objects.get(user=self.user)
        self.assertIsNone(profile.notification_email)
        self.assertTrue(profile.notice_notification_enabled)

    def test_notification_email_can_be_blank_when_all_notifications_disabled(self):
        response = self.client.patch(
            reverse('users-me-profile'),
            data={
                'notification_email': '',
                'notice_notification_enabled': False,
                'schedule_reminder_enabled': False,
                'ai_question_notification_enabled': False,
            },
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 200)
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.notification_email, '')

    def test_invalid_campus_is_rejected(self):
        response = self.client.patch(
            reverse('users-me-profile'),
            data={'campus': '부산', 'notification_email': 'user-a@example.com'},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('campus', response.json())

    def test_invalid_track_is_rejected(self):
        response = self.client.patch(
            reverse('users-me-profile'),
            data={'track': 'Rust', 'notification_email': 'user-a@example.com'},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('track', response.json())

    def test_profile_image_upload_accepts_png(self):
        image = SimpleUploadedFile('avatar.png', self._png_bytes(), content_type='image/png')

        response = self.client.post(
            reverse('users-me-profile-image'),
            data={'image': image},
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 200)
        profile = UserProfile.objects.get(user=self.user)
        self.assertTrue(profile.profile_image.name.startswith('profile_images/'))
        self.assertIsNotNone(response.json()['data']['profile_image_url'])

    def test_profile_image_upload_rejects_invalid_extension(self):
        image = SimpleUploadedFile('avatar.gif', self._png_bytes(), content_type='image/png')

        response = self.client.post(
            reverse('users-me-profile-image'),
            data={'image': image},
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('image', response.json())

    def test_profile_image_delete_clears_field(self):
        profile = UserProfile.objects.create(user=self.user, notification_email=self.user.email)
        profile.profile_image.save('avatar.png', SimpleUploadedFile('avatar.png', self._png_bytes(), content_type='image/png'))

        response = self.client.delete(reverse('users-me-profile-image'), **self._auth(self.user))

        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        self.assertFalse(profile.profile_image)

    def test_profile_endpoint_is_limited_to_request_user(self):
        UserProfile.objects.create(user=self.other_user, notification_email='other@example.com', campus='대전')

        response = self.client.get(reverse('users-me-profile'), **self._auth(self.user))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['email'], 'user-a@example.com')
        self.assertNotEqual(response.json()['data']['email'], 'user-b@example.com')


@override_settings(MATTERMOST_BASE_URL='https://meeting.ssafy.com', MATTERMOST_TIMEOUT_SECONDS=5, DEBUG=False)
class MattermostLoginApiTests(TestCase):
    def _mattermost_response(self, status_code=200, payload=None):
        response = Mock()
        response.status_code = status_code
        response.json.return_value = payload or {
            'id': 'mm-user-1',
            'username': 'ssafy-user',
            'nickname': '김싸피',
            'email': 'ssafy@example.com',
        }
        return response

    @patch('apps.users.services.requests.post')
    def test_mattermost_login_success_issues_tokens_and_saves_profile(self, mock_post):
        mock_post.return_value = self._mattermost_response()

        response = self.client.post(
            '/api/v1/users/auth/mattermost/login/',
            data={'login_id': 'ssafy-user', 'password': 'secret-password'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()['data']
        self.assertIn('access_token', payload)
        self.assertIn('refresh_token', payload)
        self.assertEqual(payload['user']['mattermost_user_id'], 'mm-user-1')
        self.assertEqual(payload['user']['mattermost_username'], 'ssafy-user')
        self.assertNotIn('secret-password', str(response.json()))
        profile = UserProfile.objects.get(mattermost_user_id='mm-user-1')
        self.assertEqual(profile.mattermost_username, 'ssafy-user')
        self.assertEqual(profile.user.email, 'ssafy@example.com')
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args.kwargs['json']['login_id'], 'ssafy-user')
        self.assertEqual(mock_post.call_args.kwargs['json']['password'], 'secret-password')

    @patch('apps.users.services.requests.post')
    def test_mattermost_login_alias_path_also_works(self, mock_post):
        mock_post.return_value = self._mattermost_response(
            payload={
                'id': 'mm-user-2',
                'username': 'alias-user',
                'nickname': '',
                'email': 'alias@example.com',
            }
        )

        response = self.client.post(
            '/api/users/auth/mattermost/login/',
            data={'login_id': 'alias-user', 'password': 'secret-password'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['user']['mattermost_user_id'], 'mm-user-2')

    @patch('apps.users.services.requests.post')
    def test_mattermost_login_auth_failure_returns_401(self, mock_post):
        mock_post.return_value = self._mattermost_response(status_code=401)

        response = self.client.post(
            '/api/v1/users/auth/mattermost/login/',
            data={'login_id': 'ssafy-user', 'password': 'wrong-password'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['message'], 'Mattermost 인증에 실패했습니다.')
        self.assertNotIn('wrong-password', str(response.json()))

    @patch('django.core.handlers.base.log_response')
    @patch('apps.users.services.requests.post')
    def test_mattermost_login_timeout_returns_503(self, mock_post, _mock_log_response):
        mock_post.side_effect = requests.Timeout()

        response = self.client.post(
            '/api/v1/users/auth/mattermost/login/',
            data={'login_id': 'ssafy-user', 'password': 'secret-password'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['message'], 'Mattermost 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.')
        self.assertNotIn('secret-password', str(response.json()))

    @override_settings(MATTERMOST_BASE_URL='')
    @patch('django.core.handlers.base.log_response')
    def test_mattermost_login_missing_config_returns_503(self, _mock_log_response):
        response = self.client.post(
            '/api/v1/users/auth/mattermost/login/',
            data={'login_id': 'ssafy-user', 'password': 'secret-password'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['message'], 'Mattermost 연동 설정이 필요합니다.')
