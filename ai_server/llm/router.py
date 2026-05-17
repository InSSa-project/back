from ai_server.llm.openai_provider import OpenAiProvider
from ai_server.llm.gemini_provider import GeminiProvider


class LlmRouter:
    def get_provider(self, provider_name: str = 'openai'):
        if provider_name == 'openai':
            return OpenAiProvider()
        if provider_name == 'gemini':
            return GeminiProvider()
        raise ValueError(f'Unsupported LLM provider: {provider_name}')
