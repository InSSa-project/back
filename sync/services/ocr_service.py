def extract_text_from_image_urls(image_urls):
    """Return OCR extraction metadata for notice images.

    This is intentionally a mock implementation. A later provider such as
    Google Vision can keep this return shape while replacing the internals.
    """
    normalized_urls = [url for url in (image_urls or []) if url]
    if not normalized_urls:
        return {
            'ocr_text': '',
            'ocr_provider': 'mock',
            'ocr_status': 'skipped',
            'ocr_error': '',
        }

    return {
        'ocr_text': '',
        'ocr_provider': 'mock',
        'ocr_status': 'skipped',
        'ocr_error': '',
    }
