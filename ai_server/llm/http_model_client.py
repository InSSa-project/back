import time

import requests

from ai_server.core.config import get_settings


class HttpModelClient:
    """Adapter for an OpenAI-like FastAPI or self-hosted fine-tuned model server."""

    def __init__(self, base_url=None, endpoint=None, session=None):
        self.settings = get_settings()
        self.base_url = (base_url or self.settings.llm_http_base_url).rstrip('/')
        self.endpoint = endpoint or self.settings.llm_http_endpoint
        self.session = session or requests.Session()

    def complete(self, messages: list[dict], **kwargs) -> dict:
        if not self.base_url:
            return self._configuration_error()

        payload = {
            'model': kwargs.get('model') or self.settings.llm_http_model or self.settings.default_chat_model,
            'messages': messages,
        }
        temperature = kwargs.get('temperature', 0.3)
        max_completion_tokens = kwargs.get('max_completion_tokens', self.settings.max_completion_tokens)
        if temperature is not None:
            payload['temperature'] = temperature
        if max_completion_tokens:
            payload['max_completion_tokens'] = max_completion_tokens
        headers = {'Content-Type': 'application/json'}
        api_key = self._api_key()
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'

        attempts = max(self.settings.llm_request_retries + 1, 1)
        for attempt in range(attempts):
            try:
                response = self.session.post(
                    f'{self.base_url}/{self.endpoint.lstrip("/")}',
                    json=payload,
                    headers=headers,
                    timeout=self.settings.llm_request_timeout,
                )
                response.raise_for_status()
                return self._normalize_response(response.json(), payload['model'])
            except requests.Timeout:
                if attempt + 1 < attempts:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                return {
                    'answer': 'AI 응답 시간이 초과됐어요. 잠시 후 다시 시도해 주세요.',
                    'usage': {'mode': 'http_model_timeout', 'provider': 'http_model'},
                }
            except (requests.RequestException, ValueError) as exc:
                response = getattr(exc, 'response', None)
                return {
                    'answer': 'AI 모델 서버 응답을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.',
                    'usage': {
                        'mode': 'http_model_error',
                        'provider': 'http_model',
                        'error_type': exc.__class__.__name__,
                        **self._error_details(response),
                    },
                }

    def stream(self, messages: list[dict], **kwargs):
        result = self.complete(messages, **kwargs)
        yield result.get('answer', '')

    def _normalize_response(self, data: dict, model: str) -> dict:
        answer = data.get('answer') or data.get('text') or ''
        if not answer:
            choices = data.get('choices') or []
            if choices:
                answer = ((choices[0].get('message') or {}).get('content') or choices[0].get('text') or '')
        usage = data.get('usage') or {}
        return {
            'answer': answer,
            'usage': {'mode': 'http_model', 'provider': 'http_model', 'model': model, **usage},
        }

    def _configuration_error(self) -> dict:
        return {
            'answer': 'AI 모델 서버 설정이 필요해요. 관리자에게 문의해 주세요.',
            'usage': {'mode': 'missing_http_model_url', 'provider': 'http_model'},
        }

    def _api_key(self) -> str:
        return self.settings.llm_http_api_key

    def _error_details(self, response) -> dict:
        if response is None:
            return {}
        text = ''
        try:
            text = response.text
        except Exception:
            text = ''
        return {
            'status_code': getattr(response, 'status_code', None),
            'response_body': text[:500],
        }


class GmsOpenAiClient(HttpModelClient):
    """OpenAI-compatible GMS gateway client."""

    def __init__(self, session=None):
        settings = get_settings()
        super().__init__(
            base_url=settings.gms_base_url,
            endpoint='/api.openai.com/v1/chat/completions',
            session=session,
        )

    def complete(self, messages: list[dict], **kwargs) -> dict:
        if not self.settings.gms_api_key and not self.settings.gemini_api_key:
            return {
                'answer': 'GMS API 키가 설정되지 않았어요. 관리자에게 문의해 주세요.',
                'usage': {'mode': 'missing_gms_key', 'provider': 'gms_openai'},
            }
        kwargs = {
            **kwargs,
            'model': kwargs.get('model') or self.settings.gms_model,
            'temperature': kwargs.get('temperature', None),
        }
        return super().complete(messages, **kwargs)

    def _normalize_response(self, data: dict, model: str) -> dict:
        result = super()._normalize_response(data, model)
        result['usage']['mode'] = 'gms_openai'
        result['usage']['provider'] = 'gms_openai'
        return result

    def _api_key(self) -> str:
        return self.settings.gms_api_key or self.settings.gemini_api_key


FastAPILLMClient = HttpModelClient
LocalFineTunedModelClient = HttpModelClient

