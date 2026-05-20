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


def extract_text_from_image_urls(image_urls, image_downloader=None):
    """Return OCR extraction metadata for notice images."""
    normalized_urls = [url for url in (image_urls or []) if url]
    provider = _get_ocr_provider()
    if not normalized_urls:
        return _result(provider=provider, status='skipped')

    if provider == OCR_PROVIDER_GOOGLE_VISION:
        return _extract_with_google_vision(normalized_urls, image_downloader=image_downloader)
    if provider == OCR_PROVIDER_CLOVA:
        return _extract_with_clova(normalized_urls, image_downloader=image_downloader)

    return _result(provider=OCR_PROVIDER_MOCK, status='skipped')


def _get_ocr_provider():
    provider = os.getenv('OCR_PROVIDER', OCR_PROVIDER_MOCK).strip().lower()
    if provider not in SUPPORTED_OCR_PROVIDERS:
        return OCR_PROVIDER_MOCK
    return provider


def _extract_with_clova(image_urls, image_downloader=None):
    invoke_url = os.getenv('CLOVA_OCR_INVOKE_URL', '').strip()
    secret_key = os.getenv('CLOVA_OCR_SECRET_KEY', '').strip()
    if not invoke_url or not secret_key:
        return _result(
            provider=OCR_PROVIDER_CLOVA,
            status='failed',
            error='Clova OCR invoke URL or secret key is not configured.',
            failed_count=len(image_urls),
            error_type='provider_auth_error',
        )

    ocr_texts = []
    ocr_boxes = []
    errors = []
    for image_url in image_urls:
        image_bytes = _download_image(image_url, errors, image_downloader=image_downloader)
        if not image_bytes:
            continue

        try:
            response = _post_clova_ocr(invoke_url, secret_key, image_url, image_bytes)
            response.raise_for_status()
            payload = response.json()
            ocr_text = _parse_clova_ocr_text(payload)
            if ocr_text:
                ocr_texts.append(ocr_text)
            ocr_boxes.extend(_parse_clova_ocr_boxes(payload, image_url=image_url, image_index=len(ocr_texts)))
        except Exception as exc:
            errors.append(_error_detail('provider_request_error', image_url, _summarize_error(exc, secret_values=[secret_key])))

    ocr_text = '\n\n'.join(text for text in ocr_texts if text)
    if ocr_text:
        return _result(
            provider=OCR_PROVIDER_CLOVA,
            status='success',
            text=ocr_text,
            error='; '.join(errors[:3]),
            failed_count=len(errors),
            boxes=ocr_boxes,
        )
    if errors:
        return _result(
            provider=OCR_PROVIDER_CLOVA,
            status='failed',
            error='; '.join(errors[:3]),
            failed_count=len(errors),
            error_type=_first_error_type(errors),
        )
    return _result(provider=OCR_PROVIDER_CLOVA, status='failed', error='OCR provider returned empty result.', failed_count=len(image_urls), error_type='provider_empty_result')


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


def _parse_clova_ocr_boxes(payload, image_url='', image_index=0):
    boxes = []
    for image in payload.get('images') or []:
        fields = image.get('fields') or []
        for field in fields:
            text = (field.get('inferText') or '').strip()
            vertices = ((field.get('boundingPoly') or {}).get('vertices')) or []
            box = _box_from_vertices(text, vertices)
            if box:
                box['confidence'] = field.get('inferConfidence')
                box['image_url'] = image_url
                box['image_index'] = image_index
                boxes.append(box)
    return boxes


def _guess_image_format(image_url):
    path = urlparse(image_url).path.lower()
    extension = path.rsplit('.', 1)[-1] if '.' in path else ''
    if extension == 'jpg':
        return 'jpeg'
    if extension in {'jpeg', 'png'}:
        return extension
    return 'png'


