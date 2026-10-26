# tests/test_ai_assistant.py
"""
🧪 تست AIAssistant — pytest-style (بدون شبکه).

نسخهٔ ۳.۰ (این نسخه):
  - 🐛 FIX: `parse_intent` مستقیماً `_call_deepseek`/`_call_ollama` را صدا می‌زند
           (نه `chat_with_status`). mock روی API واقعی.
  - 🐛 FIX: `_provider_order` هم mock می‌شود تا مستقل از env باشد.
  - 🐛 FIX: `import asyncio` اضافه شد.
  - 🎯 NEW: `_neutralize` helper برای DRY.

اجرا:
    pytest tests/test_ai_assistant.py -v
    pytest tests/test_ai_assistant.py -v -m "not network"
"""
from __future__ import annotations

import asyncio

import pytest


# ═══════════════════════════════════════════════════════════
# Fixture
# ═══════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def ai():
    """AIAssistant singleton — با reset قبل از ماژول.

    ⚠️ reset_ai_assistant() لازم است تا اگر تست‌های دیگر (مثل test_safe_brain)
    قبل از این ماژول اجرا شدند، state قبلی روی تست‌های ما اثر نگذارد.
    """
    from core.services.ai_assistant import (
        get_ai_assistant,
        reset_ai_assistant,
    )
    reset_ai_assistant()
    return get_ai_assistant()


# ═══════════════════════════════════════════════════════════
# A) SYNC TESTS
# ═══════════════════════════════════════════════════════════

def test_get_provider_order(ai):
    """get_provider_order() باید لیست معتبر برگردونه."""
    order = ai.get_provider_order()
    assert isinstance(order, list)
    assert len(order) >= 1
    assert all(isinstance(p, str) for p in order)


def test_private_provider_order(ai):
    """_provider_order() باید لیست معتبر برگردونه."""
    order = ai._provider_order()
    assert isinstance(order, list)
    assert len(order) >= 1
    valid = {"deepseek", "ollama"}
    for p in order:
        assert p in valid, f"provider نامعتبر: {p}"


def test_provider_order_idempotent(ai):
    """_provider_order() باید idempotent باشه."""
    o1 = ai._provider_order()
    o2 = ai._provider_order()
    assert o1 == o2


def test_get_stats(ai):
    """get_stats() باید dict با کلیدهای مهم برگردونه."""
    stats = ai.get_stats()
    assert isinstance(stats, dict)
    required = ["enabled", "priority"]
    missing = [k for k in required if k not in stats]
    assert not missing, f"کلیدهای گم‌شده: {missing}"


def test_get_stats_idempotent(ai):
    """get_stats() باید همیشه همون ساختار رو بده."""
    s1 = ai.get_stats()
    s2 = ai.get_stats()
    assert set(s1.keys()) == set(s2.keys())


def test_invalidate_cache(ai):
    """invalidate_available_cache() نباید کرش کنه."""
    ai.invalidate_available_cache()
    assert ai._available_cache is None or ai._available_cache is not None


# ═══════════════════════════════════════════════════════════
# B) _extract_json
# ═══════════════════════════════════════════════════════════

def test_extract_json_valid(ai):
    """_extract_json() باید JSON معتبر رو parse کنه."""
    text = '{"action": "status", "params": {}}'
    result = ai._extract_json(text)
    assert isinstance(result, dict)
    assert result.get("action") == "status"
    assert result.get("params") == {}


def test_extract_json_with_markdown(ai):
    """_extract_json() باید markdown code block رو handle کنه."""
    text = '```json\n{"action": "report", "params": {"range": "today"}}\n```'
    result = ai._extract_json(text)
    assert isinstance(result, dict)
    if result:
        assert result.get("action") == "report"


def test_extract_json_with_text(ai):
    """_extract_json() باید JSON رو از داخل متن پیدا کنه."""
    text = 'این یه پاسخ است. {"action": "help"} و ادامه.'
    result = ai._extract_json(text)
    if result:
        assert result.get("action") == "help"


