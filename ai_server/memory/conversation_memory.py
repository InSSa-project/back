class ConversationMemory:
    def build_context(self, session_id: int | None) -> str:
        if not session_id:
            return ''
        return 'Recent conversation summary will be loaded from Django chat_messages.'
