import json
import os
from pathlib import Path
from urllib.parse import urljoin, urlparse

from django.conf import settings
import requests
from bs4 import BeautifulSoup


SAMPLE_JSON_PATH = settings.BASE_DIR / 'sync' / 'samples' / 'sample_ssafy_notice.json'
DEFAULT_CRAWLER_MODE = 'sample'
MODE_SAMPLE = 'sample'
MODE_SSAFY_NOTICE = 'ssafy_notice'
SUPPORTED_CRAWLER_MODES = {MODE_SAMPLE, MODE_SSAFY_NOTICE}


class SsafyCrawlerError(Exception):
    pass


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


def get_crawler_mode(mode=None):
    selected_mode = mode or os.getenv('SSAFY_CRAWLER_MODE') or DEFAULT_CRAWLER_MODE
    selected_mode = selected_mode.strip()
    if selected_mode not in SUPPORTED_CRAWLER_MODES:
        raise SsafyCrawlerError(
            f'Unsupported SSAFY crawler mode: {selected_mode}. '
            f'Allowed modes: {", ".join(sorted(SUPPORTED_CRAWLER_MODES))}'
        )
    return selected_mode


def load_notices_by_mode(mode=None):
    selected_mode = get_crawler_mode(mode)
    if selected_mode == MODE_SAMPLE:
        return load_sample_notices()
    if selected_mode == MODE_SSAFY_NOTICE:
        return load_ssafy_notice_list()
    raise SsafyCrawlerError(f'Unsupported SSAFY crawler mode: {selected_mode}')


def load_ssafy_notice_list(list_url=None):
    notice_list_url = list_url or os.getenv('SSAFY_NOTICE_LIST_URL')
    if not notice_list_url:
        raise SsafyCrawlerError('SSAFY_NOTICE_LIST_URL is not configured.')

    soup = _request_soup(notice_list_url)
    notice_links = _extract_notice_links(soup, notice_list_url)
    if not notice_links:
        raise SsafyCrawlerError('No notice detail links were found in the SSAFY notice list.')

    notices = []
    for notice_url in notice_links:
        notices.append(fetch_notice_detail(notice_url))
    return notices


def fetch_notice_detail(detail_url):
    soup = _request_soup(detail_url)
    content_node = soup.select_one('article, main, .notice-view, .board-view, .view, body')
    if content_node is None:
        raise SsafyCrawlerError(f'Notice content area was not found: {detail_url}')

    title_node = soup.select_one('h1, h2, .title, .subject, .board-title')
    title = _clean_text(title_node.get_text(' ', strip=True) if title_node else '')
    if not title and soup.title:
        title = _clean_text(soup.title.get_text(' ', strip=True))
    if not title:
        title = 'SSAFY notice'

    raw_text = _clean_text(content_node.get_text('\n', strip=True))
    raw_html = str(content_node)
    notice_id = _guess_notice_id(detail_url)
    published_at = _extract_published_at(soup)

    return {
        'source_type': 'notice',
        'source_url': detail_url,
        'title': title,
        'raw_text': raw_text,
        'raw_html': raw_html,
        'metadata_json': {
            'notice_id': notice_id,
            'collected_from': MODE_SSAFY_NOTICE,
            'published_at': published_at,
        },
    }


def _request_soup(url):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SsafyCrawlerError(f'Failed to request SSAFY notice page: {url}') from exc
    return BeautifulSoup(response.text, 'html.parser')


def _extract_notice_links(soup, base_url):
    links = []
    seen = set()
    for anchor in soup.select('a[href]'):
        href = anchor.get('href', '').strip()
        text = _clean_text(anchor.get_text(' ', strip=True))
        if not href or href.startswith(('javascript:', '#', 'mailto:')):
            continue
        if not _looks_like_notice_link(href, text):
            continue
        absolute_url = urljoin(base_url, href)
        if absolute_url in seen:
            continue
        seen.add(absolute_url)
        links.append(absolute_url)
    return links


def _looks_like_notice_link(href, text):
    target = f'{href} {text}'.lower()
    return any(keyword in target for keyword in ['notice', 'board', 'bbs', '\uacf5\uc9c0'])


def _guess_notice_id(url):
    parsed = urlparse(url)
    if parsed.query:
        return parsed.query
    path_parts = [part for part in parsed.path.split('/') if part]
    return path_parts[-1] if path_parts else url


def _extract_published_at(soup):
    time_node = soup.select_one('time[datetime]')
    if time_node:
        return time_node.get('datetime', '')
    date_node = soup.select_one('.date, .created-at, .reg-date, .board-date')
    return _clean_text(date_node.get_text(' ', strip=True)) if date_node else ''


def _clean_text(value):
    return '\n'.join(line.strip() for line in value.splitlines() if line.strip())

