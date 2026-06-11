from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.risk.models import EvaluationResult
from apps.users.models import UserProfile
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

    def test_score_only_sets_status_from_sixty_point_rule(self):
        fail_response = self.client.post(
            reverse('risk-evaluation-list'),
            data={
                'evaluation_type': 'subject',
                'round_number': 1,
                'subject_name': 'algorithm',
                'score': 55,
            },
            content_type='application/json',
            **self._auth(self.user),
        )
        pass_response = self.client.post(
            reverse('risk-evaluation-list'),
            data={
                'evaluation_type': 'monthly',
                'round_number': 1,
                'subject_name': 'monthly',
                'score': 60,
            },
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(fail_response.status_code, 201)
        self.assertEqual(fail_response.json()['data']['status'], 'fail')
        self.assertEqual(pass_response.status_code, 201)
        self.assertEqual(pass_response.json()['data']['status'], 'pass')

    def test_risk_status_includes_track_evaluation_policy(self):
        UserProfile.objects.create(user=self.user, track=UserProfile.TRACK_PYTHON)

        data = self._status_payload(self.user)

        self.assertEqual(data['evaluation_policy_type'], 'general')
        self.assertEqual(data['evaluation_summary'][0]['evaluation_policy_type'], 'general')
        self.assertIn('evaluation_policy_label', data)

    def test_meister_profile_uses_meister_policy_type(self):
        UserProfile.objects.create(user=self.user, track='meister')

        data = self._status_payload(self.user)

        self.assertEqual(data['evaluation_policy_type'], 'meister')
        self.assertEqual(data['evaluation_summary'][0]['evaluation_policy_type'], 'meister')

    def test_score_overrides_user_selected_pass_status(self):
        response = self.client.post(
            reverse('risk-evaluation-list'),
            data={
                'evaluation_type': 'subject',
                'round_number': 2,
                'subject_name': 'algorithm',
                'score': 55,
                'status': 'pass',
            },
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['data']['status'], 'fail')

    def test_patch_status_cannot_override_existing_low_score(self):
        evaluation = EvaluationResult.objects.create(
            user=self.user,
            evaluation_type=EvaluationResult.TYPE_SUBJECT,
            round_number=3,
            subject_name='algorithm',
            score=55,
            status=EvaluationResult.STATUS_FAIL,
        )

        response = self.client.patch(
            reverse('risk-evaluation-detail', args=[evaluation.id]),
            data={'status': 'pass'},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['status'], 'fail')
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, EvaluationResult.STATUS_FAIL)

    def test_upsert_status_cannot_override_existing_low_score(self):
        EvaluationResult.objects.create(
            user=self.user,
            evaluation_type=EvaluationResult.TYPE_SUBJECT,
            round_number=4,
            subject_name='algorithm',
            score=55,
            status=EvaluationResult.STATUS_FAIL,
        )

        response = self.client.post(
            reverse('risk-evaluation-list'),
            data={
                'evaluation_type': 'subject',
                'round_number': 4,
                'status': 'pass',
            },
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['data']['status'], 'fail')

    def test_status_only_is_allowed(self):
        response = self.client.post(
            reverse('risk-evaluation-list'),
            data={
                'evaluation_type': 'subject',
                'round_number': 5,
                'status': 'retake',
            },
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.json()['data']['score'])
        self.assertEqual(response.json()['data']['status'], 'retake')

    def test_score_and_status_both_missing_is_rejected(self):
        response = self.client.post(
            reverse('risk-evaluation-list'),
            data={
                'evaluation_type': 'subject',
                'round_number': 6,
                'subject_name': 'algorithm',
            },
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 400)

    def test_user_cannot_update_or_delete_other_users_score(self):
        evaluation = EvaluationResult.objects.create(
            user=self.other_user,
            evaluation_type=EvaluationResult.TYPE_SUBJECT,
            round_number=1,
            subject_name='algorithm',
            score=30,
            status=EvaluationResult.STATUS_FAIL,
        )

        patch_response = self.client.patch(
            reverse('risk-evaluation-detail', args=[evaluation.id]),
            data={'score': 100, 'status': 'pass'},
            content_type='application/json',
            **self._auth(self.user),
        )
        delete_response = self.client.delete(
            reverse('risk-evaluation-detail', args=[evaluation.id]),
            **self._auth(self.user),
        )

        self.assertEqual(patch_response.status_code, 404)
        self.assertEqual(delete_response.status_code, 404)

    def test_holiday_is_excluded_from_recommendations_and_upcoming_items(self):
        now = timezone.now()
        holiday = ScheduleEvent.objects.create(
            title='Holiday',
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, hours=1),
            event_type='holiday',
            source_type='notice',
        )
        ScheduleEvent.objects.create(
            owner=self.user,
            title='Personal project',
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, hours=1),
            event_type='project',
            source_type='manual',
        )

        data = self._status_payload(self.user)
        recommended_ids = [card['schedule_event_id'] for card in data['recommended_schedules']]
        upcoming_ids = [item['id'] for item in data['upcoming_items']]

        self.assertNotIn(holiday.id, recommended_ids)
        self.assertNotIn(holiday.id, upcoming_ids)
    def test_important_items_use_requested_priority_order_and_exclude_routine_events(self):
        now = timezone.now()
        important = ScheduleEvent.objects.create(
            owner=self.user,
            title='Marked important',
            start_at=now + timedelta(days=4),
            end_at=now + timedelta(days=4, hours=1),
            event_type='personal',
            source_type='manual',
            metadata_json={'is_important': True},
        )
        exam = ScheduleEvent.objects.create(
            title='과목평가',
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, hours=1),
            event_type='exam',
            source_type='notice',
        )
        personal = ScheduleEvent.objects.create(
            owner=self.user,
            title='Personal plan',
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, hours=1),
            event_type='personal',
            source_type='manual',
        )
        public = ScheduleEvent.objects.create(
            title='Public notice',
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, hours=1),
            event_type='notice',
            source_type='notice',
        )
        online_week = ScheduleEvent.objects.create(
            title='온라인 위크',
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, hours=1),
            event_type='study',
            source_type='notice',
        )

        data = self._status_payload(self.user)
        upcoming_ids = [item['id'] for item in data['upcoming_items']]
        priorities = {item['id']: item['priority'] for item in data['upcoming_items']}

        self.assertEqual(upcoming_ids[:3], [important.id, exam.id, personal.id])
        self.assertEqual(priorities[important.id], 1)
        self.assertEqual(priorities[exam.id], 2)
        self.assertEqual(priorities[personal.id], 3)
        self.assertNotIn(public.id, upcoming_ids)
        self.assertNotIn(online_week.id, upcoming_ids)

    def test_important_schedule_gets_evaluation_level_recommendation_weight(self):
        now = timezone.now()
        important = ScheduleEvent.objects.create(
            owner=self.user,
            title='Marked important',
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, hours=1),
            event_type='personal',
            source_type='manual',
            metadata_json={'is_important': True},
        )

        data = self._status_payload(self.user)
        card = next(item for item in data['recommended_schedules'] if item['schedule_event_id'] == important.id)

        self.assertGreaterEqual(card['recommendation_score'], 100)
        self.assertIn('중요 일정', card['details']['summary'])

    def test_today_ongoing_important_event_appears_before_later_important_event(self):
        now = timezone.now()
        today_important = ScheduleEvent.objects.create(
            owner=self.user,
            title='Today important',
            start_at=now - timedelta(hours=2),
            end_at=now + timedelta(hours=2),
            event_type='personal',
            source_type='manual',
            metadata_json={'is_important': True},
        )
        later_important = ScheduleEvent.objects.create(
            owner=self.user,
            title='Later important',
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, hours=1),
            event_type='personal',
            source_type='manual',
            metadata_json={'is_important': True},
        )

        data = self._status_payload(self.user)
        upcoming_ids = [item['id'] for item in data['upcoming_items']]

        self.assertIn(today_important.id, upcoming_ids)
        self.assertIn(later_important.id, upcoming_ids)
        self.assertLess(upcoming_ids.index(today_important.id), upcoming_ids.index(later_important.id))

    def test_deleted_schedule_event_is_removed_from_next_risk_dashboard_response(self):
        now = timezone.now()
        event = ScheduleEvent.objects.create(
            owner=self.user,
            title='Delete me important',
            start_at=now + timedelta(hours=1),
            end_at=now + timedelta(hours=2),
            event_type='personal',
            source_type='manual',
            metadata_json={'is_important': True},
        )

        first_data = self._status_payload(self.user)
        self.assertIn(event.id, [item['id'] for item in first_data['upcoming_items']])

        event.delete()
        second_data = self._status_payload(self.user)
        self.assertNotIn(event.id, [item['id'] for item in second_data['upcoming_items']])
        self.assertNotIn(event.id, [item['schedule_event_id'] for item in second_data['recommended_schedules']])
    def test_hidden_meaningless_schedule_is_excluded_from_risk_dashboard(self):
        now = timezone.now()
        hidden = ScheduleEvent.objects.create(
            owner=self.user,
            title='DB',
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, hours=1),
            event_type='personal',
            source_type='manual',
            metadata_json={'is_important': True},
        )
        visible = ScheduleEvent.objects.create(
            owner=self.user,
            title='Visible important schedule',
            start_at=now + timedelta(hours=1),
            end_at=now + timedelta(hours=2),
            event_type='personal',
            source_type='manual',
            metadata_json={'is_important': True},
        )

        data = self._status_payload(self.user)
        upcoming_ids = [item['id'] for item in data['upcoming_items']]
        recommended_ids = [item['schedule_event_id'] for item in data['recommended_schedules']]

        self.assertIn(visible.id, upcoming_ids)
        self.assertNotIn(hidden.id, upcoming_ids)
        self.assertNotIn(hidden.id, recommended_ids)
