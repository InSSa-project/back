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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
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

    def test_signup_requires_name_and_track(self):
        missing_name = self.client.post(
            reverse('users-signup'),
            data={'email': 'new@example.com', 'password': 'password', 'track': 'python'},
            content_type='application/json',
        )
        missing_track = self.client.post(
            reverse('users-signup'),
            data={'email': 'new2@example.com', 'password': 'password', 'name': '김싸피'},
            content_type='application/json',
        )

        self.assertEqual(missing_name.status_code, 400)
        self.assertEqual(missing_track.status_code, 400)
        self.assertIn('name', missing_name.json())
        self.assertIn('track', missing_track.json())

    def test_signup_saves_user_and_profile_fields(self):
        response = self.client.post(
            reverse('users-signup'),
            data={
                'email': 'signup@example.com',
                'password': 'password',
                'name': '김싸피',
                'track': 'python',
                'campus': '서울',
                'class_number': 7,
                'generation': 13,
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()['data']
        self.assertIn('access_token', payload)
        self.assertEqual(payload['user']['name'], '김싸피')
        self.assertEqual(payload['profile']['track'], 'python')
        profile = UserProfile.objects.get(user__email='signup@example.com')
        self.assertEqual(profile.user.name, '김싸피')
        self.assertEqual(profile.track, UserProfile.TRACK_PYTHON)
        self.assertEqual(profile.campus, '서울')
        self.assertEqual(profile.class_number, 7)

    def test_get_profile_auto_creates_profile_for_login_user(self):
        response = self.client.get(reverse('users-me-profile'), **self._auth(self.user))

        self.assertEqual(response.status_code, 200)
        payload = response.json()['data']
        self.assertEqual(payload['email'], 'user-a@example.com')
        self.assertEqual(payload['name'], '김싸피')
        self.assertEqual(payload['notification_email'], 'user-a@example.com')
        self.assertTrue(UserProfile.objects.filter(user=self.user).exists())

    def test_me_includes_profile_track_and_calendar_admin_flag(self):
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        UserProfile.objects.create(user=self.user, track=UserProfile.TRACK_DATA)

        response = self.client.get(reverse('users-me'), **self._auth(self.user))

        self.assertEqual(response.status_code, 200)
        payload = response.json()['data']
        self.assertEqual(payload['track'], UserProfile.TRACK_DATA)
        self.assertTrue(payload['can_manage_calendar'])

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
        payload = response.json()['data']
        self.assertEqual(payload['generation'], 12)
        self.assertEqual(payload['campus'], '서울')
        self.assertEqual(payload['track'], 'java_major')
        self.assertEqual(payload['class_number'], 17)
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.generation, 12)
        self.assertEqual(profile.campus, '서울')
        self.assertEqual(profile.track, UserProfile.TRACK_JAVA)
        self.assertEqual(profile.class_number, 17)
        self.assertEqual(profile.notification_email, 'user-a@example.com')

    def test_patch_profile_values_are_returned_by_get(self):
        patch_response = self.client.patch(
            reverse('users-me-profile'),
            data={
                'generation': 12,
                'campus': '서울',
                'track': 'java_major',
                'class_number': 18,
                'notice_notification_enabled': True,
                'schedule_reminder_enabled': True,
                'ai_question_notification_enabled': True,
            },
            content_type='application/json',
            **self._auth(self.user),
        )
        self.assertEqual(patch_response.status_code, 200)

        get_response = self.client.get(reverse('users-me-profile'), **self._auth(self.user))

        self.assertEqual(get_response.status_code, 200)
        payload = get_response.json()['data']
        self.assertEqual(payload['generation'], 12)
        self.assertEqual(payload['campus'], '서울')
        self.assertEqual(payload['track'], 'java_major')
        self.assertEqual(payload['class_number'], 18)
        self.assertTrue(payload['notice_notification_enabled'])
        self.assertTrue(payload['schedule_reminder_enabled'])
        self.assertTrue(payload['ai_question_notification_enabled'])

    def test_patch_profile_auto_creates_profile(self):
        self.assertFalse(UserProfile.objects.filter(user=self.user).exists())

        response = self.client.patch(
            reverse('users-me-profile'),
            data={'generation': 13, 'campus': '대전', 'track': 'python', 'class_number': 1},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 200)
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.generation, 13)
        self.assertEqual(profile.campus, '대전')
        self.assertEqual(profile.track, UserProfile.TRACK_PYTHON)
        self.assertEqual(profile.class_number, 1)

    def test_profile_requires_authentication(self):
        response = self.client.get(reverse('users-me-profile'))

        self.assertEqual(response.status_code, 401)

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

    def test_profile_setup_requires_authentication(self):
        response = self.client.post(
            reverse('users-profile-setup'),
            data={'name': '김싸피', 'track': 'python'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 401)

    def test_profile_setup_saves_user_and_creates_profile(self):
        self.user.name = ''
        self.user.save(update_fields=['name'])
        self.assertFalse(UserProfile.objects.filter(user=self.user).exists())

        response = self.client.post(
            reverse('users-profile-setup'),
            data={
                'name': '김싸피',
                'track': 'python',
                'campus': '서울',
                'class_number': 1,
                'generation': 13,
            },
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()['data']
        self.assertFalse(payload['requires_profile_setup'])
        self.assertEqual(payload['missing_profile_fields'], [])
        self.assertEqual(payload['user']['name'], '김싸피')
        self.assertEqual(payload['profile']['track'], 'python')
        self.assertEqual(payload['profile']['campus'], '서울')
        profile = UserProfile.objects.get(user=self.user)
        self.user.refresh_from_db()
        self.assertEqual(self.user.name, '김싸피')
        self.assertEqual(profile.track, UserProfile.TRACK_PYTHON)
        self.assertEqual(profile.class_number, 1)
        self.assertEqual(profile.generation, 13)

    def test_profile_setup_requires_name(self):
        response = self.client.post(
            reverse('users-profile-setup'),
            data={'track': 'python'},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.json())

    def test_profile_setup_requires_track(self):
        response = self.client.post(
            reverse('users-profile-setup'),
            data={'name': '김싸피'},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('track', response.json())

    def test_profile_setup_updates_existing_profile(self):
        UserProfile.objects.create(
            user=self.user,
            track=UserProfile.TRACK_JAVA,
            campus='대전',
            class_number=7,
            generation=12,
        )

        response = self.client.post(
            reverse('users-profile-setup'),
            data={
                'name': '이싸피',
                'track': 'python',
                'campus': '서울',
                'class_number': 2,
                'generation': 13,
            },
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 200)
        profile = UserProfile.objects.get(user=self.user)
        self.user.refresh_from_db()
        self.assertEqual(self.user.name, '이싸피')
        self.assertEqual(profile.track, UserProfile.TRACK_PYTHON)
        self.assertEqual(profile.campus, '서울')
        self.assertEqual(profile.class_number, 2)
        self.assertEqual(profile.generation, 13)

    def test_profile_get_reports_setup_complete_after_profile_setup(self):
        setup_response = self.client.post(
            reverse('users-profile-setup'),
            data={'name': '김싸피', 'track': 'python'},
            content_type='application/json',
            **self._auth(self.user),
        )
        self.assertEqual(setup_response.status_code, 200)

        response = self.client.get(reverse('users-me-profile'), **self._auth(self.user))

        self.assertEqual(response.status_code, 200)
        payload = response.json()['data']
        self.assertFalse(payload['requires_profile_setup'])
        self.assertEqual(payload['missing_profile_fields'], [])


@override_settings(
    MATTERMOST_BASE_URL='https://meeting.ssafy.com',
    MATTERMOST_TIMEOUT_SECONDS=5,
    DEBUG=False,
    SECURE_SSL_REDIRECT=False,
)
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
        self.assertTrue(payload['is_new_user'])
        self.assertTrue(payload['requires_profile_setup'])
        self.assertEqual(payload['missing_profile_fields'], ['track'])
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
    def test_mattermost_login_requires_setup_when_existing_user_has_no_name(self, mock_post):
        User = get_user_model()
        user = User.objects.create_user(username='existing-mm', email='existing-mm@example.com', password='password', name='')
        UserProfile.objects.create(
            user=user,
            mattermost_user_id='mm-existing-name',
            mattermost_username='existing-mm',
            track=UserProfile.TRACK_PYTHON,
        )
        mock_post.return_value = self._mattermost_response(
            payload={
                'id': 'mm-existing-name',
                'username': 'existing-mm',
                'nickname': '',
                'email': 'existing-mm@example.com',
            }
        )

        response = self.client.post(
            '/api/v1/users/auth/mattermost/login/',
            data={'login_id': 'existing-mm', 'password': 'secret-password'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()['data']
        self.assertFalse(payload['is_new_user'])
        self.assertTrue(payload['requires_profile_setup'])
        self.assertEqual(payload['missing_profile_fields'], ['name'])

    @patch('apps.users.services.requests.post')
    def test_mattermost_login_requires_setup_when_profile_has_no_track(self, mock_post):
        User = get_user_model()
        user = User.objects.create_user(username='existing-track', email='existing-track@example.com', password='password', name='김싸피')
        UserProfile.objects.create(
            user=user,
            mattermost_user_id='mm-existing-track',
            mattermost_username='existing-track',
        )
        mock_post.return_value = self._mattermost_response(
            payload={
                'id': 'mm-existing-track',
                'username': 'existing-track',
                'nickname': '김싸피',
                'email': 'existing-track@example.com',
            }
        )

        response = self.client.post(
            '/api/v1/users/auth/mattermost/login/',
            data={'login_id': 'existing-track', 'password': 'secret-password'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()['data']
        self.assertFalse(payload['is_new_user'])
        self.assertTrue(payload['requires_profile_setup'])
        self.assertEqual(payload['missing_profile_fields'], ['track'])

    @patch('apps.users.services.requests.post')
    def test_mattermost_login_does_not_require_setup_when_name_and_track_exist(self, mock_post):
        User = get_user_model()
        user = User.objects.create_user(username='complete-mm', email='complete-mm@example.com', password='password', name='김싸피')
        UserProfile.objects.create(
            user=user,
            mattermost_user_id='mm-complete',
            mattermost_username='complete-mm',
            track=UserProfile.TRACK_PYTHON,
        )
        mock_post.return_value = self._mattermost_response(
            payload={
                'id': 'mm-complete',
                'username': 'complete-mm',
                'nickname': '김싸피',
                'email': 'complete-mm@example.com',
            }
        )

        response = self.client.post(
            '/api/v1/users/auth/mattermost/login/',
            data={'login_id': 'complete-mm', 'password': 'secret-password'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()['data']
        self.assertFalse(payload['is_new_user'])
        self.assertFalse(payload['requires_profile_setup'])
        self.assertEqual(payload['missing_profile_fields'], [])

    @patch('apps.users.services.requests.post')
    def test_mattermost_login_reports_setup_complete_after_profile_setup(self, mock_post):
        mock_post.return_value = self._mattermost_response(
            payload={
                'id': 'mm-setup',
                'username': 'setup-user',
                'nickname': '',
                'email': 'setup@example.com',
            }
        )
        login_response = self.client.post(
            '/api/v1/users/auth/mattermost/login/',
            data={'login_id': 'setup-user', 'password': 'secret-password'},
            content_type='application/json',
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertTrue(login_response.json()['data']['requires_profile_setup'])
        access_token = login_response.json()['data']['access_token']

        setup_response = self.client.post(
            reverse('users-profile-setup'),
            data={'name': '김싸피', 'track': 'python'},
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {access_token}',
        )
        self.assertEqual(setup_response.status_code, 200)

        second_login_response = self.client.post(
            '/api/v1/users/auth/mattermost/login/',
            data={'login_id': 'setup-user', 'password': 'secret-password'},
            content_type='application/json',
        )

        self.assertEqual(second_login_response.status_code, 200)
        payload = second_login_response.json()['data']
        self.assertFalse(payload['is_new_user'])
        self.assertFalse(payload['requires_profile_setup'])
        self.assertEqual(payload['missing_profile_fields'], [])

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
