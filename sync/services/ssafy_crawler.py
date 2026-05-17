import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

from django.conf import settings
import requests
from bs4 import BeautifulSoup


SAMPLE_JSON_PATH = settings.BASE_DIR / 'sync' / 'samples' / 'sample_ssafy_notice.json'
DEFAULT_CRAWLER_MODE = 'sample'
MODE_SAMPLE = 'sample'
MODE_SSAFY_NOTICE = 'ssafy_notice'
SUPPORTED_CRAWLER_MODES = {MODE_SAMPLE, MODE_SSAFY_NOTICE}
IGNORED_OCR_IMAGE_KEYWORDS = (
    'header-logo',
    'header_logo',
    'logo',
    'icon',
    'banner',
)

_LOGGER = logging.getLogger(__name__)


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
        raise SsafyCrawlerError('Missing required SSAFY crawler environment variables: SSAFY_NOTICE_LIST_URL')

    return load_ssafy_authenticated_documents(notice_list_url=notice_list_url)


def load_ssafy_authenticated_documents(
    notice_list_url=None,
    rule_list_url=None,
    faq_list_url=None,
    quest_list_url=None,
    mentoring_notice_list_url=None,
    curriculum_list_url=None,
    learning_material_list_url=None,
):
    login_url = os.getenv('SSAFY_LOGIN_URL')
    ssafy_id = os.getenv('SSAFY_ID')
    ssafy_password = os.getenv('SSAFY_PASSWORD')
    notice_url = notice_list_url or os.getenv('SSAFY_NOTICE_LIST_URL')
    academic_rule_url = rule_list_url or os.getenv('SSAFY_RULE_LIST_URL')
    faq_url = faq_list_url or os.getenv('SSAFY_FAQ_LIST_URL')
    quest_url = quest_list_url or os.getenv('SSAFY_QUEST_LIST_URL')
    mentoring_notice_url = mentoring_notice_list_url or os.getenv('SSAFY_MENTORING_NOTICE_LIST_URL')
    curriculum_url = curriculum_list_url or os.getenv('SSAFY_CURRICULUM_LIST_URL')
    learning_material_url = learning_material_list_url or os.getenv('SSAFY_LEARNING_MATERIAL_LIST_URL')

    missing_names = _missing_required_env_vars(
        {
            'SSAFY_LOGIN_URL': login_url,
            'SSAFY_ID': ssafy_id,
            'SSAFY_PASSWORD': ssafy_password,
        }
    )
    if missing_names:
        raise SsafyCrawlerError(f'Missing required SSAFY crawler environment variables: {", ".join(missing_names)}')

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SsafyCrawlerError('Playwright is required for ssafy_notice mode. Install playwright and browsers first.') from exc

    notices = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(15000)
            _login_ssafy(page, login_url, ssafy_id, ssafy_password)

            for source_type, list_url, link_extractor in _source_collection_specs(
                notice_url=notice_url,
                academic_rule_url=academic_rule_url,
                faq_url=faq_url,
                quest_url=quest_url,
                mentoring_notice_url=mentoring_notice_url,
                curriculum_url=curriculum_url,
                learning_material_url=learning_material_url,
            ):
                if not list_url:
                    continue
                try:
                    notices.extend(
                        _collect_authenticated_list(
                            page=page,
                            list_url=list_url,
                            source_type=source_type,
                            link_extractor=link_extractor,
                        )
                    )
                except Exception as exc:
                    _LOGGER.warning('Failed to collect SSAFY %s from %s: %s', source_type, list_url, exc)
            context.close()
            browser.close()
    except PlaywrightTimeoutError as exc:
        raise SsafyCrawlerError('Timed out while logging in to or collecting SSAFY pages.') from exc

    if not notices:
        raise SsafyCrawlerError('No SSAFY documents were collected.')
    return notices


def _missing_required_env_vars(env_values):
    return [name for name, value in env_values.items() if not value]


def _login_ssafy(page, login_url, ssafy_id, ssafy_password):
    page.goto(login_url, wait_until='domcontentloaded')
    page.fill('input[name="userId"]', ssafy_id)
    page.fill('input[name="userPwd"]', ssafy_password)
    page.click('button[type="submit"], input[type="submit"], button:has-text("로그인"), a:has-text("로그인")')
    page.wait_for_load_state('networkidle')
    if page.locator('input[name="userId"], input[name="userPwd"]').count():
        raise SsafyCrawlerError('SSAFY login failed. Please check SSAFY_ID, SSAFY_PASSWORD, and login selectors.')


