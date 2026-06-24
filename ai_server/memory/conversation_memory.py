class ConversationMemory:
    def build_context(self, session_id: int | None, conversation_context: list[dict] | None = None) -> str:
        if conversation_context:
            lines = []
            for message in conversation_context[-8:]:
                role = str(message.get('role') or '').upper()
                if role == 'USER':
                    label = 'User'
                elif role == 'ASSISTANT':
                    label = 'Assistant'
                else:
                    label = role.title() or 'Message'
                content = ' '.join(str(message.get('content') or '').split())[:700]
                if content:
                    lines.append(f'{label}: {content}')
            return '\n'.join(lines)
        if not session_id:
            return ''
        return self._build_context_from_django(session_id)

    def _build_context_from_django(self, session_id: int) -> str:
        try:
            self._ensure_django_ready()
            from apps.ai.models import ChatMessage

            messages = list(
                ChatMessage.objects.filter(session_id=session_id)
                .order_by('-created_at', '-id')
                .values('role', 'content')[:8]
            )
        except Exception:
            return ''
        lines = []
        for message in reversed(messages):
            role = str(message.get('role') or '').upper()
            label = 'User' if role == 'USER' else 'Assistant' if role == 'ASSISTANT' else role.title()
            content = ' '.join(str(message.get('content') or '').split())[:700]
            if content:
                lines.append(f'{label}: {content}')
        return '\n'.join(lines)

    def last_schedule_state(self, session_id: int | None) -> dict:
        if not session_id:
            return {}
        try:
            self._ensure_django_ready()
            from apps.ai.models import ChatMessage

            message = (
                ChatMessage.objects.filter(session_id=session_id, role=ChatMessage.ROLE_ASSISTANT)
                .order_by('-created_at', '-id')
                .first()
            )
            if not message:
                return {}
            usage = message.usage_json or {}
            results = usage.get('last_schedule_results') or []
            if not results:
                return {}
            return {
                'results': results,
                'query': usage.get('last_schedule_query') or {},
                'selected_index': int(usage.get('selected_schedule_index') or 0),
            }
        except Exception:
            return {}

    def _ensure_django_ready(self):
        import os

        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crawler_service.settings')
        import django
        from django.apps import apps

        if not apps.ready:
            django.setup()
