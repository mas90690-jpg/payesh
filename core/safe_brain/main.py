"""SafeBrain — گارد امن + کش + حافظه."""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from config import AI as AI_CONFIG
from config import STORAGE_DIR
from core.adapters.command_parser import (
    extract_value as _extract_value_fn,
)

# ⭐ پارسر مشترک
from core.adapters.command_parser import (
    parse_command as _parse_command,
)
from core.adapters.command_parser import (
    platform_in as _platform_in_fn,
)
from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)
from core.safe_brain.constants import (
    _BRAIN_FAIL_RETRY_S,
    _LOG_PREVIEW_LEN,
    _SLOW_ACTION_MS,
    BLOCKED_PATTERNS_REGEX,
    BLOCKED_SUBSTRINGS,
    MAX_API_CALLS_PER_HOUR,
    SAFE_ACTIONS,
    SAFE_ACTIONS_HANDLED_LOCALLY,
    _read_custom_kw_dict,
    _write_custom_kw_dict,
)
from core.safe_brain.storage import SafeCache, SafeMemory


# ═══════════════════════════════════════════
# Sentinel برای delegation (جلوگیری از اشتباه با پاسخ خالی)
# ═══════════════════════════════════════════
class _DelegateType:
    """Type منحصربه‌فرد برای delegation به لایهٔ بالاتر."""
    __slots__ = ()
    def __repr__(self) -> str:
        return "<DELEGATE>"


_DELEGATE = _DelegateType()


# ═══════════════════════════════════════════
# تنظیمات (از env)
# ═══════════════════════════════════════════
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


_AI_TIMEOUT_S = _env_float("SAFE_BRAIN_AI_TIMEOUT_S", 30.0)