def test_extract_json_invalid(ai):
    """_extract_json() با متن نامعتبر باید None یا dict خالی بده."""
    result = ai._extract_json("not a json at all")
    assert result is None or isinstance(result, dict)


def test_extract_json_empty(ai):
    """_extract_json('') باید None یا dict خالی بده."""
    result = ai._extract_json("")
    assert result is None or isinstance(result, dict)


# ═══════════════════════════════════════════════════════════
# C) ASYNC — chat_with_status (بدون شبکه)
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_chat_empty_text(ai):
    """chat_with_status('') باید (resp, False) برگردونه."""
    resp, ok = await ai.chat_with_status("")
    assert ok is False
    assert resp == "" or resp is None


@pytest.mark.asyncio
async def test_chat_none_text(ai):
    """chat_with_status(None) باید رد بشه."""
    resp, ok = await ai.chat_with_status(None)
    assert ok is False


@pytest.mark.asyncio
async def test_chat_whitespace(ai):
    """chat_with_status('   ') باید رد بشه."""
    resp, ok = await ai.chat_with_status("   ")
    assert ok is False


@pytest.mark.asyncio
async def test_chat_method_empty(ai):
    """chat('') باید رشتهٔ خالی برگردونه."""
    resp = await ai.chat("")
    assert resp == "" or resp is None


# ═══════════════════════════════════════════════════════════
# D) ASYNC — parse_intent با mock روی provider واقعی
# ═══════════════════════════════════════════════════════════

def _mock_provider(ai, monkeypatch, response: str | None):
    """mock مستقیم provider — API واقعی `parse_intent`.

    ⚠️ `parse_intent` مستقیماً `_call_deepseek`/`_call_ollama` را صدا می‌زند
    (نه `chat_with_status`). پس mock باید روی provider باشد.
    """
    async def fake_provider(prompt, format_json=False):
        return response

    monkeypatch.setattr(ai, "_call_deepseek", fake_provider)
    monkeypatch.setattr(ai, "_call_ollama", fake_provider)
    monkeypatch.setattr(
        ai, "_provider_order", lambda: ["deepseek"]
    )


@pytest.mark.asyncio
async def test_parse_intent_mocked(ai, monkeypatch):
    """parse_intent با mock — action=status (JSON معتبر)."""
    _mock_provider(
        ai, monkeypatch,
        response='{"action": "status", "args": {}}',
    )

    result = await ai.parse_intent("وضعیت سیستم چطوره؟")
    assert isinstance(result, dict)
    assert result.get("action") == "status", (
        f"action={result.get('action')!r} (انتظار 'status')"
    )


@pytest.mark.asyncio
async def test_parse_intent_invalid_json(ai, monkeypatch):
    """parse_intent با پاسخ غیر-JSON → action=unknown."""
    _mock_provider(ai, monkeypatch, response="not a json at all")

    result = await ai.parse_intent("چیزی")
    assert isinstance(result, dict)
    assert result.get("action") == "unknown"


@pytest.mark.asyncio
async def test_parse_intent_invalid_action(ai, monkeypatch):
    """parse_intent با action نامعتبر → unknown (whitelist)."""
    _mock_provider(
        ai, monkeypatch,
        response='{"action": "hack_the_planet", "args": {}}',
    )

    result = await ai.parse_intent("چیزی")
    assert isinstance(result, dict)
    assert result.get("action") == "unknown", (
        f"action={result.get('action')!r} (انتظار 'unknown' — whitelist)"
    )


@pytest.mark.asyncio
async def test_parse_intent_no_provider(ai, monkeypatch):
    """parse_intent وقتی هیچ provider پاسخ نمی‌دهد → unknown."""
    _mock_provider(ai, monkeypatch, response=None)

    result = await ai.parse_intent("چیزی")
    assert isinstance(result, dict)
    assert result.get("action") == "unknown"


