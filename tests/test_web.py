"""Tests for the web helpers. Network-free: only the URL-validation and
HTML-extraction paths are exercised here; the live DDG search and the
actual fetch round-trip are covered manually."""

from __future__ import annotations

import pytest

from shellllm.web import (
    FetchError,
    _check_url_safe,
    _TextExtractor,
    fetch_url_as_text,
)

# ─── URL safety / SSRF guard ────────────────────────────────────────────


def test_rejects_non_http_scheme():
    with pytest.raises(FetchError, match="http"):
        _check_url_safe("ftp://example.com/")


def test_rejects_file_scheme():
    with pytest.raises(FetchError, match="http"):
        _check_url_safe("file:///etc/passwd")


def test_rejects_javascript_scheme():
    with pytest.raises(FetchError):
        _check_url_safe("javascript:alert(1)")


def test_rejects_missing_host():
    with pytest.raises(FetchError, match="host"):
        _check_url_safe("https://")


def test_rejects_loopback_literal():
    with pytest.raises(FetchError, match="private/local"):
        _check_url_safe("http://127.0.0.1/")


def test_rejects_localhost_name():
    # 'localhost' resolves to 127.0.0.1 / ::1 on every realistic host.
    with pytest.raises(FetchError, match="private/local"):
        _check_url_safe("http://localhost:8080/admin")


def test_rejects_rfc1918():
    with pytest.raises(FetchError, match="private/local"):
        _check_url_safe("http://10.0.0.5/")


def test_rejects_link_local():
    with pytest.raises(FetchError, match="private/local"):
        _check_url_safe("http://169.254.169.254/latest/meta-data/")


def test_rejects_ipv6_loopback():
    with pytest.raises(FetchError, match="private/local"):
        _check_url_safe("http://[::1]/")


def test_fetch_url_as_text_returns_error_string_on_refusal():
    # Wrapper must never raise — LLM tool dispatch relies on text output.
    out = fetch_url_as_text("http://127.0.0.1/")
    assert out.startswith("(fetch_url failed:")
    assert "private/local" in out


# ─── HTML-to-text extraction ────────────────────────────────────────────


def _extract(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text


def test_strips_script_tags():
    out = _extract("<html><body>hi<script>alert(1)</script>there</body></html>")
    assert "alert" not in out
    assert "hi" in out
    assert "there" in out


def test_strips_style_tags():
    out = _extract("<html><style>body{color:red}</style><body>visible</body></html>")
    assert "color:red" not in out
    assert "visible" in out


def test_block_tags_add_newlines():
    out = _extract("<p>one</p><p>two</p>")
    assert "one" in out
    assert "two" in out
    assert "\n" in out


def test_plain_text_passes_through():
    out = _extract("hello world")
    assert "hello world" in out


def test_multiple_skip_tags_recover():
    # Sequential script blocks must not leave the parser in skip mode.
    html = "<body>before<script>junk1();</script>middle<style>.x{}</style>after</body>"
    out = _extract(html)
    assert "before" in out
    assert "middle" in out
    assert "after" in out
    assert "junk1" not in out
    assert ".x{" not in out
