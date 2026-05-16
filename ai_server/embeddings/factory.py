from ai_server.core.config import get_settings
from ai_server.embeddings.local_provider import LocalHashEmbeddingProvider
from ai_server.embeddings.openai_provider import OpenAiEmbeddingProvider


class EmbeddingProviderFactory:
    def create(self):
        settings = get_settings()
        provider = settings.embedding_provider.lower()
        if provider == 'openai':
            return OpenAiEmbeddingProvider()
        if provider == 'local':
            return LocalHashEmbeddingProvider()
        if settings.openai_api_key and not settings.openai_api_key.startswith('YOUR_'):
            return OpenAiEmbeddingProvider()
        return LocalHashEmbeddingProvider()
