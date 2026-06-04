from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class SsafySyncPermissionTests(TestCase):
    def test_regular_user_cannot_run_ssafy_import(self):
        user = get_user_model().objects.create_user(username='member', password='pass')
        self.client.force_login(user)

        response = self.client.post(reverse('ssafy-sync'), data={'items': []}, content_type='application/json')

        self.assertEqual(response.status_code, 403)

    def test_staff_user_can_run_ssafy_import(self):
        user = get_user_model().objects.create_user(username='admin', password='pass', is_staff=True)
        self.client.force_login(user)

        response = self.client.post(reverse('ssafy-sync'), data={'items': []}, content_type='application/json')

        self.assertEqual(response.status_code, 200)