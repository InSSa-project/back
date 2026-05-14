import json
from pathlib import Path

from django.conf import settings


SAMPLE_JSON_PATH = settings.BASE_DIR / 'sync' / 'samples' / 'sample_ssafy_notice.json'


def load_sample_notices(sample_path=None):
    path = Path(sample_path) if sample_path else SAMPLE_JSON_PATH
    with path.open(encoding='utf-8') as sample_file:
        payload = json.load(sample_file)

    notices = payload.get('notices', payload)
    return [
        {
            'source_type': notice.get('source_type', 'notice'),
            'source_url': notice.get('source_url', ''),
            'title': notice.get('title', ''),
            'raw_text': notice.get('raw_text', ''),
            'raw_html': notice.get('raw_html', ''),
            'metadata_json': notice.get('metadata_json', {}),
        }
        for notice in notices
    ]

