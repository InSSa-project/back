from ai_server.core.config import get_settings


class OpenAiEmbeddingProvider:
    def __init__(self):
        self.settings = get_settings()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not self.settings.openai_api_key:
            raise RuntimeError('OPENAI_API_KEY is required for OpenAI embeddings.')

        from openai import OpenAI

        client = OpenAI(api_key=self.settings.openai_api_key)
        response = client.embeddings.create(
            model=self.settings.embedding_model,
            input=texts,
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in ordered]
