# tests/test_safe_brain.py
"""
🧪 تست SafeBrain — pytest-style (با mock برای AI).

نسخهٔ ۲.۰ (این نسخه):
  - 🐛 FIX: `_is_safe()` حالا `tuple[bool, str]` برمی‌گرداند (API ارتقا یافت).
           تست‌ها به `safe, reason = ...` به‌روز شدند.
  - 🐛 FIX: `_rule_parse()` حالا `tuple[str|None, str|None]` برمی‌گرداند.
  - 🎯 NEW: تست reason دقیق برای موارد ممنوعه (چرا مسدود شد؟).
  - 🎯 NEW: تست `full_access` (اگر در آینده اضافه شد).
  - ✅ حفظ همهٔ تست‌های پاس‌شدهٔ نسخهٔ ۱.
"""
from __future__ import annotations

import pytest


# ═══════════════════════════════════════════════════════════
# Fixture
# ═══════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def sb():
    """SafeBrain singleton (بدون AI واقعی)."""
    from core.safe_brain import get_safe_brain, reset_safe_brain

    reset_safe_brain()
    return get_safe_brain()


# ═══════════════════════════════════════════════════════════
# ═══════════ A) امنیت — API جدید (tuple) ═══════════
# ═══════════════════════════════════════════════════════════

def test_is_safe_help(sb):
    """_is_safe('help') باید (True, '') بده (امن، بدون دلیل)."""
    result = sb._is_safe("help")
    assert isinstance(result, tuple), (
        f"_is_safe باید tuple برگرداند، نه {type(result).__name__}"
    )
    assert len(result) == 2, f"tuple باید ۲ عضو داشته باشد: {result}"
    safe, reason = result
    assert safe is True, f"help باید امن باشد ولی safe={safe}"
    assert reason == "", f"help دلیلی برای مسدودسازی ندارد: {reason!r}"


def test_is_safe_status(sb):
    """_is_safe('وضعیت') باید (True, '') بده."""
    safe, reason = sb._is_safe("وضعیت")
    assert safe is True
    assert reason == ""


def test_is_safe_rm_rf(sb):
    """_is_safe('rm -rf /') باید (False, reason) بده."""
    safe, reason = sb._is_safe("rm -rf /")
    assert safe is False, "rm -rf / باید مسدود باشد"
    assert reason, "دلیل مسدودسازی نباید خالی باشد"
    assert "rm" in reason.lower() or "rf" in reason.lower() or "ممنوع" in reason, (
        f"reason باید به الگوی ممنوعه اشاره کند: {reason!r}"
    )


def test_is_safe_format_disk(sb):
    """_is_safe('format c:') باید (False, reason) بده."""
    safe, reason = sb._is_safe("format c:")
    assert safe is False
    assert reason, "دلیل مسدودسازی نباید خالی باشد"
    assert "format" in reason.lower() or "ممنوع" in reason


def test_is_safe_shutdown(sb):
    """_is_safe('shutdown now') باید (False, reason) بده."""
    safe, reason = sb._is_safe("shutdown now")
    assert safe is False
    assert reason
    assert "shutdown" in reason.lower() or "ممنوع" in reason


def test_is_safe_returns_consistent_type(sb):
    """همهٔ فراخوانی‌ها باید tuple[bool, str] برگردانند."""
    for text in ("help", "وضعیت", "rm -rf /", "shutdown", ""):
        result = sb._is_safe(text)
        assert isinstance(result, tuple), f"{text!r} → {result!r} (نه tuple)"
        assert len(result) == 2, f"{text!r} → {len(result)} عضو (انتظار ۲)"
        safe, reason = result
        assert isinstance(safe, bool), f"safe باید bool باشد، نه {type(safe)}"
        assert isinstance(reason, str), f"reason باید str باشد، نه {type(reason)}"


def test_is_safe_full_access_mode(sb):
    """در حالت full_access، الگوهای ممنوعه هم امن شوند."""
    if not getattr(sb, "full_access", False):
        pytest.skip("full_access=False — این تست معنادار نیست")

    safe, _ = sb._is_safe("rm -rf /")
    assert safe is True, "در full_access همه چیز امن است"


