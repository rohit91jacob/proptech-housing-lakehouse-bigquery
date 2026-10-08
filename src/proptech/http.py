"""Resilient downloads with retries, checksums and conditional requests.

``file://`` URLs are supported so tests and CI can run the exact same code path against
committed fixtures without network access.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

CHUNK_SIZE = 1 << 20


class DownloadError(RuntimeError):
    """Raised when a source cannot be fetched after all retries."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Download:
    url: str
    path: Path | None  # None when the server answered 304 Not Modified
    sha256: str | None
    size_bytes: int
    etag: str | None
    last_modified: datetime | None
    fetched_at: datetime

    @property
    def not_modified(self) -> bool:
        return self.path is None


def build_session(user_agent: str, retries: int) -> requests.Session:
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=8)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip"})
    return session


def _parse_http_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(
    session: requests.Session,
    url: str,
    dest_dir: Path,
    *,
    etag: str | None = None,
    timeout: float = 120.0,
) -> Download:
    """Download ``url`` into ``dest_dir``.

    When ``etag`` is given an ``If-None-Match`` request is made and an unchanged source is
    reported as ``not_modified`` without transferring the body.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(UTC)
    parsed = urlparse(url)

    if parsed.scheme == "file":
        source = Path(unquote(parsed.path.lstrip("/") if _is_windows_path(parsed.path) else parsed.path))
        if not source.is_file():
            raise DownloadError(f"fixture not found: {source}")
        target = dest_dir / source.name
        shutil.copyfile(source, target)
        modified = datetime.fromtimestamp(source.stat().st_mtime, tz=UTC)
        return Download(url, target, _hash_file(target), target.stat().st_size, None, modified, fetched_at)

    headers = {"If-None-Match": etag} if etag else {}
    try:
        response = session.get(url, headers=headers, stream=True, timeout=(15, timeout))
    except requests.RequestException as exc:
        raise DownloadError(f"GET {url} failed: {exc}") from exc

    with response:
        if response.status_code == 304:
            return Download(
                url,
                None,
                None,
                0,
                etag,
                _parse_http_date(response.headers.get("Last-Modified")),
                fetched_at,
            )
        if response.status_code != 200:
            raise DownloadError(f"GET {url} returned HTTP {response.status_code}", response.status_code)

        digest = hashlib.sha256()
        size = 0
        name = Path(parsed.path).name or "download"
        with tempfile.NamedTemporaryFile(dir=dest_dir, prefix=f".{name}.", delete=False) as tmp:
            try:
                for chunk in response.iter_content(CHUNK_SIZE):
                    tmp.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            except requests.RequestException as exc:
                Path(tmp.name).unlink(missing_ok=True)
                raise DownloadError(f"GET {url} interrupted: {exc}") from exc

        expected = response.headers.get("Content-Length")
        if expected and response.headers.get("Content-Encoding") is None and int(expected) != size:
            Path(tmp.name).unlink(missing_ok=True)
            raise DownloadError(f"GET {url} truncated: got {size} of {expected} bytes")

        target = dest_dir / name
        Path(tmp.name).replace(target)
        return Download(
            url,
            target,
            digest.hexdigest(),
            size,
            response.headers.get("ETag"),
            _parse_http_date(response.headers.get("Last-Modified")),
            fetched_at,
        )


def _is_windows_path(path: str) -> bool:
    # file:///C:/x -> "/C:/x"
    return len(path) > 2 and path[0] == "/" and path[2] == ":"
