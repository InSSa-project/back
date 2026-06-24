"""
Notice image storage adapter for Supabase Storage.

Uses the Supabase Storage REST API directly via requests (already in requirements.txt).
No supabase-py SDK is required.

Object key convention:
    notices/{notice_id}/{idx}-{content_hash}.webp

Public URL format (public bucket):
    {SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{KEY}

Required settings (from environment):
    SUPABASE_URL              — e.g. https://abcdefgh.supabase.co
    SUPABASE_SERVICE_ROLE_KEY — service-role JWT (backend-only, never exposed)
    SUPABASE_STORAGE_BUCKET   — bucket name (default: "notices")

The service role key must NEVER appear in API responses.

Idempotency:
    upload() checks existence first (HEAD request).  A second upload of the
    same object key is a no-op and returns the existing public URL.
    This makes backfill commands safe to re-run.
"""

import hashlib
import logging
from typing import Optional
from urllib.parse import urlparse

from django.conf import settings

LOGGER = logging.getLogger(__name__)

_STORAGE_PREFIX = 'notices'
_CACHE_CONTROL_IMMUTABLE = 'public, max-age=31536000, immutable'


class NoticeStorageError(Exception):
    pass


class NoticeStorageUnconfigured(NoticeStorageError):
    pass


class SupabaseNoticeStorage:
    """
    Wraps Supabase Storage REST API for notice image upload and URL resolution.

    All methods are synchronous and safe to call from management commands or
    the import pipeline. They do not perform any database access.
    """

    def __init__(self):
        self._base_url: str = getattr(settings, 'SUPABASE_URL', '').rstrip('/')
        self._service_key: str = getattr(settings, 'SUPABASE_SERVICE_ROLE_KEY', '')
        self._bucket: str = getattr(settings, 'SUPABASE_STORAGE_BUCKET', 'notices')
        self._timeout: int = int(getattr(settings, 'SUPABASE_STORAGE_TIMEOUT', 30))

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self._base_url and self._service_key)

    def assert_configured(self) -> None:
        if not self.is_configured():
            raise NoticeStorageUnconfigured(
                'SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set to use notice image storage.'
            )

    def object_key(self, notice_id: int, idx: int, content_hash: str) -> str:
        return f'{_STORAGE_PREFIX}/{notice_id}/{idx}-{content_hash}.webp'

    def public_url(self, object_key: str) -> str:
        return f'{self._base_url}/storage/v1/object/public/{self._bucket}/{object_key}'

    def is_storage_url(self, url: str) -> bool:
        """Return True if url points at this Supabase project's storage."""
        if not self._base_url:
            return False
        return url.startswith(f'{self._base_url}/storage/')

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def object_exists(self, object_key: str) -> bool:
        """HEAD request to check if an object already exists in storage."""
        import requests as req
        try:
            resp = req.head(
                f'{self._base_url}/storage/v1/object/{self._bucket}/{object_key}',
                headers=self._auth_headers(),
                timeout=self._timeout,
            )
            return resp.status_code == 200
        except Exception as exc:
            LOGGER.warning('notice_storage: object_exists HEAD failed key=%s error=%s', object_key, exc)
            return False

    def upload(self, object_key: str, data: bytes, *, content_type: str = 'image/webp') -> str:
        """
        Upload bytes to Supabase Storage.

        Returns the public URL.  If the object already exists the upload is
        skipped and the existing public URL is returned (idempotent).

        Raises NoticeStorageError on network or HTTP failure.
        """
        import requests as req

        self.assert_configured()

        # Idempotency: skip if already uploaded
        if self.object_exists(object_key):
            return self.public_url(object_key)

        headers = {
            **self._auth_headers(),
            'Content-Type': content_type,
            'Cache-Control': _CACHE_CONTROL_IMMUTABLE,
            'x-upsert': 'false',
        }
        try:
            resp = req.post(
                f'{self._base_url}/storage/v1/object/{self._bucket}/{object_key}',
                data=data,
                headers=headers,
                timeout=self._timeout,
            )
        except Exception as exc:
            raise NoticeStorageError(f'Upload network error key={object_key}: {exc}') from exc

        if not resp.ok:
            body = (resp.text or '')[:300]
            # Supabase returns 400 with "The resource already exists" on duplicate
            if resp.status_code == 400 and ('already exist' in body.lower() or 'duplicate' in body.lower()):
                return self.public_url(object_key)
            raise NoticeStorageError(
                f'Upload failed key={object_key} status={resp.status_code} body={body}'
            )

        return self.public_url(object_key)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict:
        return {'Authorization': f'Bearer {self._service_key}'}


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def get_notice_storage() -> SupabaseNoticeStorage:
    """Return a configured SupabaseNoticeStorage instance."""
    return SupabaseNoticeStorage()


def content_hash(data: bytes, length: int = 16) -> str:
    """SHA-1 hex digest of raw bytes, truncated to `length` characters."""
    return hashlib.sha1(data).hexdigest()[:length]


def is_local_media_url(url: str) -> bool:
    lowered = (url or '').lower()
    return lowered.startswith('/media/') or lowered.startswith('/static/')


def is_supabase_storage_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ''
        return host.endswith('.supabase.co')
    except Exception:
        return False