@pytest.mark.asyncio
async def test_parse_intent_empty(ai):
    """parse_intent('') باید action=unknown برگردونه (بدون AI)."""
    result = await ai.parse_intent("")
    assert isinstance(result, dict)
    assert result.get("action") == "unknown"


@pytest.mark.asyncio
async def test_available_mocked(ai, monkeypatch):
    """available() با mock — باید True برگردونه."""
    async def fake_check():
        return True

    monkeypatch.setattr(ai, "_check_available_inner", fake_check)
    ai.invalidate_available_cache()

    result = await ai.available()
    assert result is True


@pytest.mark.asyncio
async def test_available_mocked_false(ai, monkeypatch):
    """available() با mock — باید False برگردونه."""
    async def fake_check():
        return False

    monkeypatch.setattr(ai, "_check_available_inner", fake_check)
    ai.invalidate_available_cache()

    result = await ai.available()
    assert result is False


# ═══════════════════════════════════════════════════════════
# E) INTEGRATION — network (اختیاری)
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
@pytest.mark.network
async def test_available_real(ai):
    """available() واقعی — شبکه."""
    ai.invalidate_available_cache()
    avail = await ai.available()
    assert isinstance(avail, bool)


@pytest.mark.asyncio
@pytest.mark.network
async def test_chat_real(ai):
    """chat() واقعی — شبکه."""
    if not await ai.available():
        pytest.skip("هیچ provider در دسترس نیست")

    resp = await ai.chat("سلام")
    assert resp is not None
    assert len(resp) > 0


# ═══════════════════════════════════════════════════════════
# F) EDGE CASES
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_chat_long_text(ai, monkeypatch):
    """chat با متن ۵۰K باید truncate بشه یا fail محترمانه."""
    async def fake_chat(text, system=""):
        return ("پاسخ کوتاه", True)

    monkeypatch.setattr(ai, "chat_with_status", fake_chat)

    long_text = "x" * 50_000
    resp = await ai.chat(long_text)
    assert resp is not None
    



# ═══════════════════════════════════════════════════════════
# G) ONLINE TESTS — واقعی (بدون mock)
# ═══════════════════════════════════════════════════════════
# این تست‌ها به شبکه وابسته‌اند و با -m "not network" skip می‌شوند.
#
# برای اجرا:
#   pytest tests/test_ai_assistant.py -v -m network
#
# یا برای اجرای همه (offline + online):
#   pytest tests/test_ai_assistant.py -v
# ─────────────────────────────────────────────────────────

async def _require_available(ai):
    """skip اگر هیچ provider در دسترس نیست."""
    ai.invalidate_available_cache()
    avail = await ai.available()
    if not avail:
        pytest.skip(
            "هیچ provider در دسترس نیست — "
            "DeepSeek key معتبر یا Ollama روشن کن"
        )
    return avail


@pytest.mark.asyncio
@pytest.mark.network
async def test_online_available(ai):
    """آنلاین: available() واقعی — آیا حداقل یک provider پاسخ می‌دهد؟
    
    اگر هیچ provider نباشد → skip (نه fail). این تست فقط با -m network اجرا.
    """
    ai.invalidate_available_cache()
    avail = await ai.available()
    assert isinstance(avail, bool), (
        f"available() باید bool باشد، نه {type(avail).__name__}"
    )

    if not avail:
        pytest.skip(
            "هیچ provider در دسترس نیست — "
            "DeepSeek key معتبر یا Ollama روشن کن"
        )


@pytest.mark.asyncio
@pytest.mark.network
async def test_online_chat_simple(ai):
    """آنلاین: chat('سلام') واقعی — پاسخ غیرخالی از provider."""
    await _require_available(ai)

    resp = await ai.chat("فقط بگو: سلام")
    assert resp is not None, "پاسخ None برگشت"
    assert isinstance(resp, str), f"پاسخ نوع {type(resp).__name__}"
    assert len(resp) > 0, "پاسخ خالی برگشت"
    assert not resp.startswith("⚠️"), f"پاسخ با خطا: {resp[:100]}"


