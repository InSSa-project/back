class ConversationMemory:
    def build_context(self, session_id: int | None) -> str:
        if not session_id:
            return ''
        return 'Recent conversation summary will be loaded from Django chat_messages.'

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
