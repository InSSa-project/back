
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.users.models import UserProfile
from sync.models import RawSsafyData

from .models import Notification


class NotificationApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='notification-user',
            email='notification@example.com',
            password='password',
        )
        self.profile = UserProfile.objects.create(
            user=self.user,
            notification_email=self.user.email,
            notice_notification_enabled=True,
            schedule_reminder_enabled=False,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_list_keeps_unread_and_prunes_old_read_notifications(self):
        unread_old = Notification.objects.create(
            user=self.user,
            notification_type=Notification.TYPE_SYSTEM,
            title='Unread old',
            content='keep',
            is_read=False,
        )
        read_old = Notification.objects.create(
            user=self.user,
            notification_type=Notification.TYPE_SYSTEM,
            title='Read old',
            content='delete',
            is_read=True,
        )
        Notification.objects.filter(id__in=[unread_old.id, read_old.id]).update(
            created_at=timezone.now() - timedelta(days=4)
        )

        response = self.client.get(reverse('notifications-list'))

        self.assertEqual(response.status_code, 200)
        titles = [item['title'] for item in response.json()['data']]
        self.assertIn('Unread old', titles)
        self.assertNotIn('Read old', titles)
        self.assertFalse(Notification.objects.filter(id=read_old.id).exists())

    def test_notice_notifications_follow_profile_toggle(self):
        RawSsafyData.objects.create(
            source_type='notice',
            source_url='https://edu.ssafy.com/notice/1',
            title='새 공지',
            raw_text='공지 본문',
            metadata_json={'notice_date': timezone.localdate().isoformat()},
        )

        enabled_response = self.client.get(reverse('notifications-list'))
        self.assertTrue(
            any(item['notification_type'] == Notification.TYPE_NOTICE for item in enabled_response.json()['data'])
        )

        Notification.objects.filter(user=self.user).delete()
        self.profile.notice_notification_enabled = False
        self.profile.save(update_fields=['notice_notification_enabled'])

        disabled_response = self.client.get(reverse('notifications-list'))
        self.assertFalse(
            any(item['notification_type'] == Notification.TYPE_NOTICE for item in disabled_response.json()['data'])
        )

    def test_list_orders_unread_notifications_before_read_notifications(self):
        read_recent = Notification.objects.create(
            user=self.user,
            notification_type=Notification.TYPE_NOTICE,
            title='Read recent notice',
            content='already read',
            is_read=True,
        )
        unread_older = Notification.objects.create(
            user=self.user,
            notification_type=Notification.TYPE_NOTICE,
            title='Unread older notice',
            content='must show first',
            is_read=False,
        )
        Notification.objects.filter(id=read_recent.id).update(created_at=timezone.now())
        Notification.objects.filter(id=unread_older.id).update(created_at=timezone.now() - timedelta(days=1))

        response = self.client.get(reverse('notifications-list'))

        self.assertEqual(response.status_code, 200)
        titles = [item['title'] for item in response.json()['data']]
        self.assertLess(titles.index('Unread older notice'), titles.index('Read recent notice'))
    def test_mark_all_read(self):
        Notification.objects.create(
            user=self.user,
            notification_type=Notification.TYPE_SYSTEM,
            title='Unread',
            content='read me',
            is_read=False,
        )

        response = self.client.post(reverse('notifications-read-all'))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Notification.objects.filter(user=self.user, is_read=False).exists())
