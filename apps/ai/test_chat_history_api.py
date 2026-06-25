from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from ai_server.memory.conversation_memory import ConversationMemory

from .models import ChatMessage, ChatSession
from .services import FastAPIAIClient


class AiChatHistoryApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='chat-history-user',
            email='chat-history@example.com',
            password='password',
        )
        self.other_user = get_user_model().objects.create_user(
            username='other-chat-user',
            email='other-chat@example.com',
            password='password',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_session_list_returns_only_current_user_sessions(self):
        session = ChatSession.objects.create(user=self.user, title='My AI chat')
        ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_USER, content='I am weak at algorithms.')
        ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_ASSISTANT, content='Let us plan practice.')
        other_session = ChatSession.objects.create(user=self.other_user, title='Other AI chat')
        ChatMessage.objects.create(session=other_session, role=ChatMessage.ROLE_USER, content='Hidden')

        response = self.client.get(reverse('ai-chat-sessions'))

        self.assertEqual(response.status_code, 200)
        items = response.json()['data']['items']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['id'], session.id)
        self.assertEqual(items[0]['message_count'], 2)
        self.assertIn('Let us plan practice.', items[0]['latest_message'])

    def test_session_messages_are_user_scoped(self):
        session = ChatSession.objects.create(user=self.user, title='My AI chat')
        ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_USER, content='Question')
        ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_ASSISTANT, content='Answer')
        other_session = ChatSession.objects.create(user=self.other_user, title='Other AI chat')

        own_response = self.client.get(reverse('ai-chat-session-messages', args=[session.id]))
        other_response = self.client.get(reverse('ai-chat-session-messages', args=[other_session.id]))

        self.assertEqual(own_response.status_code, 200)
        self.assertEqual(len(own_response.json()['data']['items']), 2)
        self.assertIsNone(other_response.json()['data']['session'])
        self.assertEqual(other_response.json()['data']['items'], [])

    def test_delete_session_removes_only_current_user_session(self):
        session = ChatSession.objects.create(user=self.user, title='Delete me')
        ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_USER, content='Question')
        other_session = ChatSession.objects.create(user=self.other_user, title='Keep me')

        response = self.client.delete(reverse('ai-chat-session-messages', args=[session.id]))
        other_response = self.client.delete(reverse('ai-chat-session-messages', args=[other_session.id]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['data']['deleted'])
        self.assertFalse(ChatSession.objects.filter(id=session.id).exists())
        self.assertFalse(ChatMessage.objects.filter(session_id=session.id).exists())
        self.assertFalse(other_response.json()['data']['deleted'])
        self.assertTrue(ChatSession.objects.filter(id=other_session.id).exists())


class ConversationMemoryTests(TestCase):
    def test_build_context_uses_recent_conversation_payload(self):
        context = ConversationMemory().build_context(
            session_id=10,
            conversation_context=[
                {'role': 'user', 'content': 'I failed REST API.'},
                {'role': 'assistant', 'content': 'Check the official rule and prepare recovery.'},
            ],
        )

        self.assertIn('User: I failed REST API.', context)
        self.assertIn('Assistant: Check the official rule', context)


class FastAPIAIClientConversationContextTests(TestCase):
    def test_conversation_context_is_limited_and_user_scoped(self):
        user = get_user_model().objects.create_user(
            username='context-user',
            email='context@example.com',
            password='password',
        )
        other_user = get_user_model().objects.create_user(
            username='context-other-user',
            email='context-other@example.com',
            password='password',
        )
        session = ChatSession.objects.create(user=user, title='Session')
        other_session = ChatSession.objects.create(user=other_user, title='Other')
        for index in range(10):
            ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_USER, content=f'message {index}')
        ChatMessage.objects.create(session=other_session, role=ChatMessage.ROLE_USER, content='secret')

        context = FastAPIAIClient()._conversation_context(user, session.id)
        other_context = FastAPIAIClient()._conversation_context(user, other_session.id)

        self.assertEqual(len(context), 8)
        self.assertEqual(context[0]['content'], 'message 2')
        self.assertEqual(context[-1]['content'], 'message 9')
        self.assertEqual(other_context, [])
