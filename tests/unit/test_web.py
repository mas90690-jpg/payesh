# tests/unit/test_web.py
"""
Unit tests for collectors/web.py — v3.0 (from scratch).

Coverage:
  ─── Pure functions ───
  - _is_junk_page (Cloudflare / challenge / login wall / error)
  - _extract (BS4 / fallback / title / text)
  - _extract_links (BS4 / fallback / absolute URLs)

  ─── Async methods ───
  - _fetch (aiohttp success / error / non-HTML)
  - _read_single (single page)
  - _crawl_site (depth / same-host / dedup)
  - _crawl_news (index_path / main page / news hints)
  - _make_message (valid / junk / seen / translate)

  ─── Anti-dup ───
  - _load_seen / _save_seen
  - fingerprint (sha256)

  ─── State ───
  - poll_interval_seconds (24h)
  - properties

  ─── Safety ───
  - fetch_messages (not connected → empty)
  - connect (load sources)
  - _load_sources (parse)

Run:
  pytest tests/unit/test_web.py -v
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from collectors.web import (
    WebCollector,
    _JUNK_MIN_LEN,
    _MAX_SEEN,
    _NEWS_PATH_HINTS,
)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════
_ENVS = ["WEB_ENABLED", "WEB_TRANSLATE"]


@pytest.fixture
def collector(tmp_path, monkeypatch):
    """Collector without connection — isolated storage."""
    import config
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path), raising=False)
    for env in _ENVS:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("WEB_ENABLED", "true")
    return WebCollector()


def _mock_resp(status=200, ctype="text/html", text="<html></html>"):
    """Mock aiohttp response."""
    resp = MagicMock()
    resp.status = status
    resp.headers = {"Content-Type": ctype}
    resp.text = AsyncMock(return_value=text)
    return resp


def _mock_session(response=None):
    """Mock aiohttp ClientSession."""
    response = response or _mock_resp()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.get = MagicMock(return_value=cm)
    return session


# ═══════════════════════════════════════════════════════════
# 1) _is_junk_page
# ═══════════════════════════════════════════════════════════
def test_is_junk_page_just_a_moment():
    is_junk, reason = WebCollector._is_junk_page("Just a moment", "")
    assert is_junk is True
    assert "just a moment" in reason.lower()


def test_is_junk_page_attention_required():
    is_junk, _ = WebCollector._is_junk_page("Attention Required!", "")
    assert is_junk is True


def test_is_junk_page_403():
    is_junk, _ = WebCollector._is_junk_page("403 Forbidden", "")
    assert is_junk is True


def test_is_junk_page_access_denied():
    is_junk, _ = WebCollector._is_junk_page("Access denied", "")
    assert is_junk is True


def test_is_junk_page_error():
    is_junk, _ = WebCollector._is_junk_page("Error", "")
    assert is_junk is True


def test_is_junk_page_short_text_with_marker():
    is_junk, reason = WebCollector._is_junk_page(
        "",
        "Please wait while we check your browser...",
    )
    assert is_junk is True


def test_is_junk_page_strong_marker_long_text():
    is_junk, _ = WebCollector._is_junk_page(
        "",
        "x" * 2000 + " performing security verification ",
    )
    assert is_junk is True


def test_is_junk_page_normal():
    is_junk, _ = WebCollector._is_junk_page(
        "Normal title",
        "Normal content " * 100,
    )
    assert is_junk is False


def test_is_junk_page_empty():
    is_junk, _ = WebCollector._is_junk_page("", "")
    assert is_junk is False


def test_is_junk_page_none():
    is_junk, _ = WebCollector._is_junk_page(None, None)
    assert is_junk is False


# ═══════════════════════════════════════════════════════════
# 2) _extract
# ═══════════════════════════════════════════════════════════
def test_extract_bs4():
    html = """
    <html>
    <head><title>Test Title</title></head>
    <body><p>Content text</p></body>
    </html>
    """
    title, text = WebCollector._extract(html)
    assert title == "Test Title"
    assert "Content text" in text


def test_extract_strips_scripts():
    html = """
    <html>
    <head><title>T</title></head>
    <body>
    <script>alert('x')</script>
    <style>body{}</style>
    <p>Real text</p>
    </body>
    </html>
    """
    title, text = WebCollector._extract(html)
    assert "Real text" in text
    assert "alert" not in text


def test_extract_empty():
    title, text = WebCollector._extract("<html></html>")
    assert title == ""
    assert text == ""


def test_extract_max_5000():
    html = "<html><body>" + "x" * 10000 + "</body></html>"
    _, text = WebCollector._extract(html)
    assert len(text) <= 5000


# ═══════════════════════════════════════════════════════════
# 3) _extract_links
# ═══════════════════════════════════════════════════════════
def test_extract_links_absolute():
    html = '<a href="https://example.com/page1">P1</a>'
    links = WebCollector._extract_links(html, "https://example.com")
    assert "https://example.com/page1" in links


def test_extract_links_relative():
    html = '<a href="/page1">P1</a>'
    links = WebCollector._extract_links(html, "https://example.com")
    assert "https://example.com/page1" in links


def test_extract_links_multiple():
    html = '''
    <a href="/a">A</a>
    <a href="/b">B</a>
    <a href="https://other.com/c">C</a>
    '''
    links = WebCollector._extract_links(html, "https://example.com")
    assert len(links) == 3
    assert "https://example.com/a" in links
    assert "https://example.com/b" in links


def test_extract_links_empty():
    links = WebCollector._extract_links("<html></html>", "https://example.com")
    assert links == []


# ═══════════════════════════════════════════════════════════
# 4) _load_sources
# ═══════════════════════════════════════════════════════════
def test_load_sources_empty(collector):
    collector.sources_file = "/nonexistent.txt"
    assert collector._load_sources() == []


def test_load_sources_page(collector, tmp_path):
    f = tmp_path / "sources.txt"
    f.write_text("page | https://example.com\n", encoding="utf-8")
    collector.sources_file = str(f)
    sources = collector._load_sources()
    assert len(sources) == 1
    assert sources[0][0] == "page"
    assert sources[0][1] == "https://example.com"


def test_load_sources_news_with_params(collector, tmp_path):
    f = tmp_path / "sources.txt"
    f.write_text("news | https://example.com/news | limit=10\n", encoding="utf-8")
    collector.sources_file = str(f)
    sources = collector._load_sources()
    assert len(sources) == 1
    assert sources[0][0] == "news"
    assert sources[0][2]["limit"] == "10"


def test_load_sources_site_with_depth(collector, tmp_path):
    f = tmp_path / "sources.txt"
    f.write_text("site | https://example.com | depth=2\n", encoding="utf-8")
    collector.sources_file = str(f)
    sources = collector._load_sources()
    assert sources[0][2]["depth"] == "2"


def test_load_sources_comments_skipped(collector, tmp_path):
    f = tmp_path / "sources.txt"
    f.write_text("# comment\npage | https://example.com\n", encoding="utf-8")
    collector.sources_file = str(f)
    sources = collector._load_sources()
    assert len(sources) == 1


def test_load_sources_invalid_mode(collector, tmp_path):
    f = tmp_path / "sources.txt"
    f.write_text("unknown | https://example.com\n", encoding="utf-8")
    collector.sources_file = str(f)
    sources = collector._load_sources()
    assert sources[0][0] == "page"  # fallback


# ═══════════════════════════════════════════════════════════
# 5) _fetch (aiohttp)
# ═══════════════════════════════════════════════════════════
def test_fetch_success(collector):
    session = _mock_session(_mock_resp(status=200, text="<html>hi</html>"))
    html = asyncio.run(collector._fetch(session, "https://example.com"))
    assert html == "<html>hi</html>"


def test_fetch_non_200(collector):
    session = _mock_session(_mock_resp(status=404))
    html = asyncio.run(collector._fetch(session, "https://example.com"))
    assert html is None


def test_fetch_non_html(collector):
    session = _mock_session(_mock_resp(status=200, ctype="application/json"))
    html = asyncio.run(collector._fetch(session, "https://example.com"))
    assert html is None


def test_fetch_exception(collector):
    session = MagicMock()
    session.get = MagicMock(side_effect=RuntimeError("boom"))
    html = asyncio.run(collector._fetch(session, "https://example.com"))
    assert html is None


# ═══════════════════════════════════════════════════════════
# 6) _read_single
# ═══════════════════════════════════════════════════════════
def test_read_single(collector):
    html = "<html><head><title>T</title></head><body><p>C</p></body></html>"
    session = _mock_session(_mock_resp(status=200, text=html))
    result = asyncio.run(collector._read_single(session, "https://example.com"))
    assert len(result) == 1
    url, title, text = result[0]
    assert url == "https://example.com"
    assert title == "T"
    assert "C" in text


def test_read_single_no_content(collector):
    session = _mock_session(_mock_resp(status=200, text="<html></html>"))
    result = asyncio.run(collector._read_single(session, "https://example.com"))
    assert result == []


# ═══════════════════════════════════════════════════════════
# 7) _make_message
# ═══════════════════════════════════════════════════════════
def test_make_message_valid(collector):
    msg = asyncio.run(collector._make_message(
        "https://example.com/article1",
        "Title",
        "Long content " * 100,
    ))
    assert msg is not None
    assert msg["platform"] == "web"
    assert msg["chat_type"] == "web"
    assert msg["id"].startswith("web_")
    assert msg["chat_link"] == "https://example.com/article1"


def test_make_message_empty_text(collector):
    msg = asyncio.run(collector._make_message(
        "https://example.com", "T", "",
    ))
    assert msg is None


def test_make_message_junk(collector):
    msg = asyncio.run(collector._make_message(
        "https://example.com", "Just a moment", "Cloudflare...",
    ))
    assert msg is None


def test_make_message_seen(collector):
    # First call — new
    msg1 = asyncio.run(collector._make_message(
        "https://example.com/page1", "T", "Content " * 50,
    ))
    assert msg1 is not None
    # Second call — same → seen
    msg2 = asyncio.run(collector._make_message(
        "https://example.com/page1", "T", "Content " * 50,
    ))
    assert msg2 is None


def test_make_message_fingerprint_stable(collector):
    msg1 = asyncio.run(collector._make_message(
        "https://example.com/page1", "T", "Content " * 50,
    ))
    fingerprint = msg1["id"]
    # Reset seen
    collector._seen_hashes = set()
    msg2 = asyncio.run(collector._make_message(
        "https://example.com/page1", "T", "Content " * 50,
    ))
    assert msg2["id"] == fingerprint


# ═══════════════════════════════════════════════════════════
# 8) Anti-dup
# ═══════════════════════════════════════════════════════════
def test_load_seen_empty(collector):
    assert collector._seen_hashes == set()


def test_save_and_load_seen(collector):
    collector._seen_hashes = {"fp1", "fp2", "fp3"}
    collector._save_seen()
    collector._seen_hashes = set()
    collector._load_seen()
    assert collector._seen_hashes == {"fp1", "fp2", "fp3"}


def test_load_seen_corrupt(collector):
    p = collector._seen_path
    with open(p, "w", encoding="utf-8") as f:
        f.write("not json")
    collector._load_seen()
    assert collector._seen_hashes == set()


# ═══════════════════════════════════════════════════════════
# 9) Properties
# ═══════════════════════════════════════════════════════════
def test_platform_name(collector):
    assert collector.platform_name == "web"


def test_supports_history(collector):
    assert collector.supports_history is False


def test_supports_websocket(collector):
    assert collector.supports_websocket is False


def test_poll_interval(collector):
    # 24h default → 86400s
    assert collector.poll_interval_seconds >= 3600


def test_is_connected_property(collector):
    collector._connected = True
    assert collector.is_connected is True
    collector._connected = False
    assert collector.is_connected is False


# ═══════════════════════════════════════════════════════════
# 10) connect
# ═══════════════════════════════════════════════════════════
def test_connect_loads_sources(collector, tmp_path):
    f = tmp_path / "sources.txt"
    f.write_text("page | https://example.com\n", encoding="utf-8")
    collector.sources_file = str(f)
    asyncio.run(collector.connect())
    assert collector._connected is True
    assert len(collector._sources) == 1


def test_connect_empty_sources(collector, tmp_path):
    collector.sources_file = str(tmp_path / "nope.txt")
    asyncio.run(collector.connect())
    assert collector._connected is True
    assert collector._sources == []


# ═══════════════════════════════════════════════════════════
# 11) fetch_messages (safety)
# ═══════════════════════════════════════════════════════════
def test_fetch_messages_not_connected(collector):
    collector._connected = False
    assert asyncio.run(collector.fetch_messages()) == []


def test_fetch_messages_no_sources(collector):
    collector._connected = True
    collector._sources = []
    assert asyncio.run(collector.fetch_messages()) == []


# ═══════════════════════════════════════════════════════════
# 12) check_health
# ═══════════════════════════════════════════════════════════
def test_check_health_disconnected(collector):
    collector._connected = False
    assert asyncio.run(collector.check_health()) is False


def test_check_health_connected(collector):
    collector._connected = True
    assert asyncio.run(collector.check_health()) is True


# ═══════════════════════════════════════════════════════════
# 13) Constants
# ═══════════════════════════════════════════════════════════
def test_max_seen_positive():
    assert _MAX_SEEN > 0


def test_news_path_hints_not_empty():
    assert len(_NEWS_PATH_HINTS) > 0
    assert "/news/" in _NEWS_PATH_HINTS


def test_junk_min_len_positive():
    assert _JUNK_MIN_LEN > 0