# ═══════════════════════════════════════════════════════════
# ═══════════ B) Rule Parsing — API جدید (tuple) ═══════════
# ════════════

def test_rule_parse_returns_tuple(sb):
    """_rule_parse باید tuple[str|None, str|None] برگرداند."""
    result = sb._rule_parse("help")
    assert isinstance(result, tuple), (
        f"_rule_parse باید tuple برگرداند، نه {type(result).__name__}"
    )
    assert len(result) == 2, f"tuple باید ۲ عضو داشته باشد: {result}"
    action, value = result
    assert action is None or isinstance(action, str), (
        f"action باید str یا None باشد: {action!r}"
    )
    assert value is None or isinstance(value, str), (
        f"value باید str یا None باشد: {value!r}"
    )


def test_rule_parse_help(sb):
    """_rule_parse('help') باید action='help' بده."""
    action, value = sb._rule_parse("help")
    assert action == "help", f"action={action!r} (انتظار 'help')"


def test_rule_parse_keyword_add(sb):
    """_rule_parse('کلیدواژه «کود» را اضافه کن') باید action + value بده."""
    action, value = sb._rule_parse("کلیدواژه «کود» را اضافه کن")
    assert action is not None, "action نباید None باشد"
    assert action == "add_keyword", f"action={action!r} (انتظار 'add_keyword')"
    assert value is not None, "value نباید None باشد"
    assert "کود" in value, f"value={value!r} (انتظار شامل 'کود')"


def test_rule_parse_unknown_text(sb):
    """متن ناشناخته باید action=None بده."""
    action, value = sb._rule_parse("یک متن کاملاً تصادفی xyz123")
    assert action is None, f"action={action!r} (انتظار None)"
    assert value is None


def test_rule_parse_empty(sb):
    """_rule_parse('') باید (None, None) بده."""
    action, value = sb._rule_parse("")
    assert action is None
    assert value is None


# ═══════════════════════════════════════════════════════════
# ═══════════ C) extract_value / platform_in ═══════════════════════════════════════════════════════════

def test_extract_value_keyword(sb):
    value = sb._extract_value("کلیدواژه «کود مایع» را اضافه کن")
    if value:
        assert "کود" in value or "مایع" in value


def test_platform_in(sb):
    result = sb._platform_in("ایتا کار می‌کنه؟")
    if result:
        assert isinstance(result, str)


def test_extract_value_empty(sb):
    result = sb._extract_value("")
    assert result is None or result == ""


def test_extract_value_no_quotes(sb):
    result = sb._extract_value("کلیدواژه اضافه کن")
    assert result is None or result == "" or isinstance(result, str)


# ═══════════════════════════════════════════════════════════
# ═══════════ D) process — ورودی نامعتبر ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_empty(sb):
    result = await sb.process("")
    assert result is None or result == ""


@pytest.mark.asyncio
async def test_process_none(sb):
    result = await sb.process(None)
    assert result is None or result == ""


@pytest.mark.asyncio
async def test_process_whitespace(sb):
    result = await sb.process("   ")
    assert result is None or result == ""


# ═══════════════════════════════════════════════════════════
# ═══════════ E) process — امنیت ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_blocks_dangerous(sb):
    result = await sb.process("rm -rf /")
    assert result is not None, "پاسخ نباید None باشد"
    assert "مسدود" in result or "blocked" in result.lower() or "ممنوع" in result, (
        f"پاسخ: {result[:100]}"
    )


@pytest.mark.asyncio
async def test_process_blocks_shutdown(sb):
    result = await sb.process("shutdown now")
    if result:
        assert "مسدود" in result or "blocked" in result.lower() or "ممنوع" in result


# ═══════════════════════════════════════════════════════════
# ═══════════ F) process — help (محلی) + cache ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_help(sb):
    result = await sb.process("help")
    assert result is not None
    assert len(result) > 0, "پاسخ خالی برگشت"


