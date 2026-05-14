from rag.routers.intent import IntentRouter


class LlmRouter:
    def __init__(self, intent_router: IntentRouter | None = None):
        self.intent_router = intent_router or IntentRouter()

    def select_chain(self, question: str) -> str:
        return self.intent_router.route(question)
