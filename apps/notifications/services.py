from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from apps.risk.services import RiskService
from sync.models import RawSsafyData
from sync.services.notice_policy import notice_source_url, notice_title, user_visible_notice_queryset

from .models import Notification


class NotificationService:
    LIST_LIMIT = 80
    NOTICE_LIMIT = 5
    RISK_LIMIT = 5

    def list_notifications(self, user):
        self.refresh_agent_notifications(user)
        self.prune_read_notifications(user)
        since = timezone.now() - timedelta(days=3)
        return (
            Notification.objects.filter(user=user)
            .filter(Q(is_read=False) | Q(created_at__gte=since))
            .order_by('-is_read', '-created_at', '-id')[: self.LIST_LIMIT]
        )

    def mark_all_read(self, user):
        now = timezone.now()
        Notification.objects.filter(user=user, is_read=False).update(is_read=True, sent_at=now)
        self.prune_read_notifications(user)

    def mark_read(self, user, notification_id):
        now = timezone.now()
        return (
            Notification.objects.filter(user=user, id=notification_id, is_read=False)
            .update(is_read=True, sent_at=now)
        )

    def refresh_agent_notifications(self, user):
        self._create_risk_notifications(user)
        if self._notice_enabled(user):
            self._create_notice_notifications(user)

    def prune_read_notifications(self, user):
        cutoff = timezone.now() - timedelta(days=3)
        Notification.objects.filter(user=user, is_read=True, created_at__lt=cutoff).delete()

    def _create_risk_notifications(self, user):
        if not self._schedule_enabled(user):
            return
        try:
            dashboard = RiskService().calculate_dashboard(user)
        except Exception:
            return
        items = list(dashboard.get('recommended_schedules') or [])[: self.RISK_LIMIT]
        if not items:
            items = list(dashboard.get('upcoming_schedules') or [])[: self.RISK_LIMIT]
        for item in items:
            event_id = item.get('schedule_event_id') or item.get('id')
            title = item.get('card_title') or item.get('title') or '중요 일정'
            summary = (
                ((item.get('details') or {}).get('summary'))
                or item.get('reason')
                or item.get('date')
                or '확인해야 할 중요 일정이 있습니다.'
            )
            self._get_or_create(
                user=user,
                notification_type=Notification.TYPE_RISK,
                schedule_event_id=None,
                title=f'중요 일정: {title}',
                content=summary,
                link_url='/risk',
            )

    def _create_notice_notifications(self, user):
        try:
            notices = list(
                user_visible_notice_queryset(RawSsafyData.objects.all())
                .only('id', 'title', 'source_url', 'metadata_json', 'raw_text', 'collected_at')
                .order_by('-collected_at', '-id')[: self.NOTICE_LIMIT]
            )
        except Exception:
            return
        for raw_data in notices:
            title = notice_title(raw_data) or raw_data.title or '새 공지'
            source_url = notice_source_url(raw_data) or ''
            self._get_or_create(
                user=user,
                notification_type=Notification.TYPE_NOTICE,
                schedule_event_id=None,
                title=f'공지 알림: {title}',
                content='새 SSAFY 공지가 등록됐어요. 필요한 내용인지 확인해 주세요.',
                link_url=source_url or '/notices',
            )

    def _get_or_create(self, user, notification_type, schedule_event_id, title, content, link_url):
        queryset = Notification.objects.filter(
            user=user,
            notification_type=notification_type,
            title=title[:255],
        )
        if schedule_event_id:
            queryset = queryset.filter(schedule_event_id=schedule_event_id)
        else:
            queryset = queryset.filter(schedule_event__isnull=True)
        if queryset.exists():
            return
        Notification.objects.create(
            user=user,
            notification_type=notification_type,
            schedule_event_id=schedule_event_id,
            title=title[:255],
            content=content or '',
            link_url=link_url or '',
        )

    def _notice_enabled(self, user):
        profile = getattr(user, 'profile', None)
        return bool(getattr(profile, 'notice_notification_enabled', True))

    def _schedule_enabled(self, user):
        profile = getattr(user, 'profile', None)
        return bool(getattr(profile, 'schedule_reminder_enabled', True))
