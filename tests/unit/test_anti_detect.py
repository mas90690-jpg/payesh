# tests/unit/test_anti_detect.py
"""
Unit tests for collectors/anti_detect.py — v2.0 (from scratch).

Coverage:
  ─── random_ua ───
  - returns a string
  - always from _REAL_UAS
  - returns different values across calls (probabilistically)
  - format looks like a real UA

  ─── apply_stealth ───
  - calls add_init_script with fingerprint script
  - calls set_extra_http_headers
  - calls set_viewport_size with random bounds
  - viewport is within expected range
  - handles missing playwright_stealth gracefully
  - handles page with no methods (defensive)
  - accepts custom rng (deterministic viewport)
  - headers contain Accept-Language + DNT
  - init script contains all critical patches:
      * navigator.webdriver
      * navigator.languages
      * navigator.plugins
      * navigator.hardwareConcurrency
      * navigator.deviceMemory
      * window.chrome
      * permissions.query
      * WebGL vendor
  - init script patches value range (plugins not empty)

Run:
  pytest tests/unit/test_anti_detect.py -v
"""
from __future__ import annotations

import asyncio
import random
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collectors.anti_detect import (
    _REAL_UAS,
    apply_stealth,
    random_ua,
)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════
def _mock_page():
    """Mock Playwright page with all methods used by apply_stealth."""
    page = MagicMock()
    page.add_init_script = AsyncMock()
    page.set_extra_http_headers = AsyncMock()
    page.set_viewport_size = AsyncMock()
    return page


# ═══════════════════════════════════════════════════════════
# 1) _REAL_UAS sanity
# ═══════════════════════════════════════════════════════════
def test_real_uas_not_empty():
    assert len(_REAL_UAS) >= 3


def test_real_uas_look_like_chrome():
    for ua in _REAL_UAS:
        assert "Mozilla/5.0" in ua
        assert "Chrome/" in ua
        assert "Safari/537.36" in ua
        assert "Windows NT 10.0" in ua


def test_real_uas_are_distinct():
    assert len(_REAL_UAS) == len(set(_REAL_UAS))


# ═══════════════════════════════════════════════════════════
# 2) random_ua
# ═══════════════════════════════════════════════════════════
def test_random_ua_returns_string():
    ua = random_ua()
    assert isinstance(ua, str)


def test_random_ua_from_list():
    ua = random_ua()
    assert ua in _REAL_UAS


def test_random_ua_not_empty():
    assert len(random_ua()) > 50


def test_random_ua_reproducible_with_seed():
    """With same seed, random.choice returns same UA."""
    random.seed(42)
    a = random_ua()
    random.seed(42)
    b = random_ua()
    assert a == b


def test_random_ua_varies():
    """Over many calls, more than one UA should appear."""
    seen = set()
    random.seed(12345)
    for _ in range(30):
        seen.add(random_ua())
    assert len(seen) >= 2


# ═══════════════════════════════════════════════════════════
# 3) apply_stealth — basic calls
# ═══════════════════════════════════════════════════════════
def test_apply_stealth_calls_add_init_script():
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    page.add_init_script.assert_awaited_once()


def test_apply_stealth_calls_set_headers():
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    page.set_extra_http_headers.assert_awaited_once()


def test_apply_stealth_calls_set_viewport():
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    page.set_viewport_size.assert_awaited_once()


# ═══════════════════════════════════════════════════════════
# 4) init script content — all patches present
# ═══════════════════════════════════════════════════════════
def _get_init_script(page: MagicMock) -> str:
    """Extract the init script string from the mock's call."""
    assert page.add_init_script.await_count == 1
    args, _ = page.add_init_script.call_args
    return args[0]


def test_init_script_patches_webdriver():
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    script = _get_init_script(page)
    assert "navigator" in script
    assert "webdriver" in script


def test_init_script_patches_languages():
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    script = _get_init_script(page)
    assert "languages" in script
    assert "fa-IR" in script


def test_init_script_patches_plugins():
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    script = _get_init_script(page)
    assert "plugins" in script
    assert "Chrome PDF Plugin" in script


def test_init_script_patches_hardware_concurrency():
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    script = _get_init_script(page)
    assert "hardwareConcurrency" in script


def test_init_script_patches_device_memory():
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    script = _get_init_script(page)
    assert "deviceMemory" in script


def test_init_script_patches_window_chrome():
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    script = _get_init_script(page)
    assert "window.chrome" in script
    assert "runtime" in script


def test_init_script_patches_permissions():
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    script = _get_init_script(page)
    assert "permissions" in script
    assert "notifications" in script


def test_init_script_patches_webgl():
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    script = _get_init_script(page)
    assert "WebGL" in script or "getParameter" in script
    assert "Intel" in script


# ═══════════════════════════════════════════════════════════
# 5) Headers
# ═══════════════════════════════════════════════════════════
def _get_headers(page: MagicMock) -> dict:
    assert page.set_extra_http_headers.await_count == 1
    args, _ = page.set_extra_http_headers.call_args
    return args[0]