# ═══════════════════════════════════════════
# SafeBrain
# ═══════════════════════════════════════════
class SafeBrain:
    """مغز امن ربات — لایهٔ نازک روی Brain.

    نسخهٔ ۵:
      - 🔴 FIX: rate limit روی DeepSeek حالا در `_try_ai` اعمال می‌شود
        (قبلاً DeepSeek بی‌محدودیت صدا زده می‌شد — `SAFE_BRAIN_MAX_API_HOUR`
        روی AIAssistant بی‌اثر بود).
      - 🔴 FIX: حالا از `chat_with_status()` استفاده می‌شود تا
        `is_fallback` دقیق تشخیص داده شود (نه با prefix substring
        که dead code داشت).
      - 🔴 FIX: پاسخ fallback حالا کش نمی‌شود.
      - 🟠 FIX: در full_access، rate limit AI هم شمرده می‌شود.
      - ✅ حفظ همهٔ FIXهای نسخهٔ ۴.
    """

    def __init__(
        self,
        api_key: str | None = None,
        brain: Any | None = None,
        full_access: bool = False,
        use_api_memory: bool = False,
        memory_api_url: str = "",
        memory_api_key: str = "",
    ):
        self.api_key = api_key or os.environ.get(
            "DEEPSEEK_API_KEY", ""
        )
        self.full_access = full_access

        self.project_memory = SafeMemory(
            "project_memory", max_entries=200
        )
        self.global_memory = SafeMemory(
            "global_memory", max_entries=500
        )
        self.cache = SafeCache("safe_brain_cache", ttl_hours=48)

        self._api_calls_this_hour = 0
        self._api_hour_start = time.monotonic()
        self._api_lock = threading.Lock()

        self._brain_instance: Any | None = None
        self._brain_failed_at: float = 0.0
        self._brain_lock = threading.Lock()

        self._ai_assistant: Any | None = None
        self._ai_lock = threading.Lock()

        self.use_api_memory = use_api_memory
        self.memory_api_url = memory_api_url
        self.memory_api_key = memory_api_key

        self.stats: dict[str, Any] = {
            "processed": 0,
            "cached": 0,
            "api_calls": 0,
            "api_blocked": 0,
            "blocked": 0,
            "errors": 0,
            "delegated": 0,
            "fallback": 0,
            "started_at": datetime.now().isoformat(),
        }

        log_success(
            f"✅ [SAFEBRAIN:init] آماده | "
            f"api_key={'✅' if self.api_key else '❌'} | "
            f"full_access={'✅' if full_access else '❌'} | "
            f"max_api_hour={MAX_API_CALLS_PER_HOUR} | "
            f"ai_timeout={_AI_TIMEOUT_S}s | "
            f"memory: project={self.project_memory.count} "
            f"global={self.global_memory.count} | "
            f"cache={self.cache.size}"
        )

    # ═══════════════════════════════════════════
    # lazy init
    # ═══════════════════════════════════════════
    def _get_brain(self):
        with self._brain_lock:
            if self._brain_instance is not None:
                return self._brain_instance

            if self._brain_failed_at > 0:
                age = time.monotonic() - self._brain_failed_at
                if age < _BRAIN_FAIL_RETRY_S:
                    return None

            try:
                t0 = time.monotonic()
                from core.brain import get_brain
                self._brain_instance = get_brain(
                    api_key=self.api_key,
                    full_access=self.full_access,
                    use_api_memory=self.use_api_memory,
                    memory_api_url=self.memory_api_url,
                    memory_api_key=self.memory_api_key,
                )
                elapsed_ms = (time.monotonic() - t0) * 1000
                self._brain_failed_at = 0.0
                log_success(
                    f"✅ [SAFEBRAIN:brain] اتصال | "
                    f"{elapsed_ms:.0f}ms"
                )
                return self._brain_instance
            except Exception as e:
                self._brain_failed_at = time.monotonic()
                log_error(
                    f"❌ [SAFEBRAIN:brain] اتصال | "
                    f"{type(e).__name__}: {e} — retry تا "
                    f"{_BRAIN_FAIL_RETRY_S}s دیگر"
                )
                return None

    def _get_ai(self):
        with self._ai_lock:
            if self._ai_assistant is not None:
                return self._ai_assistant
            try:
                t0 = time.monotonic()
                from core.services.ai_assistant import get_ai_assistant
                self._ai_assistant = get_ai_assistant()
                elapsed_ms = (time.monotonic() - t0) * 1000
                log_info(
                    f"🔗 [SAFEBRAIN:ai] اتصال | "
                    f"{elapsed_ms:.0f}ms"
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [SAFEBRAIN:ai] در دسترس نیست | "
                    f"{type(e).__name__}: {e}"
                )
                return None
            return self._ai_assistant

    # ═══════════════════════════════════════════
    # rate limit
    # ═══════════════════════════════════════════
    def _can_call_api(self) -> bool:
        """
        چک محدودیت ساعتی تماس‌های API.

        🔴 FIX: حالا حتی در full_access هم آمار شمرده می‌شود
        (ولی محدودیت اعمال نمی‌شود).
        """
        with self._api_lock:
            now = time.monotonic()
            if now - self._api_hour_start > 3600:
                log_info(
                    f"🔄 [SAFEBRAIN:api] reset پنجرهٔ ساعتی | "
                    f"قبل: {self._api_calls_this_hour} تماس"
                )
                self._api_calls_this_hour = 0
                self._api_hour_start = now

            # آمار همیشه افزایش می‌یابد
            self._api_calls_this_hour += 1
            self.stats["api_calls"] += 1

            # در full_access محدودیت اعمال نمی‌شود
            if self.full_access:
                return True

            if self._api_calls_this_hour > MAX_API_CALLS_PER_HOUR:
                self.stats["api_blocked"] += 1
                log_warning(
                    f"⚠️ [SAFEBRAIN:api] محدودیت ساعتی "
                    f"({MAX_API_CALLS_PER_HOUR}) رد شد | "
                    f"used={self._api_calls_this_hour}"
                )
                return False
            return True

    # ═══════════════════════════════════════════
    # security
    # ═══════════════════════════════════════════
    def _is_safe(self, text: str) -> tuple[bool, str]:
        if self.full_access:
            return True, ""
        if not text:
            return True, ""

        low = text.lower()

        for pat in BLOCKED_SUBSTRINGS:
            if pat.lower() in low:
                return False, f"الگوی ممنوعه: {pat}"

        for pat in BLOCKED_PATTERNS_REGEX:
            if re.search(pat, low, re.IGNORECASE):
                return False, f"الگوی ممنوعه: {pat}"

        return True, ""

    # ═══════════════════════════════════════════
    # process — نقطهٔ ورود اصلی
    # ═══════════════════════════════════════════
    async def process(self, text: str) -> str | None:
        """
        پردازش ورودی کاربر.

        Returns:
            str: پاسخ آماده
            None: واگذاری به لایهٔ بالاتر (AdminHandler)
        """
        text = (text or "").strip()
        if not text:
            return ""

        t_all = time.monotonic()
        self.stats["processed"] += 1
        log_info(
            f"▶️ [SAFEBRAIN:process] #{self.stats['processed']} | "
            f"len={len(text)} | full_access={self.full_access} | "
            f"in={text[:_LOG_PREVIEW_LEN]!r}"
        )

        try:
            result = await self._process_inner(text, t_all)
            # 🔴 FIX: تبدیل _DELEGATE به None
            if isinstance(result, _DelegateType):
                return None
            return result
        except Exception as e:
            self.stats["errors"] += 1
            elapsed_ms = (time.monotonic() - t_all) * 1000
            log_error(
                f"❌ [SAFEBRAIN:process] {type(e).__name__}: {e} | "
                f"{elapsed_ms:.0f}ms — fallback"
            )
            self.stats["fallback"] += 1
            return self._fallback_response(text)

    async def _process_inner(
        self, text: str, t_all: float
    ) -> Any:  # str | _DelegateType
        # ۱) امنیت
        safe, reason = self._is_safe(text)
        if not safe:
            self.stats["blocked"] += 1
            log_warning(
                f"⛔ [SAFEBRAIN:process] مسدود | {reason}"
            )
            return f"⛔ درخواست مسدود شد: {reason}"

        # ۲) کش
        cached = self.cache.get(text)
        if cached:
            self.stats["cached"] += 1
            elapsed_ms = (time.monotonic() - t_all) * 1000
            log_info(
                f"💡 [SAFEBRAIN:process] cache HIT | "
                f"{elapsed_ms:.0f}ms"
            )
            return cached

        # ۳) تشخیص دستور
        action, value = self._rule_parse(text)
        log_info(
            f"🎯 [SAFEBRAIN:parse] action={action} value={value!r}"
        )

        if action is not None:
            return await self._handle_parsed_action(
                action, value, text, t_all
            )

        # ۴) AI (خودش rate limit را چک می‌کند)
        ai_result = await self._try_ai(text, t_all)
        if ai_result is not None:
            return ai_result

        # ۵) Brain.agent
        if self.api_key and self._can_call_api():
            brain = self._get_brain()
            if brain:
                try:
                    self.project_memory.add("user", text)
                    self.global_memory.add("user", text)
                    t_b = time.monotonic()
                    result = await brain.agent(text)
                    b_ms = (time.monotonic() - t_b) * 1000
                    self.project_memory.add("assistant", result)
                    self.global_memory.add("assistant", result)
                    self.cache.set(text, result)
                    elapsed_ms = (time.monotonic() - t_all) * 1000
                    log_success(
                        f"✅ [SAFEBRAIN:process] Brain.agent | "
                        f"agent={b_ms:.0f}ms total={elapsed_ms:.0f}ms"
                    )
                    return result
                except Exception as e:
                    log_error(
                        f"❌ [SAFEBRAIN:process] Brain.agent | "
                        f"{type(e).__name__}: {e}"
                    )
                    self.stats["errors"] += 1

        # ۶) Fallback
        self.stats["fallback"] += 1
        elapsed_ms = (time.monotonic() - t_all) * 1000
        log_warning(
            f"⚠️ [SAFEBRAIN:process] fallback | {elapsed_ms:.0f}ms"
        )
        return self._fallback_response(text)

    async def _handle_parsed_action(
        self,
        action: str,
        value: str | None,
        text: str,
        t_all: float,
    ) -> Any:  # str | _DelegateType
        if self.full_access or action in SAFE_ACTIONS:
            result = await self._execute_safe_action(
                action, value, text
            )

            # استفاده از _DELEGATE به‌جای ""
            if isinstance(result, _DelegateType):
                self.stats["delegated"] += 1
                elapsed_ms = (time.monotonic() - t_all) * 1000
                log_info(
                    f"↪️ [SAFEBRAIN:process] واگذاری | "
                    f"action={action} value={value!r} | "
                    f"{elapsed_ms:.0f}ms"
                )
                return _DELEGATE

            # نتیجهٔ معتبر (str)
            self.cache.set(text, result)
            self.global_memory.add("assistant", result)
            elapsed_ms = (time.monotonic() - t_all) * 1000
            log_success(
                f"✅ [SAFEBRAIN:process] {action} | "
                f"{len(result)}ch | {elapsed_ms:.0f}ms"
            )
            return result

        self.stats["blocked"] += 1
        log_warning(
            f"⛔ [SAFEBRAIN:process] اکشن غیرمجاز: {action}"
        )
        return f"⛔ اکشن '{action}' غیرمجاز است."

    async def _try_ai(
        self, text: str, t_all: float
    ) -> str | None:
        """
        تلاش برای پاسخ با AI.

        🔴 FIX: حالا rate limit روی DeepSeek اعمال می‌شود.
        پیش از این، `SAFE_BRAIN_MAX_API_HOUR` فقط روی Brain.agent
        اعمال می‌شد → DeepSeek از مسیر AIAssistant بی‌محدودیت
        صدا زده می‌شد.

        🔴 FIX: حالا از `chat_with_status()` استفاده می‌کنیم تا
        `is_fallback` دقیق باشد (نه با prefix که dead code داشت).

        🟠 FIX: پاسخ fallback کش نمی‌شود.
        """
        ai = self._get_ai()
        if not ai or not AI_CONFIG.get("enabled", True):
            return None

        try:
            if not await ai.available():
                return None

            # 🔴 FIX: rate limit — فقط اگه DeepSeek در provider order
            # (Ollama رایگانه و نیازی به محدودیت نداره)
            try:
                provider_order = ai._provider_order()
            except Exception:
                provider_order = []

            if "deepseek" in provider_order:
                if not self._can_call_api():
                    log_warning(
                        "⚠️ [SAFEBRAIN:_try_ai] "
                        "محدودیت API DeepSeek — AI صدا زده نشد"
                    )
                    return None

            context = self.global_memory.get_context(3)
            system = (
                "تو «مغز هوشمند ربات پایش سرنخ سپاهان رویش» هستی؛ "
                "یک دستیار داخلی ربات. همیشه به فارسی، کوتاه، "
                "دقیق و حرفه‌ای جواب بده. هرگز نگو که «کلود» یا "
                "یک مدل هوش مصنوعی بیرونی هستی. اگر چیزی را "
                "نمی‌دانی، صادقانه بگو.\n\n"
            )
            if context:
                system += f"زمینه گفتگو:\n{context}\n\n"

            t_ai = time.monotonic()
            try:
                # 🔴 FIX: از chat_with_status استفاده می‌کنیم
                # (پاسخ + پرچم موفقیت)
                if hasattr(ai, "chat_with_status"):
                    ans, ok = await asyncio.wait_for(
                        ai.chat_with_status(text, system=system),
                        timeout=_AI_TIMEOUT_S,
                    )
                else:
                    # fallback اگه AIAssistant قدیمی بود
                    ans = await asyncio.wait_for(
                        ai.chat(text, system=system),
                        timeout=_AI_TIMEOUT_S,
                    )
                    ok = bool(ans) and not ans.startswith(
                        "⚠️ هوش در دسترس نیست"
                    )
            except TimeoutError:
                elapsed_ms = (time.monotonic() - t_ai) * 1000
                log_warning(
                    f"⏰ [SAFEBRAIN:ai] timeout | "
                    f"{elapsed_ms:.0f}ms > {_AI_TIMEOUT_S}s"
                )
                return None

            ai_ms = (time.monotonic() - t_ai) * 1000

            # چک پاسخ خالی
            if not ans or not isinstance(ans, str):
                log_info(
                    f"ℹ️ [SAFEBRAIN:ai] پاسخ خالی | {ai_ms:.0f}ms"
                )
                return None

            is_fallback = not ok

            self.global_memory.add("user", text)
            self.global_memory.add("assistant", ans)

            # 🔴 FIX: فقط پاسخ معتبر کش می‌شود
            if not is_fallback:
                self.cache.set(text, ans)

            elapsed_ms = (time.monotonic() - t_all) * 1000
            log_success(
                f"✅ [SAFEBRAIN:process] AI | "
                f"ai={ai_ms:.0f}ms total={elapsed_ms:.0f}ms | "
                f"len={len(ans)} | fallback={is_fallback}"
            )
            return ans
        except Exception as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:process] AI | "
                f"{type(e).__name__}: {e}"
            )
            return None

    # ═══════════════════════════════════════════
    # parsing
    # ═══════════════════════════════════════════
    @staticmethod
    def _extract_value(text: str) -> str | None:
        return _extract_value_fn(text)

    @staticmethod
    def _platform_in(low: str) -> str | None:
        return _platform_in_fn(low)

    def _rule_parse(
        self, text: str
    ) -> tuple[str | None, str | None]:
        return _parse_command(text)

    # ═══════════════════════════════════════════
    # execute
    # ═══════════════════════════════════════════
    async def _execute_safe_action(
        self, action: str, value: str | None, raw_text: str
    ) -> Any:  # str | _DelegateType
        """
        اجرای اکشن امن.

        Returns:
            str: پاسخ آماده
            _DELEGATE: واگذاری به لایهٔ بالاتر
        """
        t0 = time.monotonic()
        try:
            if action in SAFE_ACTIONS_HANDLED_LOCALLY:
                if action == "help":
                    return self._help()
                if action == "add_keyword":
                    return self._add_keyword(
                        value or self._extract_value(raw_text) or ""
                    )
                if action == "remove_keyword":
                    return self._remove_keyword(
                        value or self._extract_value(raw_text) or ""
                    )
                if action == "feature_request":
                    return self._log_request(raw_text)

            if action in SAFE_ACTIONS:
                # واگذاری به لایهٔ بالاتر
                return _DELEGATE

            if self.full_access:
                log_warning(
                    f"⚠️ [SAFEBRAIN:exec] ناشناخته «{action}» "
                    f"(full_access) — واگذاری"
                )
                return _DELEGATE

            # اکشن غیرمجاز در حالت غیر full_access
            return _DELEGATE
        except Exception as e:
            log_error(
                f"❌ [SAFEBRAIN:exec] {action} | "
                f"{type(e).__name__}: {e}"
            )
            self.stats["errors"] += 1
            return f"❌ خطا در اجرا: {e}"
        finally:
            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > _SLOW_ACTION_MS:
                log_warning(
                    f"🐌 [SAFEBRAIN:exec] {action} کند | "
                    f"{elapsed_ms:.0f}ms"
                )

    # ═══════════════════════════════════════════
    # local answers
    # ═══════════════════════════════════════════
    @staticmethod
    def _help() -> str:
        return (
            "🛡️ **SafeBrain — راهنما:**\n"
            "━━━━━━━━━━━━━━━\n"
            "دستورهایی که مستقیم کار می‌کنند:\n"
            "• کلیدواژه «...» را اضافه کن\n"
            "• حذف کلیدواژه «...»\n"
            "• درخواست قابلیت جدید (با «می‌خوام» + «قابلیت»)\n\n"
            "برای بقیه (وضعیت، گزارش، CRM، لاگ، سلامت، "
            "ری‌استارت) → AdminHandler جواب می‌دهد."
        )

    @staticmethod
    def _add_keyword(value: str) -> str:
        if not value:
            return "کلیدواژه را داخل « » بنویس."
        kw = str(value).strip()
        current = _read_custom_kw_dict()
        if kw in current:
            log_info(
                f"ℹ️ [SAFEBRAIN:add_kw] «{kw}» از قبل هست"
            )
            return f"«{kw}» از قبل هست."
        current[kw] = "T1"
        if _write_custom_kw_dict(current):
            log_success(
                f"✅ [SAFEBRAIN:add_kw] «{kw}» اضافه شد | "
                f"total={len(current)}"
            )
            return (
                f"✅ «{kw}» اضافه شد "
                f"(چند ثانیهٔ دیگر خودکار فعال می‌شود)."
            )
        return "❌ ذخیره ناموفق."

    @staticmethod
    def _remove_keyword(value: str) -> str:
        if not value:
            return "کدام کلیدواژه؟ داخل « » بنویس."
        kw = str(value).strip()
        current = _read_custom_kw_dict()
        if kw not in current:
            log_info(f"ℹ️ [SAFEBRAIN:rm_kw] «{kw}» نبود")
            return (
                f"«{kw}» در لیست کلیدواژه‌های سفارشی نیست."
            )
        current.pop(kw, None)
        if _write_custom_kw_dict(current):
            log_success(
                f"🗑️ [SAFEBRAIN:rm_kw] «{kw}» حذف شد | "
                f"remaining={len(current)}"
            )
            return f"🗑️ «{kw}» حذف شد."
        return "❌ ذخیره ناموفق."

    @staticmethod
    def _log_request(text: str) -> str:
        try:
            path = Path(STORAGE_DIR) / "requests.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(str(path), "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "ts": datetime.now().isoformat(),
                            "request": text,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            log_info(
                f"📝 [SAFEBRAIN:request] ثبت شد | "
                f"len={len(text)}"
            )
            return "📝 درخواست قابلیت جدید ثبت شد."
        except Exception as e:
            log_error(
                f"❌ [SAFEBRAIN:request] "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا در ثبت: {e}"

    def _fallback_response(self, text: str = "") -> str:
        """
        پاسخ fallback.

        hint هوشمندتر بر اساس کلمات کلیدی.
        """
        hint = ""
        if text:
            t_low = text.lower()
            if any(k in t_low for k in ("وضعیت", "status", "آمار")):
                hint = "\n💡 «وضعیت» → آمار زنده"
            elif any(
                k in t_low for k in ("گزارش", "report", "crm")
            ):
                hint = "\n💡 «گزارش» یا «crm» → ساخت فایل"
            elif any(
                k in t_low for k in ("کلیدواژه", "keyword")
            ):
                hint = "\n💡 «کلیدواژه‌ها» → لیست کامل"
            elif any(
                k in t_low for k in ("سلامت", "health")
            ):
                hint = "\n💡 «سلامت» → بررسی سیستم"
            elif any(
                k in t_low for k in ("لاگ", "log", "خطا", "error")
            ):
                hint = "\n💡 «لاگ» → آخرین خطاها"

        return (
            "🤖 WinDoctor:\n"
            "هوش مصنوعی در دسترس نیست. از دستورات مستقیم "
            "استفاده کنید:\n"
            "• «وضعیت» → آمار\n"
            "• «راهنما» → دستورات\n"
            "• «کلیدواژه‌ها» → لیست\n"
            "• «سلامت» → بررسی سیستم"
            + hint
        )

    # ═══════════════════════════════════════════
    # flush
    # ═══════════════════════════════════════════
    def flush(self):
        try:
            self.cache.flush()
            self.project_memory.flush()
            self.global_memory.flush()
            log_info("💾 [SAFEBRAIN:flush] همه ذخیره شدند")
        except Exception as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:flush] "
                f"{type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # debug
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        brain_age = (
            time.monotonic() - self._brain_failed_at
            if self._brain_failed_at > 0
            else 0.0
        )

        # محاسبهٔ نرخ delegation
        processed = self.stats.get("processed", 0)
        delegated = self.stats.get("delegated", 0)
        delegated_rate = (
            round(delegated / processed, 3) if processed else 0.0
        )

        return {
            **self.stats,
            "cache": self.cache.stats(),
            "project_memory": self.project_memory.count,
            "global_memory": self.global_memory.count,
            "api_calls_this_hour": self._api_calls_this_hour,
            "brain_connected": self._brain_instance is not None,
            "brain_failed": self._brain_failed_at > 0,
            "brain_retry_in_s": (
                max(0, int(_BRAIN_FAIL_RETRY_S - brain_age))
                if self._brain_failed_at > 0
                else 0
            ),
            "ai_available": self._ai_assistant is not None,
            "delegated_rate": delegated_rate,
        }


