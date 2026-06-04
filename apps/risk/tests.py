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

    def test_subject_evaluation_uses_eight_of_thirteen_rule(self):
        for round_number in range(1, 4):
            EvaluationResult.objects.create(
                user=self.user,
                evaluation_type=EvaluationResult.TYPE_SUBJECT,
                round_number=round_number,
                status=EvaluationResult.STATUS_PASS,
            )
        for round_number in range(4, 10):
            EvaluationResult.objects.create(
                user=self.user,
                evaluation_type=EvaluationResult.TYPE_SUBJECT,
                round_number=round_number,
                status=EvaluationResult.STATUS_FAIL,
            )

        data = self._status_payload(self.user)
        subject = next(item for item in data['evaluation_summary'] if item['evaluation_type'] == 'subject')

        self.assertEqual(subject['total_count'], 13)
        self.assertEqual(subject['target_pass_count'], 8)
        self.assertEqual(subject['pass_count'], 3)
        self.assertEqual(subject['remaining_count'], 4)
        self.assertEqual(subject['risk_level'], RiskStatus.LEVEL_DANGER)

    def test_monthly_evaluation_uses_four_of_seven_rule(self):
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

        self.assertEqual(monthly['total_count'], 7)
        self.assertEqual(monthly['target_pass_count'], 4)
        self.assertEqual(monthly['pass_count'], 1)
        self.assertEqual(monthly['remaining_count'], 4)
        self.assertEqual(monthly['risk_level'], RiskStatus.LEVEL_WARNING)

    def test_three_fails_with_no_passes_and_many_remaining_is_warning(self):
        for round_number in range(1, 4):
            EvaluationResult.objects.create(
                user=self.user,
                evaluation_type=EvaluationResult.TYPE_SUBJECT,
                round_number=round_number,
                status=EvaluationResult.STATUS_FAIL,
            )

        data = self._status_payload(self.user)
        subject = next(item for item in data['evaluation_summary'] if item['evaluation_type'] == 'subject')

        self.assertEqual(subject['pass_count'], 0)
        self.assertEqual(subject['fail_count'], 3)
        self.assertEqual(subject['remaining_count'], 10)
        self.assertEqual(subject['needed_pass_count'], 8)
        self.assertEqual(subject['allowed_fail_count'], 5)
        self.assertEqual(subject['risk_level'], RiskStatus.LEVEL_WARNING)
        self.assertEqual(data['status']['risk_level'], RiskStatus.LEVEL_WARNING)

    def test_no_results_default_to_normal_with_explanation(self):
        data = self._status_payload(self.user)

        self.assertEqual(data['status']['risk_level'], RiskStatus.LEVEL_CAUTION)
        self.assertEqual(data['status_details']['level_label'], '보통')
        self.assertIn('기본 보통 단계', data['status_details']['summary'])

    def test_half_of_remaining_passes_reaching_cutoff_is_normal(self):
        for round_number in range(1, 5):
            EvaluationResult.objects.create(
                user=self.user,
                evaluation_type=EvaluationResult.TYPE_SUBJECT,
                round_number=round_number,
                status=EvaluationResult.STATUS_PASS,
            )
        EvaluationResult.objects.create(
            user=self.user,
            evaluation_type=EvaluationResult.TYPE_SUBJECT,
            round_number=5,
            status=EvaluationResult.STATUS_FAIL,
        )

        data = self._status_payload(self.user)
        subject = next(item for item in data['evaluation_summary'] if item['evaluation_type'] == 'subject')

        self.assertEqual(subject['projected_half_pass_count'], 8)
        self.assertEqual(subject['risk_level'], RiskStatus.LEVEL_CAUTION)
        self.assertIn('절반 합격하면 수료 커트라인에 도달', subject['message'])

    def test_no_remaining_fail_allowance_is_warning_with_explanation(self):
        for round_number in range(1, 8):
            EvaluationResult.objects.create(
                user=self.user,
                evaluation_type=EvaluationResult.TYPE_SUBJECT,
                round_number=round_number,
                status=EvaluationResult.STATUS_PASS,
            )
        for round_number in range(8, 13):
            EvaluationResult.objects.create(
                user=self.user,
                evaluation_type=EvaluationResult.TYPE_SUBJECT,
                round_number=round_number,
                status=EvaluationResult.STATUS_FAIL,
            )

        data = self._status_payload(self.user)
        subject = next(item for item in data['evaluation_summary'] if item['evaluation_type'] == 'subject')

        self.assertEqual(subject['remaining_fail_allowance'], 0)
        self.assertEqual(subject['risk_level'], RiskStatus.LEVEL_WARNING)
        self.assertIn('한 번 더 과락하면', subject['message'])

    def test_recommendation_explains_cutoff_and_due_date(self):
        for round_number in range(1, 8):
            EvaluationResult.objects.create(
                user=self.user,
                evaluation_type=EvaluationResult.TYPE_SUBJECT,
                round_number=round_number,
                status=EvaluationResult.STATUS_PASS,
            )
        for round_number in range(8, 13):
            EvaluationResult.objects.create(
                user=self.user,
                evaluation_type=EvaluationResult.TYPE_SUBJECT,
                round_number=round_number,
                status=EvaluationResult.STATUS_FAIL,
            )
        event = ScheduleEvent.objects.create(
            title='알고리즘 과목평가',
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
            event_type='exam',
            source_type='notice',
        )

        data = self._status_payload(self.user)
        card = next(item for item in data['recommended_schedules'] if item['schedule_event_id'] == event.id)

        self.assertIn('수료 커트라인 여유가 없고 시험이', card['details']['summary'])
        self.assertTrue(any(item['label'] == '추천 점수 계산' for item in card['details']['evidence_items']))

    def test_target_met_with_three_fails_and_one_remaining_is_safe(self):
        for round_number in range(1, 10):
            EvaluationResult.objects.create(
                user=self.user,
                evaluation_type=EvaluationResult.TYPE_SUBJECT,
                round_number=round_number,
                status=EvaluationResult.STATUS_PASS,
            )
        for round_number in range(10, 13):
            EvaluationResult.objects.create(
                user=self.user,
                evaluation_type=EvaluationResult.TYPE_SUBJECT,
                round_number=round_number,
                status=EvaluationResult.STATUS_FAIL,
            )

        data = self._status_payload(self.user)
        subject = next(item for item in data['evaluation_summary'] if item['evaluation_type'] == 'subject')

        self.assertEqual(subject['pass_count'], 9)
        self.assertEqual(subject['fail_count'], 3)
        self.assertEqual(subject['remaining_count'], 1)
        self.assertEqual(subject['risk_level'], RiskStatus.LEVEL_SAFE)

    def test_schedule_risk_does_not_change_current_evaluation_status(self):
        now = timezone.now()
        ScheduleEvent.objects.create(
            title='Urgent exam',
            start_at=now + timedelta(hours=1),
            end_at=now + timedelta(hours=2),
            event_type='exam',
            source_type='notice',
        )

        data = self._status_payload(self.user)

        self.assertEqual(data['recommendations'][0]['risk_level'], RiskStatus.LEVEL_DANGER)
        self.assertEqual(data['status']['risk_level'], RiskStatus.LEVEL_CAUTION)
    def test_score_weighted_recommendation_cards_prioritize_weak_monthly_subject(self):
        now = timezone.now()
        django_event = ScheduleEvent.objects.create(
            title='Django 과목평가',
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, hours=1),
            event_type='exam',
            source_type='notice',
            metadata_json={'subject_name': 'Django'},
        )
        rest_subject_event = ScheduleEvent.objects.create(
            title='REST API 과목평가',
            start_at=now + timedelta(days=3),
            end_at=now + timedelta(days=3, hours=1),
            event_type='exam',
            source_type='notice',
            metadata_json={'subject_name': 'REST API'},
        )
        rest_monthly_event = ScheduleEvent.objects.create(
            title='REST API 월말평가',
            start_at=now + timedelta(days=7),
            end_at=now + timedelta(days=7, hours=1),
            event_type='exam',
            source_type='notice',
            metadata_json={'subject_name': 'REST API'},
        )
        for round_number in range(1, 10):
            EvaluationResult.objects.create(
                user=self.user,
                evaluation_type=EvaluationResult.TYPE_SUBJECT,
                round_number=round_number,
                subject_name='Django',
                score=80,
                status=EvaluationResult.STATUS_PASS,
            )
        for round_number in [1, 2]:
            EvaluationResult.objects.create(
                user=self.user,
                evaluation_type=EvaluationResult.TYPE_MONTHLY,
                round_number=round_number,
                subject_name='기초',
                score=80,
                status=EvaluationResult.STATUS_PASS,
            )
        EvaluationResult.objects.create(
            user=self.user,
            evaluation_type=EvaluationResult.TYPE_MONTHLY,
            round_number=3,
            subject_name='REST API',
            score=40,
            status=EvaluationResult.STATUS_FAIL,
        )

        data = self._status_payload(self.user)
        cards = data['recommended_schedules']
        card_ids = [card['schedule_event_id'] for card in cards]

        self.assertEqual(card_ids[0], rest_monthly_event.id)
        self.assertLess(card_ids.index(rest_monthly_event.id), card_ids.index(rest_subject_event.id))
        self.assertLess(card_ids.index(rest_subject_event.id), card_ids.index(django_event.id))
        first = cards[0]
        self.assertEqual(first['card_title'], 'REST API 월말평가')
        self.assertEqual(first['card_subtitle'], '')
        self.assertEqual(first['priority'], 'HIGH')
        self.assertEqual(first['subject_match_confidence'], 'HIGH')
        self.assertTrue(first['is_expandable'])
        self.assertIn('MONTHLY_PASS_COUNT_BELOW_TARGET', first['details']['reason_codes'])
        self.assertIn('WEAK_SUBJECT_RECENT_SCORE_LOW', first['details']['reason_codes'])
        self.assertTrue(first['details']['evidence_items'])
        self.assertTrue(first['details']['recommended_actions'])

    def test_other_users_scores_do_not_affect_recommendation_cards(self):
        now = timezone.now()
        django_event = ScheduleEvent.objects.create(
            title='Django 과목평가',
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, hours=1),
            event_type='exam',
            source_type='notice',
            metadata_json={'subject_name': 'Django'},
        )
        rest_event = ScheduleEvent.objects.create(
            title='REST API 과목평가',
            start_at=now + timedelta(days=3),
            end_at=now + timedelta(days=3, hours=1),
            event_type='exam',
            source_type='notice',
            metadata_json={'subject_name': 'REST API'},
        )
        EvaluationResult.objects.create(
            user=self.other_user,
            evaluation_type=EvaluationResult.TYPE_SUBJECT,
            round_number=1,
            subject_name='REST API',
            score=10,
            status=EvaluationResult.STATUS_FAIL,
        )

        data = self._status_payload(self.user)
        cards = data['recommended_schedules']

        self.assertEqual(cards[0]['schedule_event_id'], django_event.id)
        rest_card = next(card for card in cards if card['schedule_event_id'] == rest_event.id)
        self.assertNotIn('WEAK_SUBJECT_RECENT_SCORE_LOW', rest_card['details']['reason_codes'])

    def test_completed_schedule_is_excluded_from_recommendation_cards(self):
        now = timezone.now()
        completed = ScheduleEvent.objects.create(
            owner=self.user,
            title='완료한 프로젝트 마감',
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, hours=1),
            event_type='project',
            source_type='manual',
            metadata_json={'completed': True},
        )

        data = self._status_payload(self.user)
        card_ids = [card['schedule_event_id'] for card in data['recommended_schedules']]

        self.assertNotIn(completed.id, card_ids)
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
    def test_score_only_sets_status_from_sixty_point_rule(self):
        fail_response = self.client.post(
            reverse('risk-evaluation-list'),
            data={
                'evaluation_type': 'subject',
                'round_number': 11,
                'subject_name': '알고리즘',
                'score': 55,
                'max_score': 100,
            },
            content_type='application/json',
            **self._auth(self.user),
        )
        pass_response = self.client.post(
            reverse('risk-evaluation-list'),
            data={
                'evaluation_type': 'monthly',
                'round_number': 6,
                'subject_name': '웹',
                'score': 60,
                'max_score': 100,
            },
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(fail_response.status_code, 201)
        self.assertEqual(fail_response.json()['data']['status'], 'fail')
        self.assertEqual(fail_response.json()['data']['subject_name'], '알고리즘')
        self.assertEqual(fail_response.json()['data']['score'], '55.00')
        self.assertEqual(pass_response.status_code, 201)
        self.assertEqual(pass_response.json()['data']['status'], 'pass')

    def test_status_only_is_allowed(self):
        response = self.client.post(
            reverse('risk-evaluation-list'),
            data={
                'evaluation_type': 'subject',
                'round_number': 12,
                'status': 'retake',
                'note': '재시험 예정',
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
                'round_number': 13,
                'subject_name': '알고리즘',
            },
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 400)

    def test_score_validation_rejects_invalid_ranges(self):
        invalid_payloads = [
            {'score': -1},
            {'score': 101},
        ]
        for index, score_fields in enumerate(invalid_payloads, start=1):
            with self.subTest(score_fields=score_fields):
                response = self.client.post(
                    reverse('risk-evaluation-list'),
                    data={
                        'evaluation_type': 'monthly',
                        'round_number': index,
                        **score_fields,
                    },
                    content_type='application/json',
                    **self._auth(self.user),
                )
                self.assertEqual(response.status_code, 400)

    def test_client_cannot_override_fixed_max_score(self):
        response = self.client.post(
            reverse('risk-evaluation-list'),
            data={
                'evaluation_type': 'subject',
                'round_number': 1,
                'score': 50,
                'max_score': 50,
            },
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['data']['max_score'], '100.00')
        self.assertEqual(EvaluationResult.objects.get(id=response.json()['data']['id']).max_score, 100)

    def test_user_cannot_update_or_delete_other_users_score(self):
        evaluation = EvaluationResult.objects.create(
            user=self.other_user,
            evaluation_type=EvaluationResult.TYPE_SUBJECT,
            round_number=1,
            subject_name='알고리즘',
            score=30,
            max_score=100,
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
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.score, 30)