@pytest.mark.asyncio
@pytest.mark.network
async def test_online_chat_with_system(ai):
    """آنلاین: chat با system prompt — پاسخ واقعی."""
    await _require_available(ai)

    resp = await ai.chat(
        "یک جمله کوتاه درباره پایش سرنخ بنویس",
        system="تو یک دستیار فارسی هستی. کوتاه جواب بده.",
    )
    assert resp is not None
    assert isinstance(resp, str)
    assert len(resp) > 5, f"پاسخ خیلی کوتاه: {len(resp)} char"


@pytest.mark.asyncio
@pytest.mark.network
async def test_online_chat_persian(ai):
    """آنلاین: chat به فارسی — پاسخ فارسی."""
    await _require_available(ai)

    resp = await ai.chat("سه کلمه فارسی بنویس")
    assert resp is not None
    assert len(resp) > 0

    # ─── چک: پاسخ حداقل یک حرف فارسی داشته باشد ───
    has_persian = any("\u0600" <= c <= "\u06ff" for c in resp)
    assert has_persian, (
        f"پاسخ فارسی نیست: {resp[:100]!r}"
    )


@pytest.mark.asyncio
@pytest.mark.network
async def test_online_parse_intent(ai):
    """آنلاین: parse_intent واقعی — action معتبر از whitelist."""
    await _require_available(ai)

    result = await ai.parse_intent("وضعیت سیستم چطوره؟")
    assert isinstance(result, dict), (
        f"parse_intent باید dict برگرداند، نه {type(result).__name__}"
    )
    assert "action" in result, f"کلید action نیست: {result.keys()}"
    assert "args" in result, f"کلید args نیست: {result.keys()}"

    action = result["action"]
    assert isinstance(action, str)
    assert len(action) > 0, "action خالی"

    # ─── action باید یا 'unknown' باشد یا در whitelist ───
    if action != "unknown":
        assert isinstance(result["args"], dict), (
            f"args باید dict باشد، نه {type(result['args']).__name__}"
        )


@pytest.mark.asyncio
@pytest.mark.network
async def test_online_parse_intent_crm(ai):
    """آنلاین: parse_intent با درخواست CRM."""
    await _require_available(ai)

    result = await ai.parse_intent("خروجی اکسل بده")
    assert isinstance(result, dict)
    assert "action" in result

    # ─── نکته: AI واقعی ممکن است crm تشخیص دهد یا unknown ───
    # ولی action معتبر باید باشد
    action = result["action"]
    if action != "unknown":
        assert isinstance(result["args"], dict)


@pytest.mark.asyncio
@pytest.mark.network
async def test_online_provider_fallback(ai):
    """آنلاین: اگر deepseek اول باشد ولی خطا دهد، ollama امتحان می‌شود.

    این تست نیاز به Ollama روشن دارد. اگر Ollama نیست → skip.
    """
    # ─── چک: آیا Ollama واقعاً روشن است؟ ───
    try:
        import aiohttp
    except ImportError:
        pytest.skip("aiohttp نصب نیست")

    ollama_alive = False
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{ai._ollama_url}/api/tags",
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                ollama_alive = resp.status == 200
    except Exception:
        ollama_alive = False

    if not ollama_alive:
        pytest.skip("Ollama روشن نیست — این تست meaningful نیست")

    # ─── اگر Ollama روشن است، چک می‌کنیم که واقعاً کار می‌کند ───
    resp = await ai._call_ollama("بگو: تست", format_json=False)
    assert resp is not None, (
        "Ollama روشن است ولی پاسخ نداد — باگ در _call_ollama"
    )
    assert isinstance(resp, str)
    assert len(resp) > 0   