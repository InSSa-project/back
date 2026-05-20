import json
import logging
import os
import re
from datetime import datetime
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
CRAWLER_DEBUG_DIR = settings.BASE_DIR / 'tmp' / 'ssafy_crawler_debug'
CRAWLER_DEBUG_HTML_PREVIEW_LENGTH = 2000
_LAST_COLLECTION_DEBUG = []
SOURCE_LIST_URL_ENV_NAMES = {
    'notice': 'SSAFY_NOTICE_LIST_URL',
    'academic_rule': 'SSAFY_RULE_LIST_URL',
    'faq': 'SSAFY_FAQ_LIST_URL',
    'quest': 'SSAFY_QUEST_LIST_URL',
    'mentoring_notice': 'SSAFY_MENTORING_LIST_URL',
    'curriculum': 'SSAFY_CURRICULUM_LIST_URL',
    'learning_material': 'SSAFY_LEARNING_MATERIAL_LIST_URL',
    'event': 'SSAFY_EVENT_LIST_URL',
}
SESSION_EXPIRED_KEYWORDS = (
    '\ub85c\uadf8\uc778',
    '\uc138\uc158',
    '\ub9cc\ub8cc',
    'login',
    'session expired',
)


class SsafyCrawlerError(Exception):
    pass


class SsafySessionExpiredError(SsafyCrawlerError):
    def __init__(self, message, collected_items=None):
        super().__init__(message)
        self.collected_items = collected_items or []


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


def get_last_collection_debug():
    return list(_LAST_COLLECTION_DEBUG)


def _reset_collection_debug():
    _LAST_COLLECTION_DEBUG.clear()


def _record_collection_debug(message, level='info'):
    _LAST_COLLECTION_DEBUG.append(message)
    if level == 'warning':
        _LOGGER.warning(message)
    else:
        _LOGGER.info(message)


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
    event_list_url=None,
):
    _reset_collection_debug()
    login_url = os.getenv('SSAFY_LOGIN_URL')
    ssafy_id = os.getenv('SSAFY_ID')
    ssafy_password = os.getenv('SSAFY_PASSWORD')
    notice_url = notice_list_url or os.getenv('SSAFY_NOTICE_LIST_URL')
    academic_rule_url = rule_list_url or os.getenv('SSAFY_RULE_LIST_URL')
    faq_url = faq_list_url or os.getenv('SSAFY_FAQ_LIST_URL')
    quest_url = quest_list_url or os.getenv('SSAFY_QUEST_LIST_URL')
    mentoring_notice_url = (
        mentoring_notice_list_url
        or os.getenv('SSAFY_MENTORING_LIST_URL')
        or os.getenv('SSAFY_MENTORING_NOTICE_LIST_URL')
    )
    curriculum_url = curriculum_list_url or os.getenv('SSAFY_CURRICULUM_LIST_URL')
    learning_material_url = learning_material_list_url or os.getenv('SSAFY_LEARNING_MATERIAL_LIST_URL')
    event_url = event_list_url or os.getenv('SSAFY_EVENT_LIST_URL')
    main_url = os.getenv('SSAFY_MAIN_URL')

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
            discovered_urls = _discover_source_urls(page, main_url, login_url=login_url) if main_url else {}
            faq_url = faq_url or discovered_urls.get('faq')
            quest_url = quest_url or discovered_urls.get('quest')
            curriculum_url = curriculum_url or discovered_urls.get('curriculum')
            learning_material_url = learning_material_url or discovered_urls.get('learning_material')
            event_url = event_url or discovered_urls.get('event')

            for source_type, list_url, link_extractor in _source_collection_specs(
                notice_url=notice_url,
                academic_rule_url=academic_rule_url,
                faq_url=faq_url,
                quest_url=quest_url,
                mentoring_notice_url=mentoring_notice_url,
                curriculum_url=curriculum_url,
                learning_material_url=learning_material_url,
                event_url=event_url,
            ):
                env_name = SOURCE_LIST_URL_ENV_NAMES.get(source_type, '')
                if not list_url:
                    _record_collection_debug(
                        f'skipped_source={source_type} reason=missing_url env_var={env_name}',
                        level='warning',
                    )
                    continue
                _record_collection_debug(f'source_url source_type={source_type} env_var={env_name} url={list_url}')
                try:
                    collected = _collect_authenticated_list(
                        page=page,
                        list_url=list_url,
                        source_type=source_type,
                        link_extractor=link_extractor,
                        login_url=login_url,
                    )
                    notices.extend(collected)
                    _record_collection_debug(f'collected_source={source_type} count={len(collected)}')
                except SsafySessionExpiredError as exc:
                    exc.collected_items = notices
                    _record_collection_debug(
                        f'failed_source={source_type} url={list_url} error_reason=session_expired error={exc}',
                        level='warning',
                    )
                    raise exc
                except Exception as exc:
                    _record_collection_debug(
                        f'failed_source={source_type} url={list_url} error={exc}',
                        level='warning',
                    )
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


