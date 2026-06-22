import json
import logging
import os
import re
import time
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
ACADEMIC_TOGGLE_SELECTOR = (
    'table.accordian-list td.btn-area button, '
    'table.accordion-list td.btn-area button, '
    '.accordion button, '
    '.accordian button, '
    '[aria-expanded][aria-controls], '
    'button[data-toggle*="collapse"], '
    'a[data-toggle*="collapse"]'
)
ACADEMIC_TOGGLE_WAIT_MS = 300
LAZY_IMAGE_ATTRIBUTES = ('src', 'data-src', 'data-lazy-src', 'data-original', 'data-url')
BACKGROUND_IMAGE_PATTERN = re.compile(r'url\(\s*([\'"]?)(?P<url>.*?)(?:\1)\s*\)', flags=re.IGNORECASE)
EVALUATION_NOTICE_KEYWORDS = ('과목월말평가', '과목 평가', '월말평가', '평가 안내', '1학기 평가')
EVALUATION_NOTICE_KEYWORDS = EVALUATION_NOTICE_KEYWORDS + (
    '과목월말평가',
    '과목 평가',
    '과목평가',
    '월말평가',
    '평가 안내',
    '10회차 과목',
    '5회차 월말평가',
)

_LOGGER = logging.getLogger(__name__)
CRAWLER_DEBUG_DIR = settings.BASE_DIR / 'tmp' / 'ssafy_crawler_debug'
DETAIL_DEBUG_DIR = CRAWLER_DEBUG_DIR / 'ssafy_detail'
CRAWLER_DEBUG_HTML_PREVIEW_LENGTH = 2000
_LAST_COLLECTION_DEBUG = []
_DETAIL_DEBUG_COUNTS = {}
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