def _collect_authenticated_list(page, list_url, source_type, link_extractor):
    page.goto(list_url, wait_until='networkidle')
    soup = BeautifulSoup(page.content(), 'html.parser')
    links = link_extractor(soup, list_url)
    if not links:
        if source_type == 'academic_rule':
            return [_parse_detail_soup(soup, list_url, source_type=source_type)]
        raise SsafyCrawlerError(f'No {source_type} detail links were found: {list_url}')
    details = []
    for link in links:
        if isinstance(link, dict):
            detail_url = link['url']
            list_title = link.get('title', '')
        else:
            detail_url = link
            list_title = ''
        try:
            details.append(fetch_authenticated_detail(page, detail_url, source_type=source_type, list_title=list_title))
        except Exception as exc:
            _LOGGER.warning('Failed to collect SSAFY %s detail from %s: %s', source_type, detail_url, exc)
    return details


def fetch_authenticated_detail(page, detail_url, source_type='notice', list_title=''):
    page.goto(detail_url, wait_until='networkidle')
    soup = BeautifulSoup(page.content(), 'html.parser')
    return _parse_detail_soup(soup, detail_url, source_type=source_type, list_title=list_title)


def fetch_notice_detail(detail_url, list_title=''):
    soup = _request_soup(detail_url)
    return _parse_detail_soup(soup, detail_url, source_type='notice', list_title=list_title)