# ════════════════════════════════════════════
# Singleton
# ════════════════════════════════════════════
_shared_safe_brain: SafeBrain | None = None
_shared_lock = threading.Lock()


def get_safe_brain(
    api_key: str | None = None,
    full_access: bool = False,
    use_api_memory: bool = False,
    memory_api_url: str = "",
    memory_api_key: str = "",
) -> SafeBrain:
    """دریافت نمونهٔ Singleton SafeBrain."""
    global _shared_safe_brain

    with _shared_lock:
        if _shared_safe_brain is None:
            log_info("🏗️ [SAFEBRAIN:singleton] ساخت نمونهٔ اول")
            _shared_safe_brain = SafeBrain(
                api_key=api_key,
                full_access=full_access,
                use_api_memory=use_api_memory,
                memory_api_url=memory_api_url,
                memory_api_key=memory_api_key,
            )
        else:
            updated = []
            if full_access and not _shared_safe_brain.full_access:
                _shared_safe_brain.full_access = True
                updated.append("full_access=True")
            if use_api_memory and memory_api_url:
                _shared_safe_brain.use_api_memory = True
                _shared_safe_brain.memory_api_url = memory_api_url
                _shared_safe_brain.memory_api_key = memory_api_key
                updated.append("memory_api")
            if updated:
                log_info(
                    f"♻️ [SAFEBRAIN:singleton] به‌روزرسانی | "
                    f"fields={updated}"
                )
    return _shared_safe_brain