import requests
from django.conf import settings

from .models import AiChatReference, AiDocument, ChatMessage, ChatSession


class AiServerClient:
    def __init__(self, base_url=None):
        self.base_url = (base_url or getattr(settings, 'AI_SERVER_BASE_URL', 'http://localhost:8001')).rstrip('/')

    def chat(self, user, message, session_id=None):
        try:
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
        except requests.RequestException as exc:
            return {
                'answer': f'AI 서버에 연결할 수 없습니다. AI_SERVER_BASE_URL={self.base_url}, error={exc}',
                'references': [],
                'intent': 'error',
                'query_type': 'UNKNOWN',
                'answer_policy': 'ERROR',
                'usage': {'mode': 'ai_server_connection_error'},
            }

        if not response.ok:
            return {
                'answer': f'AI 서버 오류가 발생했습니다. status={response.status_code}, body={response.text[:500]}',
                'references': [],
                'intent': 'error',
                'query_type': 'UNKNOWN',
                'answer_policy': 'ERROR',
                'usage': {'mode': 'ai_server_error'},
            }

        try:
            payload = response.json()
        except ValueError:
            return {
                'answer': f'AI 서버가 JSON이 아닌 응답을 반환했습니다. body={response.text[:500]}',
                'references': [],
                'intent': 'error',
                'query_type': 'UNKNOWN',
                'answer_policy': 'ERROR',
                'usage': {'mode': 'ai_server_non_json'},
            }

        payload['references'] = [self._normalize_reference(ref) for ref in payload.get('references', [])]
        return payload

    def _normalize_reference(self, reference):
        metadata = reference.get('metadata') or {}
        return {
            'document_id': reference.get('ai_document_id'),
            'title': reference.get('title', ''),
            'source_type': reference.get('source_type') or metadata.get('source_type', ''),
            'score': reference.get('score', 0),
            'snippet': reference.get('snippet', ''),
            'chunk_id': reference.get('chunk_id', ''),
            'raw_data_id': reference.get('raw_data_id'),
        }


class AiChatService:
    def __init__(self, ai_server_client=None):
        self.ai_server_client = ai_server_client or AiServerClient()

    def answer(self, user, message, session_id=None):
        if getattr(settings, 'AI_SERVER_ENABLED', False):
            payload = self.ai_server_client.chat(user=user, message=message, session_id=session_id)
            self._persist_chat(user=user, message=message, payload=payload, session_id=session_id)
            return payload
        return {
            'answer': 'AI_SERVER_ENABLED=false 상태입니다. RAG 답변을 사용하려면 FastAPI AI 서버를 켜고 설정을 true로 변경하세요.',
            'references': [],
            'intent': 'disabled',
            'query_type': 'UNKNOWN',
            'answer_policy': 'DISABLED',
            'usage': {},
        }

    def _persist_chat(self, user, message, payload, session_id=None):
        try:
            session = self._get_or_create_session(user, message, session_id)
            ChatMessage.objects.create(session=session, role=ChatMessage.ROLE_USER, content=message)
            assistant_message = ChatMessage.objects.create(
                session=session,
                role=ChatMessage.ROLE_ASSISTANT,
                content=payload.get('answer', ''),
            )
            for reference in payload.get('references', []):
                document_id = reference.get('document_id')
                if not document_id:
                    continue
                document = AiDocument.objects.filter(id=document_id).first()
                if document:
                    AiChatReference.objects.create(
                        chat_message=assistant_message,
                        ai_document=document,
                        relevance_score=reference.get('score') or 0,
                    )
        except Exception:
            # Chat persistence should not block the user-facing answer.
            return

    def _get_or_create_session(self, user, message, session_id=None):
        if session_id:
            session = ChatSession.objects.filter(id=session_id, user=user).first()
            if session:
                return session
        title = (message[:40] or '새 채팅').strip()
        return ChatSession.objects.create(user=user, title=title)
