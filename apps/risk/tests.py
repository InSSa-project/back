from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.risk.models import EvaluationResult, RiskStatus
from apps.users.jwt.service import JwtService
from schedules.models import ScheduleEvent


class RiskApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='user-a', email='risk-a@example.com', password='password')
        self.other_user = User.objects.create_user(username='user-b', email='risk-b@example.com', password='password')

    def _auth(self, user):
        return {'HTTP_AUTHORIZATION': f"Bearer {JwtService().issue_token(user, token_type='access')}"}

    def _status_payload(self, user):
        response = self.client.get(reverse('risk-status'), **self._auth(user))
        self.assertEqual(response.status_code, 200)
        return response.json()['data']

    def test_unauthenticated_user_cannot_access_risk_status(self):
        response = self.client.get(reverse('risk-status'))

        self.assertIn(response.status_code, [401, 403])

    def test_public_and_own_private_events_are_included_but_other_private_event_is_excluded(self):
        now = timezone.now()
        public_event = ScheduleEvent.objects.create(
            title='과목평가 안내',
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, hours=1),
            event_type='exam',
            source_type='notice',
        )
        own_event = ScheduleEvent.objects.create(
            owner=self.user,
            title='개인 일정',
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, hours=1),
            event_type='project',
            source_type='manual',
        )
        other_event = ScheduleEvent.objects.create(
            owner=self.other_user,
            title='다른 사람 개인 프로젝트 마감',
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, hours=1),
            event_type='project',
            source_type='manual',
        )

        data = self._status_payload(self.user)
        ids = {item['id'] for item in data['recommendations'] + data['upcoming_items']}

        self.assertIn(public_event.id, ids)
        self.assertIn(own_event.id, ids)
        self.assertNotIn(other_event.id, ids)

    def test_other_user_sees_public_event_but_not_user_private_event(self):
        now = timezone.now()
        public_event = ScheduleEvent.objects.create(
            title='월말평가 안내',
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, hours=1),
            event_type='exam',
            source_type='notice',
        )
        private_event = ScheduleEvent.objects.create(
            owner=self.user,
            title='개인 일정',
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, hours=1),
            event_type='project',
            source_type='manual',
        )

        data = self._status_payload(self.other_user)
        ids = {item['id'] for item in data['recommendations'] + data['upcoming_items']}

        self.assertIn(public_event.id, ids)
        self.assertNotIn(private_event.id, ids)

    def test_subject_evaluation_uses_seven_of_ten_rule(self):
        for round_number in range(1, 4):
            EvaluationResult.objects.create(
                user=self.user,
                evaluation_type=EvaluationResult.TYPE_SUBJECT,
                round_number=round_number,
                status=EvaluationResult.STATUS_PASS,
            )
        for round_number in range(4, 9):
            EvaluationResult.objects.create(
                user=self.user,
                evaluation_type=EvaluationResult.TYPE_SUBJECT,
                round_number=round_number,
                status=EvaluationResult.STATUS_FAIL,
            )

        data = self._status_payload(self.user)
        subject = next(item for item in data['evaluation_summary'] if item['evaluation_type'] == 'subject')

        self.assertEqual(subject['total_count'], 10)
        self.assertEqual(subject['target_pass_count'], 7)
        self.assertEqual(subject['pass_count'], 3)
        self.assertEqual(subject['remaining_count'], 2)
        self.assertEqual(subject['risk_level'], RiskStatus.LEVEL_DANGER)

    def test_monthly_evaluation_uses_three_of_five_rule(self):
        EvaluationResult.objects.create(
            user=self.user,
            evaluation_type=EvaluationResult.TYPE_MONTHLY,
            round_number=1,
            status=EvaluationResult.STATUS_PASS,
        )
        for round_number in [2, 3]:
            EvaluationResult.objects.create(
                user=self.user,
                evaluation_type=EvaluationResult.TYPE_MONTHLY,
                round_number=round_number,
                status=EvaluationResult.STATUS_FAIL,
            )

        data = self._status_payload(self.user)
        monthly = next(item for item in data['evaluation_summary'] if item['evaluation_type'] == 'monthly')

        self.assertEqual(monthly['total_count'], 5)
        self.assertEqual(monthly['target_pass_count'], 3)
        self.assertEqual(monthly['pass_count'], 1)
        self.assertEqual(monthly['remaining_count'], 2)
        self.assertEqual(monthly['risk_level'], RiskStatus.LEVEL_WARNING)

    def test_five_day_upcoming_priority_orders_exam_before_personal_event(self):
        now = timezone.now()
        project_event = ScheduleEvent.objects.create(
            owner=self.user,
            title='개인 일정',
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, hours=1),
            event_type='project',
            source_type='manual',
        )
        exam_event = ScheduleEvent.objects.create(
            title='월말평가',
            start_at=now + timedelta(days=3),
            end_at=now + timedelta(days=3, hours=1),
            event_type='exam',
            source_type='notice',
        )

        data = self._status_payload(self.user)
        upcoming_ids = [item['id'] for item in data['upcoming_items']]

        self.assertLess(upcoming_ids.index(exam_event.id), upcoming_ids.index(project_event.id))
        self.assertEqual(data['upcoming_items'][upcoming_ids.index(exam_event.id)]['priority'], 1)
        self.assertEqual(data['upcoming_items'][upcoming_ids.index(project_event.id)]['priority'], 2)

    def test_evaluation_result_can_be_created_and_updated_for_current_user(self):
        create_response = self.client.post(
            reverse('risk-evaluation-list'),
            data={
                'evaluation_type': 'subject',
                'round_number': 1,
                'title': '1차 과목평가',
                'status': 'scheduled',
            },
            content_type='application/json',
            **self._auth(self.user),
        )
        self.assertEqual(create_response.status_code, 201)
        evaluation_id = create_response.json()['data']['id']

        patch_response = self.client.patch(
            reverse('risk-evaluation-detail', args=[evaluation_id]),
            data={'status': 'pass'},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()['data']['status'], 'pass')
        self.assertEqual(EvaluationResult.objects.get(id=evaluation_id).user_id, self.user.id)

    def test_empty_schedule_returns_empty_lists(self):
        data = self._status_payload(self.user)

        self.assertEqual(data['recommendations'], [])
        self.assertEqual(data['upcoming_items'], [])