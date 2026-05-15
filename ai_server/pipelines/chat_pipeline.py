import json

from ai_server.classification.query_classifier import QueryClassifier
from ai_server.core.config import get_settings
from ai_server.llm.router import LlmRouter
from ai_server.memory.conversation_memory import ConversationMemory
from ai_server.optimization.token_budget import TokenBudgetManager
from ai_server.prompts.builder import PromptBuilder
from ai_server.references.tracker import ReferenceTracker
from ai_server.rerankers.simple_reranker import SimpleReranker
from ai_server.retrievers.hybrid_retriever import HybridRetriever
from ai_server.schemas.chat import ChatRequest, ChatResponse


class ChatPipeline:
    def __init__(self):
        self.settings = get_settings()
        self.classifier = QueryClassifier()
        self.retriever = HybridRetriever()
        self.reranker = SimpleReranker()
        self.memory = ConversationMemory()
        self.prompt_builder = PromptBuilder()
        self.llm = LlmRouter().get_provider(self.settings.llm_provider)
        self.reference_tracker = ReferenceTracker()
        self.token_budget = TokenBudgetManager()

    def run(self, request: ChatRequest) -> ChatResponse:
        intent = self.classifier.classify(request.message)
        filters = self._build_filters(request, intent)
        retrieved = self.retriever.retrieve(request.message, filters=filters)
        reranked = self.reranker.rerank(request.message, retrieved)
        messages = self._build_messages(request, intent, reranked)
        llm_response = self.llm.complete(messages)
        return ChatResponse(
            answer=llm_response['answer'],
            intent=intent,
            references=self.reference_tracker.from_chunks(reranked),
            usage=llm_response.get('usage', {}),
        )

    def stream(self, request: ChatRequest):
        intent = self.classifier.classify(request.message)
        retrieved = self.reranker.rerank(request.message, self.retriever.retrieve(request.message, self._build_filters(request, intent)))
        messages = self._build_messages(request, intent, retrieved)
        for delta in self.llm.stream(messages):
            yield f'data: {json.dumps({"type": "delta", "content": delta}, ensure_ascii=False)}\n\n'
        yield 'data: {"type": "done"}\n\n'

    def _build_messages(self, request: ChatRequest, intent: str, chunks) -> list[dict]:
        retrieved_context = self.token_budget.trim_context('\n\n'.join(chunk.content for chunk in chunks))
        user_context = request.user_context.model_dump_json()
        memory_context = self.memory.build_context(request.session_id)
        few_shot_examples = self._select_few_shot(intent)
        return self.prompt_builder.build_messages(
            question=request.message,
            intent=intent,
            retrieved_context=retrieved_context,
            user_context=user_context,
            memory_context=memory_context,
            few_shot_examples=few_shot_examples,
        )

    def _build_filters(self, request: ChatRequest, intent: str) -> dict:
        return {
            'campus': request.user_context.campus,
            'generation': request.user_context.generation,
            'intent': intent,
        }

    def _select_few_shot(self, intent: str) -> str:
        if intent == 'exam':
            return 'Use exam_examples.yaml.'
        if intent == 'mentoring':
            return 'Use mentoring_examples.yaml.'
        return 'Use concise SSAFY senior style examples.'
