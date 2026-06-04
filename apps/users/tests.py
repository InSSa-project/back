import tempfile
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

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