def _extract_with_google_vision(image_urls, image_downloader=None):
    if not _is_google_vision_enabled():
        return _result(
            provider=OCR_PROVIDER_GOOGLE_VISION,
            status='failed',
            error='Google Vision OCR is not enabled.',
            failed_count=len(image_urls),
            error_type='provider_auth_error',
        )
    if not os.getenv('GOOGLE_APPLICATION_CREDENTIALS'):
        return _result(
            provider=OCR_PROVIDER_GOOGLE_VISION,
            status='failed',
            error='Google Vision credentials are not configured.',
            failed_count=len(image_urls),
            error_type='provider_auth_error',
        )

    try:
        from google.cloud import vision
    except ImportError:
        return _result(
            provider=OCR_PROVIDER_GOOGLE_VISION,
            status='failed',
            error='google-cloud-vision package is not installed.',
            failed_count=len(image_urls),
            error_type='provider_auth_error',
        )

    try:
        client = vision.ImageAnnotatorClient()
    except Exception as exc:
        return _result(
            provider=OCR_PROVIDER_GOOGLE_VISION,
            status='failed',
            error=_summarize_error(exc, secret_values=[os.getenv('GOOGLE_APPLICATION_CREDENTIALS')]),
            failed_count=len(image_urls),
            error_type='provider_auth_error',
        )

    ocr_texts = []
    ocr_boxes = []
    errors = []
    for image_url in image_urls:
        image_bytes = _download_image(image_url, errors, image_downloader=image_downloader)
        if not image_bytes:
            continue

        try:
            response = client.text_detection(image=vision.Image(content=image_bytes))
            if getattr(response, 'error', None) and response.error.message:
                errors.append(_error_detail('provider_request_error', image_url, response.error.message))
                continue
            annotations = getattr(response, 'text_annotations', None) or []
            if annotations:
                ocr_texts.append(annotations[0].description.strip())
                ocr_boxes.extend(_parse_google_vision_ocr_boxes(annotations[1:], image_url, len(ocr_texts)))
        except Exception as exc:
            errors.append(_error_detail('provider_request_error', image_url, _summarize_error(exc)))

    ocr_text = '\n\n'.join(text for text in ocr_texts if text)
    if ocr_text:
        return _result(
            provider=OCR_PROVIDER_GOOGLE_VISION,
            status='success',
            text=ocr_text,
            error='; '.join(errors[:3]),
            failed_count=len(errors),
            boxes=ocr_boxes,
        )
    if errors:
        return _result(
            provider=OCR_PROVIDER_GOOGLE_VISION,
            status='failed',
            error='; '.join(errors[:3]),
            failed_count=len(errors),
            error_type=_first_error_type(errors),
        )
    return _result(provider=OCR_PROVIDER_GOOGLE_VISION, status='failed', error='OCR provider returned empty result.', failed_count=len(image_urls), error_type='provider_empty_result')


def _parse_google_vision_ocr_boxes(annotations, image_url='', image_index=0):
    boxes = []
    for annotation in annotations:
        text = (getattr(annotation, 'description', '') or '').strip()
        vertices = getattr(getattr(annotation, 'bounding_poly', None), 'vertices', None) or []
        box = _box_from_vertices(text, vertices)
        if box:
            box['confidence'] = getattr(annotation, 'confidence', None)
            box['image_url'] = image_url
            box['image_index'] = image_index
            boxes.append(box)
    return boxes


def _box_from_vertices(text, vertices):
    if not text or not vertices:
        return None
    xs = []
    ys = []
    for vertex in vertices:
        x = _vertex_value(vertex, 'x')
        y = _vertex_value(vertex, 'y')
        if x is not None:
            xs.append(x)
        if y is not None:
            ys.append(y)
    if not xs or not ys:
        return None
    return {
        'text': text,
        'x1': min(xs),
        'y1': min(ys),
        'x2': max(xs),
        'y2': max(ys),
    }


def _vertex_value(vertex, name):
    if isinstance(vertex, dict):
        value = vertex.get(name)
    else:
        value = getattr(vertex, name, None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _download_image(image_url, errors, image_downloader=None):
    try:
        if image_downloader:
            download = image_downloader(image_url)
            content = download.get('content') or b''
            detail = download.get('detail') or {}
        else:
            response = requests.get(image_url, timeout=IMAGE_DOWNLOAD_TIMEOUT_SECONDS)
            response.raise_for_status()
            content = response.content
            detail = {
                'image_download_status': getattr(response, 'status_code', None),
                'mime_type': response.headers.get('Content-Type', '').split(';')[0] if hasattr(response, 'headers') else '',
                'image_size': len(content or b''),
                'image_file_path': '',
            }
    except Exception as exc:
        errors.append(_error_detail('image_download_failed', image_url, _summarize_error(exc)))
        return b''
    if not content:
        errors.append(_error_detail('empty_image', image_url, 'Downloaded image is empty.', detail=detail))
        return b''
    mime_type = detail.get('mime_type', '')
    if mime_type and not mime_type.startswith('image/'):
        errors.append(_error_detail('unsupported_image_format', image_url, f'Unsupported mime type: {mime_type}', detail=detail))
        return b''
    return content


def _is_google_vision_enabled():
    return os.getenv('GOOGLE_VISION_ENABLED', '').strip().lower() in TRUE_VALUES


def _result(provider, status, text='', error='', failed_count=0, boxes=None, error_type=''):
    return {
        'ocr_text': text,
        'ocr_boxes': boxes or [],
        'ocr_provider': provider,
        'ocr_status': status,
        'ocr_error': error,
        'ocr_error_type': error_type or ('unknown' if status == 'failed' and error else ''),
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


def _error_detail(error_type, image_url, message, detail=None):
    payload = {
        'error_type': error_type,
        'image_url': image_url,
        'message': message,
    }
    payload.update(detail or {})
    return json.dumps(payload, ensure_ascii=False)


def _first_error_type(errors):
    for error in errors:
        try:
            return json.loads(error).get('error_type') or 'unknown'
        except (TypeError, ValueError):
            continue
    return 'unknown'