class SsafySourceTimeoutError(SsafyCrawlerError):
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
            browser = None
            context = None
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            try:
                page = context.new_page()
                page.set_default_timeout(_detail_timeout_ms())
                _login_ssafy(page, login_url, ssafy_id, ssafy_password)
                selected_sources = _env_source_set('SSAFY_CRAWLER_SOURCES')
                discovery_source_types = selected_sources or {'quest', 'curriculum', 'faq', 'learning_material', 'event'}
                discovered_urls = (
                    _discover_source_urls(
                        page,
                        main_url,
                        login_url=login_url,
                        source_types=discovery_source_types,
                    )
                    if main_url
                    else {}
                )
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
                    source_started_at = _source_log_timestamp()
                    source_started_monotonic = time.monotonic()
                    env_name = SOURCE_LIST_URL_ENV_NAMES.get(source_type, '')
                    _record_collection_debug(
                        f'source_run_start source_type={source_type} started_at={source_started_at} '
                        f'env_var={env_name} url={list_url or "-"}',
                        level='warning',
                    )
                    if not list_url:
                        _record_collection_debug(
                            f'skipped_source={source_type} reason=missing_url env_var={env_name}',
                            level='warning',
                        )
                        _record_source_run_end(
                            source_type=source_type,
                            started_at=source_started_at,
                            started_monotonic=source_started_monotonic,
                            status='skipped',
                            collected_count=0,
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
                        _record_source_run_end(
                            source_type=source_type,
                            started_at=source_started_at,
                            started_monotonic=source_started_monotonic,
                            status='success',
                            collected_count=len(collected),
                        )
                    except SsafySessionExpiredError as exc:
                        exc.collected_items = notices
                        _record_collection_debug(
                            f'failed_source={source_type} url={list_url} error_reason=session_expired error={exc}',
                            level='warning',
                        )
                        _record_source_run_end(
                            source_type=source_type,
                            started_at=source_started_at,
                            started_monotonic=source_started_monotonic,
                            status='failed',
                            collected_count=0,
                            error_count=1,
                        )
                        raise exc
                    except SsafySourceTimeoutError as exc:
                        _record_collection_debug(
                            f'failed_source={source_type} url={list_url} error_reason=timeout error={exc}',
                            level='warning',
                        )
                        _record_source_run_end(
                            source_type=source_type,
                            started_at=source_started_at,
                            started_monotonic=source_started_monotonic,
                            status='timeout',
                            collected_count=0,
                            error_count=1,
                        )
                        continue
                    except Exception as exc:
                        _record_collection_debug(
                            f'failed_source={source_type} url={list_url} error={exc}',
                            level='warning',
                        )
                        _record_source_run_end(
                            source_type=source_type,
                            started_at=source_started_at,
                            started_monotonic=source_started_monotonic,
                            status='failed',
                            collected_count=0,
                            error_count=1,
                        )
            finally:
                if context:
                    context.close()
                if browser:
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
    max_pages = _crawler_max_pages()
    recent_limit = _crawler_recent_limit()
    source_deadline = _source_deadline()
    seen_page_urls = set()
    seen_detail_urls = set()
    seen_page_signatures = {}
    page_stats = []
    total_notice_count = 0
    last_page = 0
    page_size = 0
    links = []
    current_url = list_url
    requested_page = 1

    for page_index in range(1, max_pages + 1):
        _raise_if_source_timed_out(source_type, source_deadline)
        _open_list_page(page, list_url, current_url, requested_page)
        _prepare_academic_page_for_collection(page, source_type, current_url)
        session_reason = _session_expired_reason(page, login_url=login_url)
        if session_reason:
            _record_source_page_debug(
                source_type=source_type,
                url=current_url,
                page=page,
                item_count=0,
                error_reason=f'session_expired:{session_reason}',
            )
            raise SsafySessionExpiredError(
                f'session_expired source_type={source_type} url={current_url} reason={session_reason}'
            )

        soup = BeautifulSoup(page.content(), 'html.parser')
        page_links = link_extractor(soup, current_url)
        totals = _extract_pagination_totals(soup)
        total_notice_count = total_notice_count or totals.get('total_notice_count', 0)
        last_page = max(last_page, totals.get('last_page', 0))
        page_size = page_size or totals.get('page_size', 0)
        page_signature = _page_link_signature(page_links)
        row_count = _count_list_rows(soup)
        duplicate_url_count = sum(
            1
            for link in page_links
            if (link['url'] if isinstance(link, dict) else link) in seen_detail_urls
        )
        first_link = page_links[0] if page_links else {}
        last_link = page_links[-1] if page_links else {}
        first_title, first_source_url = _link_debug_values(first_link)
        last_title, last_source_url = _link_debug_values(last_link)
        if page_signature in seen_page_signatures:
            _record_collection_debug(
                f'pagination_failed source_type={source_type} requested_page={requested_page} '
                f'current_url={_page_url(page)} repeated_page={seen_page_signatures[page_signature]} '
                f'row_count={row_count} extracted_item_count={len(page_links)}',
                level='warning',
            )
            if not page_links:
                requested_page += 1
                current_url = _page_url_for_number(list_url, requested_page)
                continue
            break
        seen_page_signatures[page_signature] = requested_page

        new_link_count = 0
        for link in page_links:
            if recent_limit and len(links) >= recent_limit:
                break
            detail_url = link['url'] if isinstance(link, dict) else link
            if detail_url in seen_detail_urls:
                continue
            seen_detail_urls.add(detail_url)
            links.append(link)
            new_link_count += 1
            link_title = link.get('title', '') if isinstance(link, dict) else ''
            if _looks_like_evaluation_notice(f'{link_title} {detail_url}'):
                _record_collection_debug(
                    f'evaluation_notice_candidate source_type={source_type} page={page_index} '
                    f'title={link_title or "-"} url={detail_url} detail=pending'
                )
        if recent_limit and len(links) >= recent_limit:
            _record_collection_debug(
                f'pagination_stopped source_type={source_type} reason=recent_limit recent_limit={recent_limit}',
                level='warning',
            )
            break
        page_stats.append(
            {
                'page': page_index,
                'row_count': row_count,
                'extracted_count': len(page_links),
                'duplicate_count': duplicate_url_count,
                'unique_count': new_link_count,
            }
        )

        _record_source_page_debug(
            source_type=source_type,
            url=current_url,
            page=page,
            item_count=len(page_links),
            skipped_reason='' if page_links else 'no_detail_links',
        )
        _record_collection_debug(
            f'page_collected source_type={source_type} page={page_index} '
            f'new_links={new_link_count} total_links={len(links)} url={current_url}'
        )
        _record_collection_debug(
            f'pagination_debug source_type={source_type} requested_page={requested_page} current_url={_page_url(page)} '
            f'row_count={row_count} extracted_item_count={len(page_links)} '
            f'first_title={first_title or "-"} last_title={last_title or "-"} '
            f'first_source_url={first_source_url or "-"} last_source_url={last_source_url or "-"} '
            f'duplicate_url_count={duplicate_url_count} unique_count={new_link_count} '
            f'total_notice_count={total_notice_count or "-"} last_page={last_page or "-"} page_size={page_size or "-"} '
            f'{_pagination_controls_debug(soup)} {_filter_controls_debug(soup)}'
        )

        seen_page_urls.add(_normalize_url_without_fragment(current_url))
        next_page_number = _extract_next_page_number(soup, requested_page)
        next_url = _page_url_for_number(list_url, next_page_number) if next_page_number else ''
        if not next_url:
            break
        current_url = next_url
        requested_page = next_page_number

    if len(seen_page_urls) >= max_pages:
        _record_collection_debug(
            f'pagination_stopped source_type={source_type} reason=max_pages max_pages={max_pages}',
            level='warning',
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
    failed_detail_count = 0
    skipped_before_detail_count = 0
    for link in links:
        _raise_if_source_timed_out(source_type, source_deadline)
        if isinstance(link, dict):
            detail_url = link['url']
            list_title = link.get('title', '')
        else:
            detail_url = link
            list_title = ''
        skip_reason = _existing_list_item_skip_reason(source_type, detail_url, list_title)
        if skip_reason:
            skipped_before_detail_count += 1
            _record_collection_debug(
                f'skipped_before_detail source_type={source_type} url={detail_url} '
                f'title={_compact_debug_value(list_title) or "-"} reason={skip_reason}'
            )
            continue
        try:
            detail = fetch_authenticated_detail(
                page,
                detail_url,
                source_type=source_type,
                list_title=list_title,
                login_url=login_url,
            )
            details.append(detail)
            if _looks_like_evaluation_notice(f'{detail.get("title", "")} {detail.get("raw_text", "")}'):
                _record_collection_debug(
                    f'evaluation_notice_candidate source_type={source_type} title={detail.get("title", "")} '
                    f'url={detail_url} detail=ok'
                )
        except SsafySessionExpiredError:
            raise
        except Exception as exc:
            failed_detail_count += 1
            _LOGGER.warning('Failed to collect SSAFY %s detail from %s: %s', source_type, detail_url, exc)
            _record_collection_debug(
                f'failed_detail source_type={source_type} url={detail_url} error={exc}',
                level='warning',
            )
    if failed_detail_count:
        _record_collection_debug(
            f'detail_summary source_type={source_type} success={len(details)} failed={failed_detail_count}',
            level='warning',
        )
    _record_collection_debug(
        f'list_summary source_type={source_type} pages_scanned={len(seen_page_signatures)} '
        f'total_rows_seen={sum(stat["row_count"] for stat in page_stats)} '
        f'unique_items_extracted={len(links)} duplicated_items={sum(stat["duplicate_count"] for stat in page_stats)} '
        f'details_fetched={len(details)} skipped_before_detail={skipped_before_detail_count} '
        f'target_total_notice_count={total_notice_count or "-"} '
        f'last_page={last_page or "-"} page_size={page_size or "-"} '
        f'total_count_unavailable={str(not bool(total_notice_count)).lower()} '
        f'discovered_filter_conditions={_discovered_filter_conditions(page_stats)} '
        f'additional_candidates_count=0 '
        f'pagination_gap={"|".join(_format_page_stat(stat) for stat in page_stats) or "none"}'
    )
    return details


def _crawler_max_pages():
    try:
        return max(1, int(os.getenv('SSAFY_NOTICE_MAX_PAGES') or os.getenv('SSAFY_CRAWLER_MAX_PAGES', '20')))
    except ValueError:
        return 20


def _crawler_recent_limit():
    value = os.getenv('SSAFY_CRAWLER_RECENT_LIMIT') or os.getenv('SSAFY_NOTICE_RECENT_LIMIT') or ''
    if not value:
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        return 0


def _existing_list_item_skip_reason(source_type, detail_url, list_title):
    # Keep Playwright collection DB-free. Duplicate detection runs later in the sync import service.
    return ''


def _compact_debug_value(value):
    return ' '.join(str(value or '').split())[:80]


def _detail_timeout_ms():
    try:
        return max(1000, int(float(os.getenv('SSAFY_DETAIL_TIMEOUT') or '15') * 1000))
    except ValueError:
        return 15000


def _source_deadline():
    try:
        seconds = float(os.getenv('SSAFY_SOURCE_TIMEOUT') or '0')
    except ValueError:
        seconds = 0
    return time.monotonic() + seconds if seconds > 0 else 0


def _source_log_timestamp():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def _record_source_run_end(
    source_type,
    started_at,
    started_monotonic,
    status,
    collected_count,
    error_count=0,
):
    _record_collection_debug(
        f'source_run_end source_type={source_type} started_at={started_at} '
        f'ended_at={_source_log_timestamp()} elapsed_seconds={time.monotonic() - started_monotonic:.3f} '
        f'status={status} collected_count={collected_count} saved_count=pending '
        f'skipped_count=pending error_count={error_count}',
        level='warning',
    )


def _raise_if_source_timed_out(source_type, deadline):
    if deadline and time.monotonic() > deadline:
        raise SsafySourceTimeoutError(f'source_timeout source_type={source_type}')


def _open_list_page(page, list_url, current_url, page_number):
    page.goto(current_url, wait_until='networkidle')
    if page_number <= 1:
        return
    submitted = False
    try:
        submitted = page.evaluate(
            """
            (pageNumber) => {
              const names = ['pageNo', 'pageIndex', 'currentPageNo'];
              let touched = false;
              for (const name of names) {
                const input = document.querySelector(`input[name="${name}"]`);
                if (input) {
                  input.value = String(pageNumber);
                  touched = true;
                }
              }
              const form = document.querySelector('form[name="searchForm"], form[name="frm"], form');
              if (touched && form) {
                form.submit();
                return true;
              }
              return false;
            }
            """,
            page_number,
        )
        if submitted:
            page.wait_for_load_state('networkidle')
    except Exception as exc:
        _record_collection_debug(
            f'pagination_form_submit_failed url={list_url} page={page_number} error={exc}',
            level='warning',
        )
    if submitted:
        return
    try:
        clicked = page.evaluate(
            """
            (pageNumber) => {
              const target = String(pageNumber);
              const anchors = Array.from(document.querySelectorAll('a[href], a[onclick]'));
              const anchor = anchors.find((node) => {
                const text = (node.textContent || '').trim();
                const onclick = node.getAttribute('onclick') || '';
                const href = node.getAttribute('href') || '';
                return text === target || onclick.includes(`'${target}'`) || onclick.includes(`(${target}`)
                  || href.includes(`pageNo=${target}`) || href.includes(`pageIndex=${target}`);
              });
              if (!anchor) return false;
              anchor.click();
              return true;
            }
            """,
            page_number,
        )
        if clicked:
            page.wait_for_load_state('networkidle')
            _record_collection_debug(f'pagination_click_fallback source_url={list_url} page={page_number}')
    except Exception as exc:
        _record_collection_debug(
            f'pagination_click_failed url={list_url} page={page_number} error={exc}',
            level='warning',
        )


def _extract_next_page_url(soup, current_url, seen_page_urls):
    current_page = _current_page_number(current_url)
    candidates = []
    for anchor in soup.select('a[href], a[onclick]'):
        text = _clean_text(anchor.get_text(' ', strip=True))
        href = anchor.get('href', '').strip()
        onclick = anchor.get('onclick', '').strip()
        page_number = _extract_page_number(text, href, onclick)
        if page_number is None:
            continue
        if page_number <= current_page:
            continue
        next_url = _page_url_for_number(current_url, page_number)
        normalized = _normalize_url_without_fragment(next_url)
        if normalized in seen_page_urls:
            continue
        candidates.append((page_number, next_url))
    if not candidates:
        return ''
    return min(candidates, key=lambda item: item[0])[1]


def _extract_next_page_number(soup, current_page):
    candidates = []
    for anchor in soup.select('a[href], a[onclick]'):
        text = _clean_text(anchor.get_text(' ', strip=True))
        href = anchor.get('href', '').strip()
        onclick = anchor.get('onclick', '').strip()
        page_number = _extract_page_number(text, href, onclick)
        if page_number and page_number > current_page:
            candidates.append(page_number)
    if candidates:
        return min(candidates)
    return current_page + 1


def _extract_page_number(text, href, onclick):
    onclick_match = re.search(r'(?:fn\w*Page|goPage|movePage|linkPage)\s*\(\s*[\'"]?(\d{1,3})', onclick or '', re.I)
    if onclick_match:
        return int(onclick_match.group(1))

    href_match = re.search(r'[?&](?:pageNo|pageIndex|currentPageNo)=(\d{1,3})(?:&|$)', href or '', re.I)
    if href_match:
        return int(href_match.group(1))

    if text and text.isdigit() and int(text) <= 500:
        return int(text)
    return None


def _current_page_number(url):
    parsed_url = urlparse(url or '')
    match = re.search(r'(?:pageNo|pageIndex|currentPageNo)=(\d+)', parsed_url.query)
    return int(match.group(1)) if match else 1


def _page_url_for_number(url, page_number):
    if re.search(r'([?&](?:pageNo|pageIndex|currentPageNo)=)\d+', url):
        return re.sub(r'([?&](?:pageNo|pageIndex|currentPageNo)=)\d+', rf'\g<1>{page_number}', url)
    separator = '&' if '?' in url else '?'
    return f'{url}{separator}pageNo={page_number}'


def _page_link_signature(page_links):
    return tuple((link['url'] if isinstance(link, dict) else link) for link in page_links)


def _link_debug_values(link):
    if isinstance(link, dict):
        return link.get('title', ''), link.get('url', '')
    if isinstance(link, str):
        return '', link
    return '', ''


def _count_list_rows(soup):
    rows = soup.select('table tbody tr, .board-list li, .board_list li, .list li')
    return len(rows)


def _pagination_controls_debug(soup):
    names = ['pageIndex', 'pageNo', 'currentPageNo', 'searchCondition', 'searchKeyword']
    found_inputs = []
    for name in names:
        node = soup.select_one(f'input[name="{name}"], select[name="{name}"]')
        if node:
            found_inputs.append(name)
    functions = []
    html = str(soup)
    for function_name in ['fnPage', 'goPage', 'linkPage', 'movePage']:
        if function_name in html:
            functions.append(function_name)
    return (
        f'pagination_inputs={",".join(found_inputs) or "none"} '
        f'pagination_functions={",".join(functions) or "none"}'
    )


def _filter_controls_debug(soup):
    forms = []
    for form in soup.select('form')[:3]:
        forms.append(
            f'{form.get("name") or "-"}:{form.get("method") or "get"}:{form.get("action") or "-"}'
        )

    hidden_inputs = []
    for node in soup.select('input[type="hidden"][name]')[:20]:
        hidden_inputs.append(f'{node.get("name")}={node.get("value", "")[:30]}')

    selects = []
    for node in soup.select('select[name]')[:10]:
        options = [
            _clean_text(option.get_text(' ', strip=True)) or option.get('value', '')
            for option in node.select('option')[:8]
        ]
        selects.append(f'{node.get("name")}:[{"/".join(options)}]')

    buttons = [
        _clean_text(node.get_text(' ', strip=True)) or node.get('value', '')
        for node in soup.select('button, input[type="button"], input[type="submit"]')[:10]
    ]
    tabs = [
        _clean_text(node.get_text(' ', strip=True)) or node.get('href', '') or node.get('onclick', '')
        for node in soup.select('a[href], a[onclick]')[:20]
        if _looks_like_filter_control(node)
    ]
    return (
        f'forms={";".join(forms) or "none"} '
        f'hidden_inputs={";".join(hidden_inputs) or "none"} '
        f'selects={";".join(selects) or "none"} '
        f'buttons={";".join(buttons) or "none"} '
        f'filter_tabs={";".join(tabs) or "none"}'
    )


def _looks_like_filter_control(node):
    target = f'{node.get("href", "")} {node.get("onclick", "")} {node.get_text(" ", strip=True)}'.lower()
    keywords = ['tab', 'category', 'type', 'search', 'filter', 'campus', 'track', 'generation', 'brditmcd']
    korean_keywords = ['공지', '학습', '평가', '기타', '검색', '분류', '트랙', '캠퍼스', '기수']
    return any(keyword in target for keyword in keywords + korean_keywords)


def _discovered_filter_conditions(page_stats):
    if not page_stats:
        return 'none'
    return 'default'


def _extract_pagination_totals(soup):
    text = _clean_text(soup.get_text(' ', strip=True))
    total_notice_count = 0
    for pattern in [r'(?:총|total)\s*[:：]?\s*(\d{1,5})', r'(\d{1,5})\s*(?:건|개)']:
        match = re.search(pattern, text, flags=re.I)
        if match:
            total_notice_count = int(match.group(1))
            break

    page_numbers = []
    for anchor in soup.select('a[href], a[onclick]'):
        page_number = _extract_page_number(
            _clean_text(anchor.get_text(' ', strip=True)),
            anchor.get('href', '').strip(),
            anchor.get('onclick', '').strip(),
        )
        if page_number:
            page_numbers.append(page_number)

    return {
        'total_notice_count': total_notice_count,
        'last_page': max(page_numbers) if page_numbers else 0,
        'page_size': _count_list_rows(soup),
    }


def _format_page_stat(stat):
    return (
        f'p{stat["page"]}:rows={stat["row_count"]},'
        f'extracted={stat["extracted_count"]},dup={stat["duplicate_count"]},unique={stat["unique_count"]}'
    )


def _normalize_url_without_fragment(url):
    return urlparse(url)._replace(fragment='').geturl()


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
    _prepare_academic_page_for_collection(page, source_type, detail_url)
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
    _save_detail_debug_html(soup, source_type, detail_url)
    content_node, content_quality = _select_detail_content_node(soup)
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

    raw_html, raw_text = _clean_detail_html_and_text(content_node)
    image_urls = extract_image_urls_from_html(raw_html, detail_url)
    if source_type == 'academic_rule':
        reply_image_urls = extract_academic_rule_reply_image_urls_from_html(str(soup), detail_url)
        if reply_image_urls:
            image_urls = reply_image_urls
    if not image_urls:
        image_urls = extract_image_urls_from_html(str(soup), detail_url)
    if source_type == 'academic_rule':
        content_image_urls = extract_image_urls_from_html(raw_html, detail_url)
        _record_collection_debug(
            f'academic_collected_images source_type={source_type} content_image_count={len(content_image_urls)} '
            f'metadata_image_count={len(image_urls)} ocr_target_image_count={len(image_urls)} '
            f'urls={_format_debug_urls(image_urls)}',
            level='warning',
        )
    notice_id = _guess_notice_id(detail_url)
    published_at = _extract_published_at(soup)
    is_evaluation_notice = _looks_like_evaluation_notice(f'{title} {raw_text} {raw_html}')
    metadata = {
        'notice_id': notice_id,
        'collected_from': MODE_SSAFY_NOTICE,
        'published_at': published_at,
        'source_url': detail_url,
        'detail_url': detail_url,
        'image_urls': image_urls,
        'ocr_status': 'pending' if image_urls else 'skipped',
        'detail_success': True,
        'real_content': content_quality == 'real_content',
        'content_quality': content_quality,
        'image_found': bool(image_urls),
    }
    cleaned_list_title = _clean_text(list_title)
    if cleaned_list_title:
        metadata['list_title'] = cleaned_list_title
        metadata['original_title'] = cleaned_list_title
        metadata['notice_title'] = cleaned_list_title
    if is_evaluation_notice:
        metadata.update(
            {
                'category': 'exam',
                'document_type': 'evaluation_notice',
                'ocr_ready': bool(image_urls),
            }
        )

    return {
        'source_type': source_type,
        'source_url': detail_url,
        'title': title,
        'raw_text': raw_text,
        'raw_html': raw_html,
        'metadata_json': metadata,
    }


def _select_detail_content_node(soup):
    selectors = [
        '.view_cont',
        '.view_content',
        '.view-content',
        '.board_view',
        '.boardView',
        '.board-view',
        '.notice-view',
        '.noticeView',
        '.detail-content',
        '.detail_content',
        '.content_view',
        '.contentView',
        '.cont',
        '.content',
        '#content',
        'article',
        'main',
        'td',
        'body',
    ]
    best_node = None
    best_score = -1
    best_quality = 'empty_detail'
    for selector in selectors:
        for node in soup.select(selector):
            raw_html, text = _clean_detail_html_and_text(node)
            score = _detail_content_score(text, raw_html)
            if score > best_score:
                best_node = node
                best_score = score
                best_quality = 'real_content' if score >= 20 else 'menu_only_content'
        if best_quality == 'real_content' and selector != 'body':
            break
    return best_node or soup.select_one('body'), best_quality


def _clean_detail_html_and_text(node):
    cleaned = BeautifulSoup(str(node), 'html.parser')
    for removable in cleaned.select(
        'script, style, header, footer, nav, aside, .header, .footer, .gnb, .lnb, .menu, '
        '.breadcrumb, .pagination, .paging, .copyright, .sidebar'
    ):
        removable.decompose()
    raw_html = str(cleaned)
    raw_text = _clean_text(cleaned.get_text('\n', strip=True))
    return raw_html, raw_text


def _detail_content_score(text, raw_html):
    compact = (text or '').replace('\n', ' ').strip()
    if not compact and '<img' not in (raw_html or '').lower():
        return 0
    score = len(compact)
    menu_hits = sum(1 for keyword in ('HOME', 'Copyright', '마이캠퍼스', '로그아웃', '메뉴') if keyword in compact)
    score -= menu_hits * 80
    if '<img' in (raw_html or '').lower():
        score += 30
    return score


def _save_detail_debug_html(soup, source_type, detail_url):
    if not _crawler_debug_html_enabled():
        return
    count = _DETAIL_DEBUG_COUNTS.get(source_type, 0)
    if count >= 3:
        return
    DETAIL_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    notice_id = re.sub(r'[^A-Za-z0-9_-]+', '_', _guess_notice_id(detail_url))[:80] or str(count + 1)
    path = DETAIL_DEBUG_DIR / f'{source_type}_{notice_id}.html'
    path.write_text(str(soup)[:200000], encoding='utf-8')
    _DETAIL_DEBUG_COUNTS[source_type] = count + 1
    _record_collection_debug(f'detail_debug_saved source_type={source_type} url={detail_url} html={path}')


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
    return _extract_links_by_keywords(soup, base_url, ['mentor', 'mentoring', '\uba58\ud1a0\ub9c1', '\uba58\ud1a0'], include_titles=True)


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
    event_url='',
):
    specs = [
        ('notice', notice_url, _extract_notice_link_items),
        ('academic_rule', academic_rule_url, _extract_academic_rule_links),
        ('quest', quest_url, _extract_quest_links),
        ('curriculum', curriculum_url, _extract_curriculum_links),
        ('faq', faq_url, _extract_faq_links),
        ('learning_material', learning_material_url, _extract_learning_material_links),
        ('event', event_url, _extract_event_links),
        ('mentoring_notice', mentoring_notice_url, _extract_mentoring_notice_links),
    ]
    selected_sources = _env_source_set('SSAFY_CRAWLER_SOURCES')
    skipped_sources = _env_source_set('SSAFY_CRAWLER_SKIP_SOURCES')
    filtered = []
    for spec in specs:
        source_type = spec[0]
        if selected_sources and source_type not in selected_sources:
            _record_collection_debug(f'skipped_source={source_type} reason=source_filter')
            continue
        if source_type in skipped_sources:
            _record_collection_debug(f'skipped_source={source_type} reason=skip_source_filter')
            continue
        filtered.append(spec)
    return filtered


def _env_source_set(name):
    value = os.getenv(name, '')
    return {item.strip() for item in value.split(',') if item.strip()}


def _discover_source_urls(page, main_url, login_url=None, source_types=None):
    discovered = {}
    source_types = set(source_types or {'quest', 'curriculum', 'faq', 'learning_material', 'event'})
    try:
        page.goto(main_url, wait_until='networkidle')
        session_reason = _session_expired_reason(page, login_url=login_url)
        if session_reason and session_reason != 'login_or_session_text':
            _record_collection_debug(f'source_discovery_failed reason=session_expired:{session_reason}', level='warning')
            return discovered
        if session_reason == 'login_or_session_text':
            _record_collection_debug('source_discovery_ignored_session_text reason=login_or_session_text')
        candidates = _extract_source_url_candidates(page.content(), main_url, source_types=source_types)
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


def _extract_source_url_candidates(html, base_url, source_types=None):
    soup = BeautifulSoup(html, 'html.parser')
    source_types = set(source_types or {'quest', 'curriculum', 'faq', 'learning_material', 'event'})
    candidates = {source_type: [] for source_type in source_types}
    seen = set()
    for node in soup.select('a, button, li, div, span'):
        text = _clean_text(node.get_text(' ', strip=True))
        raw_targets = [node.get('href', ''), node.get('onclick', ''), node.get('data-url', '')]
        for raw_target in raw_targets:
            for url in _urls_from_menu_target(raw_target, base_url):
                source_type = _guess_source_type_from_menu(url, text, raw_target)
                if not source_type or source_type not in candidates or (source_type, url) in seen:
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


def _prepare_academic_page_for_collection(page, source_type, source_url):
    if source_type != 'academic_rule':
        return

    before_html = _page_content(page)
    before_urls = extract_image_urls_from_html(before_html, source_url)
    toggle_count, opened_toggle_count = _open_academic_toggles(page, source_type)
    after_html = _page_content(page)
    after_urls = extract_image_urls_from_html(after_html, source_url)
    reply_image_urls = extract_academic_rule_reply_image_urls_from_html(after_html, source_url)
    reply_row_count = _academic_rule_reply_row_count(after_html)
    _record_collection_debug(
        f'academic_toggle_images source_type={source_type} toggle_count={toggle_count} '
        f'opened_toggle_count={opened_toggle_count} reply_row_count={reply_row_count} '
        f'reply_image_count={len(reply_image_urls)} page_total_image_count={len(after_urls)} '
        f'content_image_count={len(reply_image_urls)} before_count={len(before_urls)} after_count={len(after_urls)} '
        f'iframe_count={_page_iframe_count(page)} urls={_format_debug_urls(after_urls)}',
        level='warning',
    )
    if _crawler_debug_html_enabled():
        debug_info = _save_crawler_debug_page(page, source_type, 'academic_toggles_opened')
        _record_collection_debug(
            f'debug_saved source_type={source_type} reason=academic_toggles_opened '
            f'html={debug_info.get("html_path", "")} screenshot={debug_info.get("screenshot_path", "")}'
        )


def _open_academic_toggles(page, source_type):
    try:
        toggle_buttons = page.locator(ACADEMIC_TOGGLE_SELECTOR)
        toggle_count = toggle_buttons.count()
    except Exception as exc:
        _record_collection_debug(
            f'academic_toggle_scan_failed source_type={source_type} selector={ACADEMIC_TOGGLE_SELECTOR} error={exc}',
            level='warning',
        )
        return 0, 0

    opened_toggle_count = 0
    for index in range(toggle_count):
        button = toggle_buttons.nth(index)
        try:
            if _academic_toggle_is_open(button):
                continue
            button.click()
            opened_toggle_count += 1
            page.wait_for_timeout(ACADEMIC_TOGGLE_WAIT_MS)
        except Exception as exc:
            _record_collection_debug(
                f'academic_toggle_failed source_type={source_type} index={index} '
                f'selector={ACADEMIC_TOGGLE_SELECTOR} error={exc}',
                level='warning',
            )
    return toggle_count, opened_toggle_count


def _academic_toggle_is_open(button):
    aria_expanded = _locator_attribute(button, 'aria-expanded').lower()
    if aria_expanded:
        return aria_expanded == 'true'

    try:
        button_state = button.evaluate(
            """button => {
                const classNames = button.innerHTML || '';
                const label = (button.textContent || '').trim();
                if (classNames.includes('accordian-arrow-down') || label.includes('열기')) {
                    return 'closed';
                }
                if (classNames.includes('accordian-arrow-up') || label.includes('닫기')) {
                    return 'open';
                }
                return '';
            }"""
        )
        if button_state:
            return button_state == 'open'
    except Exception:
        pass

    class_names = ' '.join(
        _locator_attribute(button, attribute)
        for attribute in ('class', 'data-state')
    ).lower()
    if any(marker in class_names.split() for marker in ('active', 'expanded', 'open', 'opened', 'is-active')):
        return True

    try:
        return bool(
            button.evaluate(
                """button => {
                    const row = button.closest('tr');
                    const reply = row && row.nextElementSibling;
                    if (!reply || !reply.classList.contains('reply')) {
                        return false;
                    }
                    const style = window.getComputedStyle(reply);
                    return !reply.hidden && style.display !== 'none' && style.visibility !== 'hidden';
                }"""
            )
        )
    except Exception:
        return False


def _locator_attribute(locator, name):
    try:
        return (locator.get_attribute(name) or '').strip()
    except Exception:
        return ''


def _page_iframe_count(page):
    try:
        return page.locator('iframe').count()
    except Exception:
        return 0


def _format_debug_urls(image_urls, limit=20):
    if not image_urls:
        return '-'
    urls = ','.join(image_urls[:limit])
    return f'{urls},...' if len(image_urls) > limit else urls


def _crawler_debug_html_enabled():
    return str(os.getenv('SSAFY_CRAWLER_DEBUG_HTML', '')).lower() in {'1', 'true', 'yes', 'on'}


def extract_image_urls_from_html(raw_html, source_url):
    if not raw_html:
        return []

    soup = BeautifulSoup(raw_html, 'html.parser')
    image_urls = []
    seen = set()
    for image in soup.select('img'):
        for attribute in LAZY_IMAGE_ATTRIBUTES:
            _append_image_url(image_urls, seen, image.get(attribute, ''), source_url)
        _append_srcset_image_urls(image_urls, seen, image.get('srcset', ''), source_url)
        _append_srcset_image_urls(image_urls, seen, image.get('data-srcset', ''), source_url)
    for styled_node in soup.select('[style]'):
        for background_url in _extract_background_image_urls(styled_node.get('style', '')):
            _append_image_url(image_urls, seen, background_url, source_url)
    return image_urls


def extract_academic_rule_reply_image_urls_from_html(raw_html, source_url):
    if not raw_html:
        return []

    soup = BeautifulSoup(raw_html, 'html.parser')
    image_urls = []
    seen = set()
    for image in soup.select('tr.reply img'):
        for attribute in LAZY_IMAGE_ATTRIBUTES:
            _append_image_url(image_urls, seen, image.get(attribute, ''), source_url)
        _append_srcset_image_urls(image_urls, seen, image.get('srcset', ''), source_url)
        _append_srcset_image_urls(image_urls, seen, image.get('data-srcset', ''), source_url)
    return image_urls


def _academic_rule_reply_row_count(raw_html):
    if not raw_html:
        return 0
    return len(BeautifulSoup(raw_html, 'html.parser').select('tr.reply'))


def _append_srcset_image_urls(image_urls, seen, srcset, source_url):
    for candidate in (srcset or '').split(','):
        image_url = candidate.strip().split(' ', 1)[0]
        _append_image_url(image_urls, seen, image_url, source_url)


def _extract_background_image_urls(style):
    return [match.group('url').strip() for match in BACKGROUND_IMAGE_PATTERN.finditer(style or '')]


def _append_image_url(image_urls, seen, image_url, source_url):
    image_url = str(image_url or '').strip()
    if not image_url or image_url.startswith(('data:', 'javascript:', 'mailto:', '#')):
        return
    absolute_url = urljoin(source_url, image_url)
    if _is_ignored_ocr_image_url(absolute_url) or absolute_url in seen:
        return
    seen.add(absolute_url)
    image_urls.append(absolute_url)


def _is_ignored_ocr_image_url(image_url):
    path = urlparse(image_url).path.lower()
    return any(keyword in path for keyword in IGNORED_OCR_IMAGE_KEYWORDS)


def _extract_links_by_keywords(soup, base_url, keywords, include_titles=False):
    links = []
    seen = set()
    for anchor in soup.select('a[href], a[onclick]'):
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
    direct_url_match = re.search(r"(?:location\.href|document\.location)\s*=\s*['\"](?P<url>[^'\"]+)['\"]", onclick or '')
    if direct_url_match:
        return urljoin(base_url, direct_url_match.group('url'))

    match = re.search(
        r"(?:fnDetail2?|goDetail|detail|selectDetail|viewDetail)\s*\(\s*['\"]?(?P<id>\d+)[^)]*\)",
        onclick or '',
        flags=re.IGNORECASE,
    )
    if not match:
        return ''

    parsed_base_url = urlparse(base_url)
    detail_path = parsed_base_url.path.replace('/list.do', '/detail.do')
    if detail_path == parsed_base_url.path:
        return ''
    detail_url = urljoin(base_url, detail_path)
    return f'{detail_url}?brdItmSeq={quote(match.group("id"))}'


def _looks_like_evaluation_notice(text):
    return any(keyword in (text or '') for keyword in EVALUATION_NOTICE_KEYWORDS)


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

