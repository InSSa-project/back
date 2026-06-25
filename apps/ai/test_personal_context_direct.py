from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from ai_server.classification.domain_intent_router import DomainIntent
from ai_server.pipelines.chat_pipeline import ChatPipeline
from ai_server.schemas.chat import ChatRequest, UserContext
from apps.risk.models import EvaluationResult
from schedules.models import ScheduleEvent


class PersonalContextDirectAnswerTests(TestCase):
    class RecordingLlm:
        def __init__(self):
            self.messages = []

        def complete(self, messages, **_kwargs):
            self.messages.append(messages)
            return {'answer': 'LLM should not be used', 'usage': {}}

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='direct-user', password='password')
        self.llm = self.RecordingLlm()
        self.pipeline = ChatPipeline()
        self.pipeline.llm = self.llm

    def ask(self, message):
        return self.pipeline.run(ChatRequest(message=message, user_context=UserContext(user_id=self.user.id)))

    def test_personal_risk_without_scores_does_not_use_lora_or_llm(self):
        response = self.ask('내 상태면 과락 위험해?')

        self.assertEqual(response.query_type, DomainIntent.PERSONAL_RISK)
        self.assertEqual(response.answer_policy, 'PERSONAL_CONTEXT_NO_DATA')
        self.assertFalse(response.usage['lora_attempted'])
        self.assertIn('성적 기록이 없어', response.answer)
        self.assertEqual(self.llm.messages, [])

    def test_personal_score_uses_db_direct_answer(self):
        EvaluationResult.objects.create(
            user=self.user,
            evaluation_type=EvaluationResult.TYPE_SUBJECT,
            round_number=1,
            subject_name='Algorithm',
            score=55,
            status=EvaluationResult.STATUS_FAIL,
        )

        response = self.ask('내 성적 보여줘')

        self.assertEqual(response.query_type, DomainIntent.PERSONAL_SCORE)
        self.assertEqual(response.answer_policy, 'PERSONAL_CONTEXT_DB_DIRECT')
        self.assertFalse(response.usage['lora_attempted'])
        self.assertIn('Algorithm', response.answer)
        self.assertEqual(self.llm.messages, [])

    def test_personal_risk_with_scores_uses_db_direct_answer(self):
        EvaluationResult.objects.create(
            user=self.user,
            evaluation_type=EvaluationResult.TYPE_SUBJECT,
            round_number=1,
            subject_name='Algorithm',
            score=55,
            status=EvaluationResult.STATUS_FAIL,
        )

        response = self.ask('내 상태면 과락 위험해?')

        self.assertEqual(response.query_type, DomainIntent.PERSONAL_RISK)
        self.assertEqual(response.answer_policy, 'PERSONAL_CONTEXT_DB_DIRECT')
        self.assertFalse(response.usage['lora_attempted'])
        self.assertIn('과락/수료 위험도', response.answer)
        self.assertEqual(self.llm.messages, [])

    def test_recommendation_question_uses_db_direct_formatter(self):
        now = timezone.now()
        ScheduleEvent.objects.create(
            title='월말평가6',
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, hours=1),
            event_type='exam',
            source_type='notice',
        )
        ScheduleEvent.objects.create(
            title='관통PJT 경진대회',
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, hours=1),
            event_type='project',
            source_type='notice',
        )

        response = self.ask('다음 주 월말평가 있는데 이번 주 일정이랑 같이 고려해서 뭘 먼저 해야 해?')

        self.assertEqual(response.query_type, DomainIntent.RECOMMENDED_SCHEDULE)
        self.assertEqual(response.answer_policy, 'PERSONAL_CONTEXT_DB_DIRECT')
        self.assertFalse(response.usage['lora_attempted'])
        self.assertIn('월말평가6', response.answer)
        self.assertIn('먼저 할 일', response.answer)
        self.assertEqual(self.llm.messages, [])