def _parse_detail_soup(soup, detail_url, source_type, list_title=''):
    content_node = soup.select_one('article, main, .notice-view, .board-view, .view, body')
    if content_node is None:
        raise SsafyCrawlerError(f'SSAFY content area was not found: {detail_url}')

    title_node = soup.select_one('h1, h2, .title, .subject, .board-title')
    title = _clean_text(title_node.get_text(' ', strip=True) if title_node else '')
    if not title and soup.title:
        title = _clean_text(soup.title.get_text(' ', strip=True))
    if _is_generic_detail_title(title) and list_title:
        title = _clean_text(list_title)
    if not title:
        title = 'SSAFY document'

    raw_text = _clean_text(content_node.get_text('\n', strip=True))
    raw_html = str(content_node)
    image_urls = extract_image_urls_from_html(raw_html, detail_url)
    notice_id = _guess_notice_id(detail_url)
    published_at = _extract_published_at(soup)

    return {
        'source_type': source_type,
        'source_url': detail_url,
        'title': title,
        'raw_text': raw_text,
        'raw_html': raw_html,
        'metadata_json': {
            'notice_id': notice_id,
            'collected_from': MODE_SSAFY_NOTICE,
            'published_at': published_at,
            'image_urls': image_urls,
            'ocr_status': 'pending' if image_urls else 'skipped',
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
    return _extract_links_by_keywords(soup, base_url, ['notice', 'board', 'bbs', '\uacf5\uc9c0'])


def _extract_notice_link_items(soup, base_url):
    return _extract_links_by_keywords(soup, base_url, ['notice', 'board', 'bbs', '\uacf5\uc9c0'], include_titles=True)


def _extract_academic_rule_links(soup, base_url):
    return _extract_links_by_keywords(soup, base_url, ['rule', 'policy', 'academic', 'board', 'bbs', '\uaddc\uc815', '\ud559\uc0ac'])


def _extract_faq_links(soup, base_url):
    return _extract_links_by_keywords(soup, base_url, ['faq', 'qna', 'question', 'answer', 'help', '\uc790\uc8fc', '\ubb38\uc758', '\ub2f5\ubcc0'])


def _extract_quest_links(soup, base_url):
    return _extract_links_by_keywords(soup, base_url, ['quest', 'evaluate', 'evaluation', 'exam', 'test', '\ud3c9\uac00', '\ucd5c\uc885'])


def _extract_mentoring_notice_links(soup, base_url):
    return _extract_links_by_keywords(soup, base_url, ['mentor', 'mentoring', '\uba58\ud1a0\ub9c1', '\uba58\ud1a0'])


def _extract_curriculum_links(soup, base_url):
    return _extract_links_by_keywords(soup, base_url, ['curriculum', 'course', '\ucee4\ub9ac\ud058\ub7fc', '\uac15\uc758\uacc4\ud68d', '\uad50\uc218'])


def _extract_learning_material_links(soup, base_url):
    return _extract_links_by_keywords(soup, base_url, ['learning', 'material', 'study', '\ud559\uc2b5\uc790\ub8cc', '\uc790\ub8cc', '\uad50\uc7ac'])


def _source_collection_specs(
    notice_url,
    academic_rule_url,
    faq_url,
    quest_url,
    mentoring_notice_url,
    curriculum_url,
    learning_material_url,
):
    return [
        ('notice', notice_url, _extract_notice_link_items),
        ('academic_rule', academic_rule_url, _extract_academic_rule_links),
        ('faq', faq_url, _extract_faq_links),
        ('quest', quest_url, _extract_quest_links),
        ('mentoring_notice', mentoring_notice_url, _extract_mentoring_notice_links),
        ('curriculum', curriculum_url, _extract_curriculum_links),
        ('learning_material', learning_material_url, _extract_learning_material_links),
    ]


def extract_image_urls_from_html(raw_html, source_url):
    if not raw_html:
        return []

    soup = BeautifulSoup(raw_html, 'html.parser')
    image_urls = []
    seen = set()
    for image in soup.select('img[src]'):
        src = image.get('src', '').strip()
        if not src or src.startswith(('data:', 'javascript:', 'mailto:', '#')):
            continue
        absolute_url = urljoin(source_url, src)
        if _is_ignored_ocr_image_url(absolute_url):
            continue
        if absolute_url in seen:
            continue
        seen.add(absolute_url)
        image_urls.append(absolute_url)
    return image_urls


def _is_ignored_ocr_image_url(image_url):
    path = urlparse(image_url).path.lower()
    return any(keyword in path for keyword in IGNORED_OCR_IMAGE_KEYWORDS)


def _extract_links_by_keywords(soup, base_url, keywords, include_titles=False):
    links = []
    seen = set()
    for anchor in soup.select('a[href]'):
        href = anchor.get('href', '').strip()
        onclick = anchor.get('onclick', '').strip()
        text = _clean_text(anchor.get_text(' ', strip=True))
        onclick_detail_url = _extract_detail_url_from_onclick(onclick, base_url)
        if onclick_detail_url:
            absolute_url = onclick_detail_url
        else:
            if not href or href.startswith(('javascript:', '#', 'mailto:')):
                continue
            if not _looks_like_link(href, text, keywords):
                continue
            absolute_url = urljoin(base_url, href)
        if _is_same_or_list_page_url(absolute_url, base_url):
            continue
        if absolute_url in seen:
            continue
        seen.add(absolute_url)
        if include_titles:
            links.append({'url': absolute_url, 'title': text})
        else:
            links.append(absolute_url)
    return links


def _looks_like_link(href, text, keywords):
    target = f'{href} {text}'.lower()
    return any(keyword in target for keyword in keywords)


def _extract_detail_url_from_onclick(onclick, base_url):
    match = re.search(r"fnDetail\(['\"]?(?P<id>[^'\",)]+)['\"]?", onclick or '')
    if not match:
        return ''

    parsed_base_url = urlparse(base_url)
    detail_path = parsed_base_url.path.replace('/list.do', '/detail.do')
    if detail_path == parsed_base_url.path:
        return ''
    detail_url = urljoin(base_url, detail_path)
    return f'{detail_url}?brdItmSeq={quote(match.group("id"))}'


def _is_same_or_list_page_url(url, base_url):
    parsed_url = urlparse(url)
    parsed_base_url = urlparse(base_url)
    if parsed_url._replace(fragment='') == parsed_base_url._replace(fragment=''):
        return True

    path_name = parsed_url.path.rstrip('/').split('/')[-1].lower()
    return path_name in {'list.do', 'list', 'index.do', 'index'}


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


def _is_generic_detail_title(title):
    cleaned = _clean_text(title)
    return cleaned in {'공지사항 상세', '게시물 상세', '상세', 'SSAFY'} or cleaned.endswith('상세')

