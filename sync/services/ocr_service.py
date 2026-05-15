import json
import os
import time
import uuid
from urllib.parse import urlparse

import requests


OCR_PROVIDER_MOCK = 'mock'
OCR_PROVIDER_GOOGLE_VISION = 'google_vision'
OCR_PROVIDER_CLOVA = 'clova'
SUPPORTED_OCR_PROVIDERS = {OCR_PROVIDER_MOCK, OCR_PROVIDER_GOOGLE_VISION, OCR_PROVIDER_CLOVA}
TRUE_VALUES = {'1', 'true', 'yes', 'on'}
IMAGE_DOWNLOAD_TIMEOUT_SECONDS = 10
CLOVA_OCR_TIMEOUT_SECONDS = 20


def extract_text_from_image_urls(image_urls):
    """Return OCR extraction metadata for notice images."""
    normalized_urls = [url for url in (image_urls or []) if url]
    provider = _get_ocr_provider()
    if not normalized_urls:
        return _result(provider=provider, status='skipped')

    if provider == OCR_PROVIDER_GOOGLE_VISION:
        return _extract_with_google_vision(normalized_urls)
    if provider == OCR_PROVIDER_CLOVA:
        return _extract_with_clova(normalized_urls)

    return _result(provider=OCR_PROVIDER_MOCK, status='skipped')


def _get_ocr_provider():
    provider = os.getenv('OCR_PROVIDER', OCR_PROVIDER_MOCK).strip().lower()
    if provider not in SUPPORTED_OCR_PROVIDERS:
        return OCR_PROVIDER_MOCK
    return provider


def _extract_with_clova(image_urls):
    invoke_url = os.getenv('CLOVA_OCR_INVOKE_URL', '').strip()
    secret_key = os.getenv('CLOVA_OCR_SECRET_KEY', '').strip()
    if not invoke_url or not secret_key:
        return _result(
            provider=OCR_PROVIDER_CLOVA,
            status='failed',
            error='Clova OCR invoke URL or secret key is not configured.',
            failed_count=len(image_urls),
        )

    ocr_texts = []
    errors = []
    for image_url in image_urls:
        image_bytes = _download_image(image_url, errors)
        if not image_bytes:
            continue

        try:
            response = _post_clova_ocr(invoke_url, secret_key, image_url, image_bytes)
            response.raise_for_status()
            ocr_text = _parse_clova_ocr_text(response.json())
            if ocr_text:
                ocr_texts.append(ocr_text)
        except Exception as exc:
            errors.append(_summarize_error(exc, secret_values=[secret_key]))

    ocr_text = '\n\n'.join(text for text in ocr_texts if text)
    if ocr_text:
        return _result(
            provider=OCR_PROVIDER_CLOVA,
            status='success',
            text=ocr_text,
            error='; '.join(errors[:3]),
            failed_count=len(errors),
        )
    if errors:
        return _result(
            provider=OCR_PROVIDER_CLOVA,
            status='failed',
            error='; '.join(errors[:3]),
            failed_count=len(errors),
        )
    return _result(provider=OCR_PROVIDER_CLOVA, status='skipped')


def _post_clova_ocr(invoke_url, secret_key, image_url, image_bytes):
    image_format = _guess_image_format(image_url)
    message = {
        'version': 'V2',
        'requestId': str(uuid.uuid4()),
        'timestamp': int(time.time() * 1000),
        'images': [
            {
                'format': image_format,
                'name': 'notice-image',
            }
        ],
    }
    files = {
        'file': (f'notice-image.{image_format}', image_bytes, f'image/{image_format}'),
    }
    return requests.post(
        invoke_url,
        headers={'X-OCR-SECRET': secret_key},
        data={'message': json.dumps(message)},
        files=files,
        timeout=CLOVA_OCR_TIMEOUT_SECONDS,
    )


def _parse_clova_ocr_text(payload):
    lines = []
    for image in payload.get('images') or []:
        fields = image.get('fields') or []
        for field in fields:
            text = (field.get('inferText') or '').strip()
            if text:
                lines.append(text)
    return '\n'.join(lines)


def _guess_image_format(image_url):
    path = urlparse(image_url).path.lower()
    extension = path.rsplit('.', 1)[-1] if '.' in path else ''
    if extension == 'jpg':
        return 'jpeg'
    if extension in {'jpeg', 'png'}:
        return extension
    return 'png'


def _extract_with_google_vision(image_urls):
    if not _is_google_vision_enabled():
        return _result(
            provider=OCR_PROVIDER_GOOGLE_VISION,
            status='failed',
            error='Google Vision OCR is not enabled.',
            failed_count=len(image_urls),
        )
    if not os.getenv('GOOGLE_APPLICATION_CREDENTIALS'):
        return _result(
            provider=OCR_PROVIDER_GOOGLE_VISION,
            status='failed',
            error='Google Vision credentials are not configured.',
            failed_count=len(image_urls),
        )

    try:
        from google.cloud import vision
    except ImportError:
        return _result(
            provider=OCR_PROVIDER_GOOGLE_VISION,
            status='failed',
            error='google-cloud-vision package is not installed.',
            failed_count=len(image_urls),
        )

    try:
        client = vision.ImageAnnotatorClient()
    except Exception as exc:
        return _result(
            provider=OCR_PROVIDER_GOOGLE_VISION,
            status='failed',
            error=_summarize_error(exc),
            failed_count=len(image_urls),
        )

    ocr_texts = []
    errors = []
    for image_url in image_urls:
        image_bytes = _download_image(image_url, errors)
        if not image_bytes:
            continue

        try:
            response = client.text_detection(image=vision.Image(content=image_bytes))
            if getattr(response, 'error', None) and response.error.message:
                errors.append(response.error.message)
                continue
            annotations = getattr(response, 'text_annotations', None) or []
            if annotations:
                ocr_texts.append(annotations[0].description.strip())
        except Exception as exc:
            errors.append(_summarize_error(exc))

    ocr_text = '\n\n'.join(text for text in ocr_texts if text)
    if ocr_text:
        return _result(
            provider=OCR_PROVIDER_GOOGLE_VISION,
            status='success',
            text=ocr_text,
            error='; '.join(errors[:3]),
            failed_count=len(errors),
        )
    if errors:
        return _result(
            provider=OCR_PROVIDER_GOOGLE_VISION,
            status='failed',
            error='; '.join(errors[:3]),
            failed_count=len(errors),
        )
    return _result(provider=OCR_PROVIDER_GOOGLE_VISION, status='skipped')


def _download_image(image_url, errors):
    try:
        response = requests.get(image_url, timeout=IMAGE_DOWNLOAD_TIMEOUT_SECONDS)
        response.raise_for_status()
    except Exception as exc:
        errors.append(_summarize_error(exc))
        return b''
    return response.content


def _is_google_vision_enabled():
    return os.getenv('GOOGLE_VISION_ENABLED', '').strip().lower() in TRUE_VALUES


def _result(provider, status, text='', error='', failed_count=0):
    return {
        'ocr_text': text,
        'ocr_provider': provider,
        'ocr_status': status,
        'ocr_error': error,
        'ocr_failed_count': failed_count,
    }


def _summarize_error(exc, secret_values=None):
    message = str(exc).strip()
    if not message:
        message = exc.__class__.__name__
    for secret_value in secret_values or []:
        if secret_value:
            message = message.replace(secret_value, '[redacted]')
    return message[:300]
