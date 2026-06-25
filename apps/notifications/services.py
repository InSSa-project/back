from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

from apps.risk.services import RiskService
from sync.models import RawSsafyData
from sync.services.notice_policy import (
    notice_publication_date,
    notice_publication_values,
    notice_sort_key,
    notice_source_url,
    notice_title,
    user_visible_notice_queryset,
)

from .models import Notification


class NotificationService:
    LIST_LIMIT = 80
    NOTICE_LIMIT = 5
    RISK_LIMIT = 5
    NOTICE_RECENT_DAYS = 30
    READ_RETENTION_DAYS = 3

    def list_notifications(self, user):
        self.refresh_agent_notifications(user)
        self.prune_read_notifications(user)
        since = timezone.now() - timedelta(days=self.READ_RETENTION_DAYS)
        notice_since = timezone.now() - timedelta(days=self.NOTICE_RECENT_DAYS)
        return (
            Notification.objects.filter(user=user)
            .filter(Q(is_read=False) | Q(created_at__gte=since))
            .exclude(notification_type=Notification.TYPE_NOTICE, created_at__lt=notice_since)
            .order_by('is_read', '-created_at', '-id')[: self.LIST_LIMIT]
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
        cutoff = timezone.now() - timedelta(days=self.READ_RETENTION_DAYS)
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
                title=f'\uC911\uC694 \uC77C\uC815: {title}',
                content=summary,
                link_url='/risk',
            )

    def _create_notice_notifications(self, user):
        try:
            rows = list(
                user_visible_notice_queryset(RawSsafyData.objects.all())
                .only('id', 'title', 'source_url', 'metadata_json', 'raw_text', 'collected_at')
            )
            today = timezone.localdate()
            since = today - timedelta(days=self.NOTICE_RECENT_DAYS)
            notices = [
                raw_data
                for raw_data in rows
                if self._notice_display_date(raw_data) and since <= self._notice_display_date(raw_data) <= today
            ]
            notices = sorted(notices, key=notice_sort_key)[: self.NOTICE_LIMIT]
        except Exception:
            return
        for raw_data in notices:
            title = notice_title(raw_data) or raw_data.title or '\uC0C8 \uACF5\uC9C0'
            source_url = notice_source_url(raw_data) or ''
            created_at = self._notice_display_datetime(raw_data)
            self._get_or_create(
                user=user,
                notification_type=Notification.TYPE_NOTICE,
                schedule_event_id=None,
                title=f'\uACF5\uC9C0 \uC54C\uB9BC: {title}',
                content='\uC0C8 SSAFY \uACF5\uC9C0\uAC00 \uB4F1\uB85D\uB418\uC5C8\uC5B4\uC694. \uD544\uC694\uD55C \uB0B4\uC6A9\uC778\uC9C0 \uD655\uC778\uD574 \uC8FC\uC138\uC694.',
                link_url=source_url or '/notices',
                created_at=created_at,
            )

    def _get_or_create(self, user, notification_type, schedule_event_id, title, content, link_url, created_at=None):
        queryset = Notification.objects.filter(
            user=user,
            notification_type=notification_type,
            title=title[:255],
        )
        if schedule_event_id:
            queryset = queryset.filter(schedule_event_id=schedule_event_id)
        else:
            queryset = queryset.filter(schedule_event__isnull=True)
        existing = queryset.first()
        if existing:
            if created_at and existing.created_at != created_at:
                Notification.objects.filter(id=existing.id).update(created_at=created_at)
            return
        notification = Notification.objects.create(
            user=user,
            notification_type=notification_type,
            schedule_event_id=schedule_event_id,
            title=title[:255],
            content=content or '',
            link_url=link_url or '',
        )
        if created_at:
            Notification.objects.filter(id=notification.id).update(created_at=created_at)

    def _notice_display_date(self, raw_data):
        notice_date = notice_publication_date(raw_data)
        if notice_date:
            return notice_date
        collected_at = getattr(raw_data, 'collected_at', None)
        if not collected_at:
            return None
        return timezone.localdate(collected_at)

    def _notice_display_datetime(self, raw_data):
        published_at, notice_date = notice_publication_values(raw_data)
        if published_at:
            return published_at
        if notice_date:
            return timezone.make_aware(datetime.combine(notice_date, time.min), timezone.get_current_timezone())
        collected_at = getattr(raw_data, 'collected_at', None)
        return collected_at

    def _notice_enabled(self, user):
        profile = getattr(user, 'profile', None)
        return bool(getattr(profile, 'notice_notification_enabled', True))

    def _schedule_enabled(self, user):
        profile = getattr(user, 'profile', None)
        return bool(getattr(profile, 'schedule_reminder_enabled', True))
