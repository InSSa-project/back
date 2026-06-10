import os
from urllib.parse import urljoin, urlparse


# User-facing notice APIs should expose only official SSAFY notices.
# Other source types are preserved for admin/raw-data views and AI/RAG ingestion.
USER_VISIBLE_NOTICE_SOURCE_TYPES = {
    'notice',
    'ssafy_notice',
    'announcement',
}

AI_DOCUMENT_NOTICE_SOURCE_TYPES = {
    *USER_VISIBLE_NOTICE_SOURCE_TYPES,
    'academic_rule',
    'mentoring_notice',
    'mentor_story',
    'geeknews',
    'external_article',
    'article',
    'blog',
    'curriculum',
    'learning_material',
    'quest',
    'faq',
    'resource',
    'reference',
}

HIDDEN_USER_NOTICE_SOURCE_TYPES = AI_DOCUMENT_NOTICE_SOURCE_TYPES - USER_VISIBLE_NOTICE_SOURCE_TYPES

GENERIC_NOTICE_TITLES = {
    '게시물 목록',
    '게시물 상세',
    '공지사항 상세',
    '멘토 스토리 상세',
    '상세',
    '목록',
    'SSAFY',
    '寃뚯떆臾?紐⑸줉',
    '寃뚯떆臾??곸꽭',
    '怨듭??ы빆 ?곸꽭',
    '硫섑넗 ?ㅽ넗由??곸꽭',
    '?곸꽭',
    '紐⑸줉',
}
NOTICE_TITLE_KEYS = (
    'title',
    'subject',
    'notice_title',
    'article_title',
    'board_title',
    'original_title',
    'list_title',
)
NOTICE_URL_KEYS = (
    'source_url',
    'detail_url',
    'original_url',
    'url',
    'link',
    'href',
)
RAW_TEXT_TITLE_STOPWORDS = {
    '멘토 스토리',
    '멘토칼럼',
    '공지사항',
    '학사규정',
    'FAQ',
    '1:1 문의',
    '조회',
    '좋아요수',
    '댓글',
    '硫섑넗 ?ㅽ넗由?',
    '硫섑넗移쇰읆',
    '怨듭??ы빆',
    '?숈궗洹쒖젙',
    '1:1 臾몄쓽',
    '議고쉶',
    '醫뗭븘?붿닔',
    '?볤?',
}


def is_user_visible_notice_source_type(source_type):
    return str(source_type or '').strip().lower() in USER_VISIBLE_NOTICE_SOURCE_TYPES


def user_visible_notice_queryset(queryset):
    return queryset.filter(source_type__in=USER_VISIBLE_NOTICE_SOURCE_TYPES)


def notice_title(raw_data):
    title = str(getattr(raw_data, 'title', '') or '').strip()
    if not _is_generic_notice_title(title):
        return title

    metadata = getattr(raw_data, 'metadata_json', None) or {}
    for source in _notice_metadata_sources(metadata):
        for key in NOTICE_TITLE_KEYS:
            candidate = str(_metadata_value(source, key) or '').strip()
            if candidate and not _is_generic_notice_title(candidate):
                return candidate

    raw_text_title = _notice_title_from_raw_text(getattr(raw_data, 'raw_text', ''))
    if raw_text_title:
        return raw_text_title
    return title


def notice_source_url(raw_data):
    candidates = [getattr(raw_data, 'source_url', '')]
    metadata = getattr(raw_data, 'metadata_json', None) or {}
    for source in _notice_metadata_sources(metadata):
        candidates.extend(_metadata_value(source, key) for key in NOTICE_URL_KEYS)

    normalized_urls = [_normalize_notice_url(candidate) for candidate in candidates]
    detail_urls = [url for url in normalized_urls if url and not _is_list_page_url(url)]
    if detail_urls:
        return detail_urls[0]
    return None


def _notice_metadata_sources(metadata):
    if not isinstance(metadata, dict):
        return []
    return [
        metadata,
        metadata.get('raw_json'),
        metadata.get('metadata_json'),
    ]


def _metadata_value(metadata, key):
    if not isinstance(metadata, dict):
        return ''
    return metadata.get(key) or ''


def _is_generic_notice_title(title):
    cleaned = ' '.join(str(title or '').split())
    return cleaned in GENERIC_NOTICE_TITLES or cleaned.endswith('상세') or cleaned.endswith('?곸꽭')


def _notice_title_from_raw_text(raw_text):
    tokens = [token.strip() for token in str(raw_text or '').replace('\n', '|').split('|')]
    tokens = [token for token in tokens if token]
    if len(tokens) >= 3 and tokens[0] in RAW_TEXT_TITLE_STOPWORDS:
        candidate = tokens[2]
        if _is_raw_text_title_candidate(candidate):
            return candidate
    for candidate in tokens:
        if _is_raw_text_title_candidate(candidate):
            return candidate
    return ''


def _is_raw_text_title_candidate(value):
    value = str(value or '').strip()
    if not value or value in RAW_TEXT_TITLE_STOPWORDS or _is_generic_notice_title(value):
        return False
    if value.isdigit() or len(value) < 4:
        return False
    if ' ' not in value and value.endswith(('건', '개', '嫄?', '??')):
        return False
    return True


def _normalize_notice_url(value):
    value = str(value or '').strip()
    if not value or value == '#':
        return None
    lowered = value.lower()
    if lowered.startswith(('javascript:', 'mailto:', 'tel:')):
        return None
    if lowered.startswith(('http://', 'https://')):
        return value
    if lowered.startswith('//'):
        return f'https:{value}'
    if lowered.startswith('/'):
        base_url = _ssafy_base_url()
        return urljoin(base_url, value) if base_url else None
    return None


def _is_list_page_url(value):
    path_name = urlparse(value).path.rstrip('/').split('/')[-1].lower()
    return path_name in {'list', 'list.do', 'index', 'index.do'}


def _ssafy_base_url():
    for env_name in (
        'SSAFY_MAIN_URL',
        'SSAFY_NOTICE_LIST_URL',
        'SSAFY_MENTORING_LIST_URL',
        'SSAFY_MENTORING_NOTICE_LIST_URL',
    ):
        env_value = os.getenv(env_name, '').strip()
        if env_value:
            parsed = urlparse(env_value)
            if parsed.scheme and parsed.netloc:
                return f'{parsed.scheme}://{parsed.netloc}'
    return ''
