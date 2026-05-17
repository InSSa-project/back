from ai_server.core.config import get_settings


class OpenAiProvider:
    def __init__(self):
        self.settings = get_settings()

    def complete(self, messages: list[dict], **kwargs) -> dict:
        if not self.settings.openai_api_key:
            return {
                'answer': (
                    'FastAPI AI 서버 연결은 정상입니다. '
                    '실제 OpenAI 답변을 받으려면 back/.env에 OPENAI_API_KEY를 설정해 주세요.'
                ),
                'usage': {'mode': 'missing_openai_api_key'},
            }

        from openai import OpenAI

        client = OpenAI(api_key=self.settings.openai_api_key)
        try:
            response = client.chat.completions.create(
                model=kwargs.get('model') or self.settings.default_chat_model,
                messages=messages,
                temperature=kwargs.get('temperature', 0.3),
            )
            return {
                'answer': response.choices[0].message.content or '',
                'usage': response.usage.model_dump() if response.usage else {},
            }
        except Exception as exc:
            return {
                'answer': f'OpenAI 호출 중 오류가 발생했습니다: {exc}',
                'usage': {'mode': 'openai_error', 'error_type': exc.__class__.__name__},
            }

    def stream(self, messages: list[dict], **kwargs):
        if not self.settings.openai_api_key:
            yield 'FastAPI AI 서버 연결은 정상입니다. OPENAI_API_KEY를 설정하면 실제 답변을 받을 수 있습니다.'
            return

        from openai import OpenAI

        client = OpenAI(api_key=self.settings.openai_api_key)
        with client.chat.completions.stream(
            model=kwargs.get('model') or self.settings.default_chat_model,
            messages=messages,
            temperature=kwargs.get('temperature', 0.3),
        ) as stream:
            for event in stream:
                if event.type == 'content.delta':
                    yield event.delta