@pytest.mark.asyncio
async def test_process_help_cached(sb):
    r1 = await sb.process("help")
    r2 = await sb.process("help")
    assert r1 == r2, f"cache hit ندارد: {r1[:50]!r} vs {r2[:50]!r}"


# ═══════════════════════════════════════════════════════════
# ═══════════ G) get_stats / reset_stats ═══════════════════════════════════════════════════════════

def test_get_stats(sb):
    stats = sb.get_stats()
    assert isinstance(stats, dict)
    required = ["processed", "cached", "delegated", "fallback"]
    missing = [k for k in required if k not in stats]
    assert not missing, f"کلیدهای گم‌شده: {missing}"


def test_reset_stats(sb):
    sb.reset_stats()
    stats = sb.get_stats()
    for key in ["processed", "cached", "delegated", "fallback", "errors"]:
        if key in stats:
            assert stats[key] == 0, (
                f"بعد از reset، {key}={stats[key]} (انتظار 0)"
            )


# ═══════════ H) flush / close ═══════════

def test_flush(sb):
    sb.flush()


def test_close(sb):
    sb.close()


# ═══════════ I) can_call_api / budget ═══════════

def test_can_call_api(sb):
    result = sb._can_call_api()
    assert isinstance(result, bool)


def test_has_api_budget(sb):
    result = sb._has_api_budget()
    assert isinstance(result, bool)


# ═══════════ J) Components ═══════════

def test_components(sb):
    assert hasattr(sb, "cache")
    assert hasattr(sb, "project_memory")
    assert hasattr(sb, "global_memory")
    assert sb.cache is not None
    assert sb.project_memory is not None
    assert sb.global_memory is not None
    


# ═══════════════════════════════════════════════════════════
# K) ONLINE TESTS — SafeBrain با AI واقعی
# ═══════════════════════════════════════════════════════════
# این تست‌ها به شبکه وابسته‌اند و با -m "not network" skip می‌شوند.
# ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.network
async def test_online_process_real_question(sb):
    """آنلاین: SafeBrain.process با سؤال واقعی — پاسخ یا delegate."""
    # ⚠️ _can_call_api همگام (sync) است — await ندارد
    if not sb._can_call_api():
        pytest.skip("SafeBrain نمی‌تواند به API وصل شود")

    result = await sb.process("۵ تا ۵ چند می‌شود؟")

    # ─── نتیجه: یا پاسخ واقعی، یا None (delegate) ───
    if result is None:
        # delegate شده — قابل قبول (AdminHandler باید جواب دهد)
        return

    assert isinstance(result, str), (
        f"پاسخ باید str باشد، نه {type(result).__name__}"
    )
    assert len(result) > 0, "پاسخ خالی"

    # ─── چک: پاسخ «مسدود» نباشد (چون سؤال امن است) ───
    assert "مسدود" not in result, (
        f"سؤال امن مسدود شد: {result[:100]}"
    )


@pytest.mark.asyncio
@pytest.mark.network
async def test_online_cache_hit_real(sb):
    """آنلاین: cache hit در SafeBrain — بار دوم سریع‌تر."""
    import time

    # ⚠️ _can_call_api همگام (sync) است — await ندارد
    if not sb._can_call_api():
        pytest.skip("SafeBrain نمی‌تواند به API وصل شود")

    question = f"تست cache {time.time_ns()}"

    # ─── بار اول: کند (AI) ───
    t0 = time.monotonic()
    r1 = await sb.process(question)
    t1 = (time.monotonic() - t0) * 1000

    # ─── بار دوم: سریع (cache) ───
    t0 = time.monotonic()
    r2 = await sb.process(question)
    t2 = (time.monotonic() - t0) * 1000

    # ─── cache hit باید >= ۲ برابر سریع‌تر باشد ───
    if r1 is not None and r1 == r2:
        assert t2 < t1 / 2, (
            f"cache hit بسیار کند: t1={t1:.0f}ms t2={t2:.0f}ms"
        )