def _collect_authenticated_list(page, list_url, source_type, link_extractor, login_url=None):
    page.goto(list_url, wait_until='networkidle')
    session_reason = _session_expired_reason(page, login_url=login_url)
    if session_reason:
        _record_source_page_debug(
            source_type=source_type,
            url=list_url,
            page=page,
            item_count=0,
            error_reason=f'session_expired:{session_reason}',
        )
        raise SsafySessionExpiredError(
            f'session_expired source_type={source_type} url={list_url} reason={session_reason}'
        )

    soup = BeautifulSoup(page.content(), 'html.parser')
    links = link_extractor(soup, list_url)
    _record_source_page_debug(
        source_type=source_type,
        url=list_url,
        page=page,
        item_count=len(links),
        skipped_reason='' if links else 'no_detail_links',
    )
    if not links:
        debug_info = _save_crawler_debug_page(
            page=page,
            source_type=source_type,
            reason='no_detail_links',
        )
        _record_collection_debug(
            (
                f'debug_saved source_type={source_type} reason=no_detail_links '
                f'title={debug_info.get("title", "")} url={debug_info.get("url", "")} '
                f'html={debug_info.get("html_path", "")} screenshot={debug_info.get("screenshot_path", "")}'
            ),
            level='warning',
        )
        if source_type in {'academic_rule', 'curriculum'}:
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
            details.append(
                fetch_authenticated_detail(
                    page,
                    detail_url,
                    source_type=source_type,
                    list_title=list_title,
                    login_url=login_url,
                )
            )
        except SsafySessionExpiredError:
            raise
        except Exception as exc:
            _LOGGER.warning('Failed to collect SSAFY %s detail from %s: %s', source_type, detail_url, exc)
    return details


def _record_source_page_debug(source_type, url, page, item_count, skipped_reason='', error_reason=''):
    parts = [
        f'source_page source_type={source_type}',
        f'url={url}',
        f'final_url={_page_url(page)}',
        f'page_title={_page_title(page)}',
        f'item_count={item_count}',
    ]
    if skipped_reason:
        parts.append(f'skipped_reason={skipped_reason}')
    if error_reason:
        parts.append(f'error_reason={error_reason}')
    _record_collection_debug(' '.join(parts), level='warning' if skipped_reason or error_reason else 'info')


