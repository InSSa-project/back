import requests

from ai_server.core.config import get_settings


class GeminiProvider:
    def __init__(self):
        self.settings = get_settings()

    def complete(self, messages: list[dict], **kwargs) -> dict:
        if self._gms_key():
            return self._complete_with_gms(messages, **kwargs)

        if not self.settings.gemini_api_key:
            return {
                'answer': (
                    'FastAPI AI 서버 연결은 정상입니다. '
                    'Gemini 답변을 받으려면 back/.env에 GEMINI_API_KEY를 설정해 주세요.'
                ),
                'usage': {'mode': 'missing_gemini_api_key'},
            }

        try:
            import google.generativeai as genai

            genai.configure(api_key=self.settings.gemini_api_key)
            model = genai.GenerativeModel(kwargs.get('model') or self.settings.default_gemini_model)
            response = model.generate_content(
                self._to_gemini_prompt(messages),
                generation_config={'max_output_tokens': self.settings.max_completion_tokens},
            )
            return {
                'answer': response.text or '',
                'usage': {
                    'mode': 'gemini',
                    **self._normalize_usage(getattr(response, 'usage_metadata', None)),
                },
            }
        except Exception as exc:
            return {
                'answer': 'AI 답변을 생성하지 못했어요. 잠시 후 다시 시도해 주세요.',
                'usage': {'mode': 'gemini_error', 'error_type': exc.__class__.__name__},
            }

    def stream(self, messages: list[dict], **kwargs):
        if self._gms_key():
            result = self._complete_with_gms(messages, **kwargs)
            yield result.get('answer', '')
            return

        if not self.settings.gemini_api_key:
            yield 'FastAPI AI 서버 연결은 정상입니다. GEMINI_API_KEY를 설정하면 실제 답변을 받을 수 있습니다.'
            return

        try:
            import google.generativeai as genai

            genai.configure(api_key=self.settings.gemini_api_key)
            model = genai.GenerativeModel(kwargs.get('model') or self.settings.default_gemini_model)
            for chunk in model.generate_content(
                self._to_gemini_prompt(messages),
                generation_config={'max_output_tokens': self.settings.max_completion_tokens},
                stream=True,
            ):
                if chunk.text:
                    yield chunk.text
        except Exception as exc:
            yield 'AI 답변을 생성하지 못했어요. 잠시 후 다시 시도해 주세요.'

    def _to_gemini_prompt(self, messages: list[dict]) -> str:
        parts = []
        for message in messages:
            role = message.get('role', 'user')
            content = message.get('content', '')
            parts.append(f'[{role.upper()}]\n{content}')
        return '\n\n'.join(parts)

    def _complete_with_gms(self, messages: list[dict], **kwargs) -> dict:
        model = kwargs.get('model') or self.settings.default_gemini_model
        base_url = self.settings.gemini_api_base_url.rstrip('/')
        url = f'{base_url}/generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
        payload = {
            'generationConfig': {'maxOutputTokens': self.settings.max_completion_tokens},
            'contents': [
                {
                    'parts': [
                        {'text': self._to_gemini_prompt(messages)}
                    ]
                }
            ]
        }
        try:
            response = requests.post(
                url,
                headers={
                    'Content-Type': 'application/json',
                    'x-goog-api-key': self._gms_key(),
                },
                json=payload,
                timeout=self.settings.llm_request_timeout,
            )
            response.raise_for_status()
            data = response.json()
            return {
                'answer': self._extract_gms_text(data),
                'usage': {
                    'mode': 'gms_gemini',
                    'model': model,
                    **self._normalize_usage(data.get('usageMetadata')),
                },
            }
        except Exception as exc:
            return {
                'answer': 'AI 답변을 생성하지 못했어요. 잠시 후 다시 시도해 주세요.',
                'usage': {'mode': 'gms_gemini_error', 'error_type': exc.__class__.__name__},
            }

    def _normalize_usage(self, metadata) -> dict:
        if not metadata:
            return {}

        def read(*keys):
            for key in keys:
                value = metadata.get(key) if isinstance(metadata, dict) else getattr(metadata, key, None)
                if value is not None:
                    return value
            return None

        usage = {
            'prompt_tokens': read('promptTokenCount', 'prompt_token_count'),
            'completion_tokens': read('candidatesTokenCount', 'candidates_token_count'),
            'total_tokens': read('totalTokenCount', 'total_token_count'),
        }
        return {key: value for key, value in usage.items() if value is not None}

    def _extract_gms_text(self, data: dict) -> str:
        texts = []
        for candidate in data.get('candidates') or []:
            content = candidate.get('content') or {}
            for part in content.get('parts') or []:
                text = part.get('text')
                if text:
                    texts.append(text)
        return '\n'.join(texts)

    def _gms_key(self) -> str:
        if self.settings.gms_api_key:
            return self.settings.gms_api_key
        if 'gms.ssafy.io' in self.settings.gemini_api_base_url and self.settings.gemini_api_key:
            return self.settings.gemini_api_key
        return ''





# Explicit client name for provider-independent wiring.
GeminiClient = GeminiProvider

