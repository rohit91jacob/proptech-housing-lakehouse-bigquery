from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import responses

from proptech.http import DownloadError, build_session, fetch

URL = "https://files.example.test/research/public_csvs/zhvi/Metro.csv"
BODY = b"RegionID,SizeRank\n1,0\n"


@responses.activate
def test_retries_transient_errors_then_succeeds(tmp_path: Path) -> None:
    responses.add(responses.GET, URL, status=503)
    responses.add(responses.GET, URL, status=200, body=BODY, headers={"ETag": '"abc"'})
    session = build_session("test-agent", retries=3)
    session.adapters["https://"].max_retries.backoff_factor = 0  # keep the test fast
    result = fetch(session, URL, tmp_path)
    assert result.path is not None and result.path.read_bytes() == BODY
    assert result.sha256 == hashlib.sha256(BODY).hexdigest()
    assert result.etag == '"abc"'
    assert len(responses.calls) == 2
    assert responses.calls[0].request.headers["User-Agent"] == "test-agent"


@responses.activate
def test_conditional_request_reports_not_modified(tmp_path: Path) -> None:
    responses.add(responses.GET, URL, status=304)
    result = fetch(build_session("t", 0), URL, tmp_path, etag='"abc"')
    assert result.not_modified
    assert responses.calls[0].request.headers["If-None-Match"] == '"abc"'


@responses.activate
def test_truncated_body_is_an_error(tmp_path: Path) -> None:
    responses.add(
        responses.GET,
        URL,
        status=200,
        body=BODY,
        headers={"Content-Length": "999"},
        auto_calculate_content_length=False,
    )
    with pytest.raises(DownloadError):
        fetch(build_session("t", 0), URL, tmp_path)
    assert not list(tmp_path.glob("*.csv")), "partial downloads must not be left behind"


@responses.activate
def test_http_errors_carry_status_code(tmp_path: Path) -> None:
    responses.add(responses.GET, URL, status=404)
    with pytest.raises(DownloadError) as excinfo:
        fetch(build_session("t", 0), URL, tmp_path)
    assert excinfo.value.status_code == 404


def test_file_urls_are_supported(tmp_path: Path) -> None:
    source = tmp_path / "src" / "a.csv"
    source.parent.mkdir()
    source.write_bytes(BODY)
    result = fetch(build_session("t", 0), source.as_uri(), tmp_path / "dest")
    assert result.path is not None and result.path.read_bytes() == BODY
    assert result.sha256 == hashlib.sha256(BODY).hexdigest()
    with pytest.raises(DownloadError):
        fetch(build_session("t", 0), (tmp_path / "missing.csv").as_uri(), tmp_path / "dest")