def _save_crawler_debug_page(page, source_type, reason):
    CRAWLER_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    safe_source_type = re.sub(r'[^a-zA-Z0-9_-]+', '_', source_type or 'unknown')
    safe_reason = re.sub(r'[^a-zA-Z0-9_-]+', '_', reason or 'debug')
    base_path = CRAWLER_DEBUG_DIR / f'{timestamp}_{safe_source_type}_{safe_reason}'

    try:
        title = page.title()
    except Exception:
        title = ''
    url = getattr(page, 'url', '') or ''
    try:
        html = page.content()
    except Exception:
        html = ''

    html_path = base_path.with_suffix('.html')
    metadata_path = base_path.with_suffix('.json')
    screenshot_path = base_path.with_suffix('.png')

    html_path.write_text(html, encoding='utf-8')
    screenshot_saved_path = ''
    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
        screenshot_saved_path = str(screenshot_path)
    except Exception:
        screenshot_saved_path = ''

    metadata = {
        'source_type': source_type,
        'reason': reason,
        'title': title,
        'url': url,
        'html_path': str(html_path),
        'screenshot_path': screenshot_saved_path,
        'html_preview': html[:CRAWLER_DEBUG_HTML_PREVIEW_LENGTH],
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    metadata['metadata_path'] = str(metadata_path)
    return metadata


def _session_expired_reason(page, login_url=None):
    final_url = _page_url(page)
    page_title = _page_title(page)
    if _looks_like_login_url(final_url, login_url):
        return 'login_url_redirect'
    if _has_login_inputs(page):
        return 'login_inputs_visible'
    if _looks_like_login_title(final_url, page_title):
        return 'login_page_title'
    content = _page_content(page)
    content_sample = content[:5000].lower()
    if any(keyword.lower() in content_sample for keyword in SESSION_EXPIRED_KEYWORDS):
        return 'login_or_session_text'
    return ''


def _page_title(page):
    try:
        return page.title()
    except Exception:
        return ''


def _page_url(page):
    return getattr(page, 'url', '') or ''


def _page_content(page):
    try:
        return page.content()
    except Exception:
        return ''


def _has_login_inputs(page):
    try:
        return page.locator('input[name="userId"], input[name="userPwd"]').count() > 0
    except Exception:
        return False


def _looks_like_login_url(final_url, login_url=None):
    parsed_final = urlparse(final_url or '')
    final_path = (parsed_final.path or '').lower()
    if 'login' in final_path:
        return True
    if login_url:
        parsed_login = urlparse(login_url)
        return bool(parsed_login.path and parsed_final.path == parsed_login.path)
    return False


def _looks_like_login_title(final_url, page_title):
    target = f'{final_url} {page_title}'.lower()
    return 'login' in target or '\ub85c\uadf8\uc778' in target


def fetch_authenticated_detail(page, detail_url, source_type='notice', list_title='', login_url=None):
    page.goto(detail_url, wait_until='networkidle')
    session_reason = _session_expired_reason(page, login_url=login_url)
    if session_reason:
        _record_source_page_debug(
            source_type=source_type,
            url=detail_url,
            page=page,
            item_count=0,
            error_reason=f'session_expired:{session_reason}',
        )
        raise SsafySessionExpiredError(
            f'session_expired source_type={source_type} url={detail_url} reason={session_reason}'
        )
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


def _extract_event_links(soup, base_url):
    return _extract_links_by_keywords(soup, base_url, ['event', 'ssafyday', 'ssafy day', '\uc774\ubca4\ud2b8', '\uc2f8\ud53c\ub370\uc774'])


def _source_collection_specs(
    notice_url,
    academic_rule_url,
    faq_url,
    quest_url,
    mentoring_notice_url,
    curriculum_url,
    learning_material_url,
    event_url,
):
    return [
        ('notice', notice_url, _extract_notice_link_items),
        ('academic_rule', academic_rule_url, _extract_academic_rule_links),
        ('quest', quest_url, _extract_quest_links),
        ('curriculum', curriculum_url, _extract_curriculum_links),
        ('faq', faq_url, _extract_faq_links),
        ('learning_material', learning_material_url, _extract_learning_material_links),
        ('event', event_url, _extract_event_links),
        ('mentoring_notice', mentoring_notice_url, _extract_mentoring_notice_links),
    ]


def _discover_source_urls(page, main_url, login_url=None):
    discovered = {}
    try:
        page.goto(main_url, wait_until='networkidle')
        session_reason = _session_expired_reason(page, login_url=login_url)
        if session_reason and session_reason != 'login_or_session_text':
            _record_collection_debug(f'source_discovery_failed reason=session_expired:{session_reason}', level='warning')
            return discovered
        if session_reason == 'login_or_session_text':
            _record_collection_debug('source_discovery_ignored_session_text reason=login_or_session_text')
        candidates = _extract_source_url_candidates(page.content(), main_url)
        for source_type, urls in candidates.items():
            _record_collection_debug(f'source_url_candidates source_type={source_type} urls={",".join(urls[:10])}')
            for url in urls:
                if _candidate_is_accessible(page, url, source_type, login_url=login_url):
                    discovered[source_type] = url
                    _record_collection_debug(f'discovered_source_url source_type={source_type} url={url}')
                    break
    except Exception as exc:
        _record_collection_debug(f'source_discovery_failed error={exc}', level='warning')
    return discovered


def _extract_source_url_candidates(html, base_url):
    soup = BeautifulSoup(html, 'html.parser')
    candidates = {source_type: [] for source_type in ['quest', 'curriculum', 'faq', 'learning_material', 'event']}
    seen = set()
    for node in soup.select('a, button, li, div, span'):
        text = _clean_text(node.get_text(' ', strip=True))
        raw_targets = [node.get('href', ''), node.get('onclick', ''), node.get('data-url', '')]
        for raw_target in raw_targets:
            for url in _urls_from_menu_target(raw_target, base_url):
                source_type = _guess_source_type_from_menu(url, text, raw_target)
                if not source_type or (source_type, url) in seen:
                    continue
                seen.add((source_type, url))
                candidates[source_type].append(url)
    return {source_type: urls for source_type, urls in candidates.items() if urls}


def _urls_from_menu_target(raw_target, base_url):
    if not raw_target:
        return []
    urls = []
    target = raw_target.strip()
    if target and not target.startswith(('javascript:', '#', 'mailto:')) and '.do' in target:
        urls.append(urljoin(base_url, target))
    for quoted in re.findall(r"['\"](?P<url>[^'\"]+\.do(?:\?[^'\"]*)?)['\"]", target):
        urls.append(urljoin(base_url, quoted))
    return [_normalize_list_url(url) for url in urls]


def _normalize_list_url(url):
    parsed = urlparse(url)
    path = parsed.path
    if path.endswith('/detail.do'):
        path = path[:-len('/detail.do')] + '/list.do'
    return parsed._replace(path=path, fragment='').geturl()


def _guess_source_type_from_menu(url, text, raw_target):
    target = f'{url} {text} {raw_target}'.lower()
    if any(keyword in target for keyword in ['quest', 'eval', 'test', '\ud3c9\uac00', '\uc2dc\ud5d8']):
        return 'quest'
    if any(keyword in target for keyword in ['curriculum', 'course', 'weekly', '\ucee4\ub9ac\ud058', '\uc8fc\uac04\ud559\uc2b5']):
        return 'curriculum'
    if any(keyword in target for keyword in ['faq', 'qna', '\uc790\uc8fc', '\ubb38\uc758']):
        return 'faq'
    if any(keyword in target for keyword in ['learning', 'material', 'study', '\ud559\uc2b5\uc790\ub8cc', '\uc790\ub8cc']):
        return 'learning_material'
    if any(keyword in target for keyword in ['event', 'ssafyday', 'ssafy day', '\uc774\ubca4\ud2b8', '\uc2f8\ud53c\ub370\uc774']):
        return 'event'
    return ''


def _candidate_is_accessible(page, url, source_type, login_url=None):
    try:
        page.goto(url, wait_until='networkidle')
        session_reason = _session_expired_reason(page, login_url=login_url)
        if session_reason:
            _record_collection_debug(
                f'source_url_candidate source_type={source_type} url={url} accessible=false reason=session_expired:{session_reason}',
                level='warning',
            )
            return False
        _record_collection_debug(f'source_url_candidate source_type={source_type} url={url} accessible=true')
        return True
    except Exception as exc:
        _record_collection_debug(
            f'source_url_candidate source_type={source_type} url={url} accessible=false error={exc}',
            level='warning',
        )
        return False


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
    match = re.search(r"fnDetail2?\(['\"]?(?P<id>[^'\",)]+)['\"]?", onclick or '')
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