def test_headers_have_accept_language():
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    headers = _get_headers(page)
    assert "Accept-Language" in headers
    assert "fa-IR" in headers["Accept-Language"]


def test_headers_have_dnt():
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    headers = _get_headers(page)
    assert headers.get("DNT") == "1"


def test_headers_have_accept_encoding():
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    headers = _get_headers(page)
    assert "Accept-Encoding" in headers
    assert "gzip" in headers["Accept-Encoding"]


# ═══════════════════════════════════════════════════════════
# 6) Viewport randomness
# ═══════════════════════════════════════════════════════════
def _get_viewport(page: MagicMock) -> dict:
    assert page.set_viewport_size.await_count == 1
    args, _ = page.set_viewport_size.call_args
    return args[0]


def test_viewport_has_width_height():
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    vp = _get_viewport(page)
    assert "width" in vp
    assert "height" in vp


def test_viewport_width_in_range():
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    vp = _get_viewport(page)
    assert 1280 <= vp["width"] <= 1600


def test_viewport_height_in_range():
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    vp = _get_viewport(page)
    assert 720 <= vp["height"] <= 1000


def test_viewport_uses_custom_rng():
    """With seeded rng, viewport should be deterministic."""
    page1 = _mock_page()
    asyncio.run(apply_stealth(page1, rng=random.Random(42)))
    vp1 = _get_viewport(page1)

    page2 = _mock_page()
    asyncio.run(apply_stealth(page2, rng=random.Random(42)))
    vp2 = _get_viewport(page2)

    assert vp1 == vp2


def test_viewport_random_between_calls():
    """Different rngs → potentially different viewports."""
    viewports = set()
    for seed in (1, 2, 3, 4, 5):
        page = _mock_page()
        asyncio.run(apply_stealth(page, rng=random.Random(seed)))
        vp = _get_viewport(page)
        viewports.add((vp["width"], vp["height"]))
    # At least 2 distinct viewports across 5 seeds
    assert len(viewports) >= 2


# ═══════════════════════════════════════════════════════════
# 7) Playwright-stealth optional import
# ═══════════════════════════════════════════════════════════
def test_apply_stealth_no_playwright_stealth():
    """If playwright_stealth isn't installed, no crash."""
    page = _mock_page()

    with patch.dict("sys.modules", {"playwright_stealth": None}):
        asyncio.run(apply_stealth(page))

    # Still calls init script + headers + viewport
    page.add_init_script.assert_awaited_once()
    page.set_extra_http_headers.assert_awaited_once()
    page.set_viewport_size.assert_awaited_once()


def test_apply_stealth_playwright_stealth_raises():
    """If playwright_stealth exists but raises, we still continue."""
    page = _mock_page()

    fake_mod = MagicMock()
    fake_mod.stealth_async = AsyncMock(side_effect=RuntimeError("boom"))

    with patch.dict("sys.modules", {"playwright_stealth": fake_mod}):
        asyncio.run(apply_stealth(page))

    # Still continued to init script
    page.add_init_script.assert_awaited_once()


# ═══════════════════════════════════════════════════════════
# 8) Defensive — page with missing methods
# ═══════════════════════════════════════════════════════════
def test_apply_stealth_page_no_set_headers():
    """If set_extra_http_headers raises, no crash."""
    page = _mock_page()
    page.set_extra_http_headers = AsyncMock(
        side_effect=AttributeError("no method")
    )
    asyncio.run(apply_stealth(page))
    # add_init_script still called
    page.add_init_script.assert_awaited_once()


def test_apply_stealth_page_no_set_viewport():
    """If set_viewport_size raises, no crash."""
    page = _mock_page()
    page.set_viewport_size = AsyncMock(
        side_effect=AttributeError("no method")
    )
    asyncio.run(apply_stealth(page))
    page.add_init_script.assert_awaited_once()


def test_apply_stealth_page_no_add_init_script():
    """If add_init_script raises, no crash (stealth optional)."""
    page = _mock_page()
    page.add_init_script = AsyncMock(
        side_effect=RuntimeError("boom")
    )
    # Should not raise
    asyncio.run(apply_stealth(page))


# ═══════════════════════════════════════════════════════════
# 9) Integration with rng=None (default)
# ═══════════════════════════════════════════════════════════
def test_apply_stealth_rng_none_works():
    page = _mock_page()
    asyncio.run(apply_stealth(page, rng=None))
    vp = _get_viewport(page)
    assert 1280 <= vp["width"] <= 1600
    assert 720 <= vp["height"] <= 1000


# ═══════════════════════════════════════════════════════════
# 10) Idempotency
# ═══════════════════════════════════════════════════════════
def test_apply_stealth_called_twice():
    """Calling twice doesn't crash (may re-apply)."""
    page = _mock_page()
    asyncio.run(apply_stealth(page))
    asyncio.run(apply_stealth(page))
    # Both calls happened
    assert page.add_init_script.await_count == 2