from ai_server.llm.gemini_provider import GeminiProvider
from ai_server.llm.http_model_client import HttpModelClient
from ai_server.llm.openai_provider import OpenAiProvider


class UnavailableLLMClient:
    def __init__(self, provider_name: str):
        self.provider_name = provider_name

    def complete(self, messages: list[dict], **kwargs) -> dict:
        return {
            'answer': '설정된 AI 제공자를 사용할 수 없어요. 관리자에게 문의해 주세요.',
            'usage': {'mode': 'unsupported_llm_provider', 'provider': self.provider_name},
        }

    def stream(self, messages: list[dict], **kwargs):
        yield self.complete(messages, **kwargs)['answer']


class LLMClientFactory:
    def create(self, provider_name: str = 'openai'):
        normalized = (provider_name or 'openai').strip().lower()
        if normalized == 'openai':
            return OpenAiProvider()
        if normalized == 'gemini':
            return GeminiProvider()
        if normalized in {'http', 'fastapi', 'local', 'fine_tuned'}:
            return HttpModelClient()
        return UnavailableLLMClient(normalized)


class LlmRouter:
    """Backward-compatible facade. New code should treat the result as an LLMClient."""

    def get_provider(self, provider_name: str = 'openai'):
        return LLMClientFactory().create(provider_name)
