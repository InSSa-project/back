import requests
from django.conf import settings


class AiServerClient:
    def __init__(self, base_url=None):
        self.base_url = (base_url or getattr(settings, 'AI_SERVER_BASE_URL', 'http://localhost:8001')).rstrip('/')

    def chat(self, user, message, session_id=None):
        response = requests.post(
            f'{self.base_url}/v1/chat',
            json={
                'session_id': session_id,
                'message': message,
                'user_context': {
                    'user_id': user.id,
                    'campus': getattr(user, 'campus', ''),
                    'generation': getattr(user, 'generation', ''),
                    'track': getattr(user, 'track', ''),
                    'risk_level': getattr(getattr(user, 'risk_status', None), 'risk_level', ''),
                },
                'stream': False,
            },
            timeout=15,
        )
        if not response.ok:
            return {
                'answer': f'AI 서버 오류가 발생했습니다. status={response.status_code}, body={response.text[:500]}',
                'references': [],
                'intent': 'error',
                'usage': {'mode': 'ai_server_error'},
            }
        return response.json()


class AiChatService:
    def __init__(self, ai_server_client=None):
        self.ai_server_client = ai_server_client or AiServerClient()

    def answer(self, user, message, session_id=None):
        if getattr(settings, 'AI_SERVER_ENABLED', False):
            return self.ai_server_client.chat(user=user, message=message, session_id=session_id)
        return {
            'answer': 'AI chat service is ready. RAG pipeline will be connected here.',
            'references': [],
            'intent': 'general',
            'usage': {},
        }
