"""SafeBrain — گارد امن + کش + حافظه + PersistentMemory.

نسخهٔ ۹ (این نسخه):
  - 🐛 FIX بحرانی: `_try_ai` حالا از `get_provider_order()` public
    استفاده می‌کند (نه `ai._provider_order()` private).
  - 🐛 FIX بحرانی: `_try_ai` حالا از `_has_api_budget()` استفاده
    می‌کند (budget را هدر نمی‌دهد).
  - 🐛 FIX: `_get_ai` حالا اگر `get_ai_assistant()` خطا بدهد،
    `_ai_assistant` را None نگه می‌دارد (نه که instance خالی
    برگرداند).
  - 🎯 `_has_api_budget()` helper جدید — فقط چک می‌کند،
    افزایش نمی‌دهد.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۸.

نسخهٔ ۸ (قبلی):
  - 🎯 اتصال به `PersistentMemory` (SQLite + FTS5).
  - 🎯 `_try_persistent_recall()` و `_try_persistent_remember()`.
  - 🎯 `_persistent_context()` برای context.
  - 🎯 در `_process_inner`: قبل از AI، اول PersistentMemory چک می‌شود.
  - 🎯 در `_try_ai`: context از PersistentMemory هم اضافه می‌شود.

نسخهٔ ۷ (قدیمی‌تر):
  - 🎯 پیام‌های خطا از `data/configs/errors.json`.
  - 🎯 `_err()` helper.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from datetime import datetime
from typing import Any

from config import AI as AI_CONFIG
from config import STORAGE_DIR
from core.adapters.command_parser import (
    extract_value as _extract_value_fn,
)
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
# helpers (env)
# ═══════════════════════════════════════════
def _env_int(
    name: str, default: int,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    """خواندن int از env — با sanitize."""
    try:
        v = int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


def _env_float(
    name: str, default: float,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    """خواندن float از env — با sanitize."""
    try:
        v = float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


def _env_bool(name: str, default: bool = True) -> bool:
    """خواندن bool از env."""
    raw = os.getenv(name)
    if raw is None:
        return default
    v = str(raw).strip().lower()
    if v in ("true", "1", "yes", "on", "y", "t"):
        return True
    if v in ("false", "0", "no", "off", "n", "f"):
        return False
    return default


# ═══════════════════════════════════════════
# پیام‌های خطا از data/configs/errors.json
# ═══════════════════════════════════════════
try:
    from data.loader import load_json as _load_json_errors
    _ERRORS_DATA = _load_json_errors("errors", default={}) or {}
except Exception:
    _ERRORS_DATA = {}

_ERRS: dict[str, str] = (
    _ERRORS_DATA.get("errors", {})
    if isinstance(_ERRORS_DATA, dict)
    else {}
)


def _err(key: str, default: str = "", **kw) -> str:
    """خواندن پیام خطا از فایل — با fallback امن."""
    template = _ERRS.get(key) or default or f"[{key}]"
    try:
        return template.format(**kw) if kw else template
    except (KeyError, IndexError, ValueError):
        return template


# ═══════════════════════════════════════════
# تنظیمات (env-based)
# ═══════════════════════════════════════════
_AI_TIMEOUT_S = _env_float(
    "SAFE_BRAIN_AI_TIMEOUT_S", 120.0,
    min_val=5.0, max_val=300.0,
)
_PROJECT_MEMORY_MAX = _env_int(
    "SAFE_BRAIN_PROJECT_MEMORY", 2000,
    min_val=10, max_val=10_000,
)
_GLOBAL_MEMORY_MAX = _env_int(
    "SAFE_BRAIN_GLOBAL_MEMORY", 5000,
    min_val=10, max_val=10_000,
)
_CACHE_TTL_HOURS = _env_int(
    "SAFE_BRAIN_CACHE_TTL_HOURS", 720,
    min_val=1, max_val=720,
)
_CONTEXT_N = _env_int(
    "SAFE_BRAIN_CONTEXT_N", 5,
    min_val=1, max_val=20,
)

# 🆕 PersistentMemory settings
_PERSISTENT_ENABLED = _env_bool(
    "BRAIN_PERSISTENT_MEMORY", True
)
_PERSISTENT_REMEMBER = _env_bool(
    "SAFE_BRAIN_REMEMBER_CONVERSATIONS", True
)
_PERSISTENT_CONTEXT_N = _env_int(
    "SAFE_BRAIN_PERSISTENT_CONTEXT_N", 3,
    min_val=1, max_val=20,
)
_PERSISTENT_IMPORTANCE_DEFAULT = _env_float(
    "SAFE_BRAIN_PERSISTENT_IMPORTANCE", 0.5,
    min_val=0.0, max_val=1.0,
)


# ═══════════════════════════════════════════
# Sentinel برای delegation
# ═══════════════════════════════════════════
class _DelegateType:
    """Type منحصربه‌فرد برای delegation به لایهٔ بالاتر."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<DELEGATE>"

    def __bool__(self) -> bool:
        """در بولی، _DELEGATE باید False باشد."""
        return False


_DELEGATE = _DelegateType()


# ═══════════════════════════════════════════
# SafeBrain
# ═══════════════════════════════════════════
class SafeBrain:
    """مغز امن ربات — لایهٔ نازک روی Brain + حافظهٔ پایدار.

    نسخهٔ ۹:
      - PersistentMemory (SQLite + FTS5) — هیچ‌وقت پاک نمی‌شود.
      - پاسخ‌های قبلی از حافظه بازیابی می‌شوند.
      - پیام‌های خطا از `data/configs/errors.json`.
      - rate-limit DeepSeek (بدون هدر دادن budget).
      - پاسخ fallback کش نمی‌شود.
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
        # api_key از config.AI
        self.api_key = (
            api_key
            or (AI_CONFIG or {}).get("deepseek_api_key", "")
            or os.environ.get("DEEPSEEK_API_KEY", "")
            or ""
        )
        self.full_access = full_access

        # ─── memory (کلاسیک) ───
        self.project_memory = SafeMemory(
            "project_memory",
            max_entries=_PROJECT_MEMORY_MAX,
        )
        self.global_memory = SafeMemory(
            "global_memory",
            max_entries=_GLOBAL_MEMORY_MAX,
        )

        # ─── cache ───
        self.cache = SafeCache(
            "safe_brain_cache",
            ttl_hours=_CACHE_TTL_HOURS,
        )

        # ═══════════════════════════════════════════
        # 🆕 PersistentMemory (SQLite + FTS5)
        # ═══════════════════════════════════════════
        self.persistent_memory: Any | None = None
        self._persistent_lock = threading.Lock()

        if _PERSISTENT_ENABLED:
            try:
                from core.brain.memory import (
                    get_persistent_memory,
                )
                self.persistent_memory = (
                    get_persistent_memory()
                )
                if self.persistent_memory is not None:
                    log_success(
                        f"✅ [SAFEBRAIN:persistent] اتصال | "
                        f"count="
                        f"{self.persistent_memory.count()}"
                    )
                else:
                    log_warning(
                        "⚠️ [SAFEBRAIN:persistent] "
                        "get_persistent_memory() = None"
                    )
            except Exception as e:
                log_warning(
                    f"⚠️ [SAFEBRAIN:persistent] "
                    f"{type(e).__name__}: {e}"
                )
                self.persistent_memory = None
        else:
            log_info(
                "ℹ️ [SAFEBRAIN:persistent] غیرفعال "
                "(BRAIN_PERSISTENT_MEMORY=false)"
            )

        # ─── rate limit ───
        self._api_calls_this_hour = 0
        self._api_hour_start = time.monotonic()
        self._api_lock = threading.Lock()

        # ─── lazy instances ───
        self._brain_instance: Any | None = None
        self._brain_failed_at: float = 0.0
        self._brain_lock = threading.Lock()

        self._ai_assistant: Any | None = None
        self._ai_lock = threading.Lock()

        # ─── memory config ───
        self.use_api_memory = use_api_memory
        self.memory_api_url = memory_api_url
        self.memory_api_key = memory_api_key

        # ─── stats ───
        self.stats: dict[str, Any] = {
            "processed": 0,
            "cached": 0,
            "api_calls": 0,
            "api_blocked": 0,
            "blocked": 0,
            "errors": 0,
            "delegated": 0,
            "fallback": 0,
            "persistent_recall": 0,
            "persistent_remember": 0,
            "started_at": datetime.now().isoformat(),  # noqa: DTZ005
            "started_monotonic": time.monotonic(),
        }

        persistent_status = (
            "✅" if self.persistent_memory else "❌"
        )

        log_success(
            f"✅ [SAFEBRAIN:init] آماده | "
            f"api_key={'✅' if self.api_key else '❌'} | "
            f"full_access={'✅' if full_access else '❌'} | "
            f"persistent={persistent_status} | "
            f"max_api_hour={MAX_API_CALLS_PER_HOUR} | "
            f"ai_timeout={_AI_TIMEOUT_S}s"
        )

    # ═══════════════════════════════════════════
    # PersistentMemory helpers
    # ═══════════════════════════════════════════
    def _try_persistent_recall(
        self, query: str
    ) -> str | None:
        """جستجوی پاسخ در حافظهٔ پایدار.

        Args:
            query: سؤال کاربر.

        Returns:
            پاسخ اگر در حافظه بود، وگرنه None.
        """
        # 🎯 غیرفعال — همیشه از AI زنده
        if os.getenv("SAFE_BRAIN_RECALL_FROM_MEMORY", "true").lower() != "true":
            return None
        
        if not self.persistent_memory:
            return None
        if not query or len(query) < 3:
            return None

        try:
            # ─── FTS search ───
            results = self.persistent_memory.recall(
                query=query,
                category="conversation",
                limit=1,
            )

            if not results:
                return None

            # ─── بهترین نتیجه (اولین) ───
            best = results[0]
            answer = best.get("value") or ""

            # ─── فقط اگر پاسخ کافی طولانی باشد ───
            if not answer or len(answer) < 10:
                return None

            # ─── log ───
            self.stats["persistent_recall"] += 1
            log_success(
                f"🎯 [SAFEBRAIN:persistent:recall] "
                f"'{query[:40]}' → "
                f"'{answer[:40]}' | "
                f"imp={best.get('importance', 0)}"
            )
            return answer

        except Exception as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:persistent:recall] "
                f"{type(e).__name__}: {e}"
            )
            return None

    def _try_persistent_remember(
        self,
        query: str,
        answer: str,
        category: str = "conversation",
        importance: float | None = None,
    ) -> bool:
        """ذخیره در حافظهٔ پایدار.

        Args:
            query: سؤال.
            answer: پاسخ.
            category: دسته.
            importance: اهمیت.

        Returns:
            True اگر ذخیره شد.
        """
        if not self.persistent_memory:
            return False
        if not query or not answer:
            return False
        if len(query) < 3 or len(answer) < 10:
            return False

        if importance is None:
            importance = _PERSISTENT_IMPORTANCE_DEFAULT

        try:
            new_id = self.persistent_memory.remember(
                category=category,
                key=query[:500],
                value=answer[:10000],
                importance=importance,
            )

            if new_id > 0:
                self.stats["persistent_remember"] += 1
                log_info(
                    f"💾 [SAFEBRAIN:persistent:remember] "
                    f"id={new_id} | "
                    f"cat={category} | "
                    f"'{query[:40]}'"
                )
                return True
            return False

        except Exception as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:persistent:remember] "
                f"{type(e).__name__}: {e}"
            )
            return False

    def _persistent_context(self, query: str) -> str:
        """context از PersistentMemory برای AI.

        Returns:
            متن آماده یا "".
        """
        if not self.persistent_memory:
            return ""
        try:
            return self.persistent_memory.get_context(
                query, n=_PERSISTENT_CONTEXT_N
            )
        except Exception as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:persistent:context] "
                f"{type(e).__name__}: {e}"
            )
            return ""

    # ═══════════════════════════════════════════
    # lazy init
    # ═══════════════════════════════════════════
    def _get_brain(self) -> Any | None:
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

    def _get_ai(self) -> Any | None:
        """🎯 FIX: اگر خطا داد، None نگه‌دار (نه instance خالی)."""
        with self._ai_lock:
            if self._ai_assistant is not None:
                return self._ai_assistant
            try:
                t0 = time.monotonic()
                from core.services.ai_assistant import (
                    get_ai_assistant,
                )
                self._ai_assistant = get_ai_assistant()
                elapsed_ms = (time.monotonic() - t0) * 1000
                log_info(
                    f"🔗 [SAFEBRAIN:ai] اتصال | "
                    f"{elapsed_ms:.0f}ms"
                )
                return self._ai_assistant
            except Exception as e:
                log_warning(
                    f"⚠️ [SAFEBRAIN:ai] در دسترس نیست | "
                    f"{type(e).__name__}: {e}"
                )
                return None

    # ═══════════════════════════════════════════
    # rate limit
    # ═══════════════════════════════════════════
    def _has_api_budget(self) -> bool:
        """🎯 NEW: چک بودجهٔ API — بدون افزایش شمارنده.

        این متد برای تصمیم‌گیری «آیا AI را صدا بزنم؟» استفاده
        می‌شود، بدون اینکه بودجه هدر برود.

        Returns:
            True اگر بودجه در دسترس باشد.
        """
        if self.full_access:
            return True

        with self._api_lock:
            now = time.monotonic()
            # ─── پنجره منقضی شده ───
            if now - self._api_hour_start > 3600:
                return True
            # ─── چک سقف ───
            return (
                self._api_calls_this_hour
                < MAX_API_CALLS_PER_HOUR
            )

    def _can_call_api(self) -> bool:
        """چک بودجه + افزایش شمارنده (برای استفادهٔ واقعی).

        🐛 FIX: این متد فقط وقتی صدا زده می‌شود که واقعاً
        می‌خواهیم API را صدا بزنیم.
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

            self._api_calls_this_hour += 1
            self.stats["api_calls"] += 1

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
        """چک امنیت — با پشتیبانی از full_access."""
        if self.full_access:
            return True, ""
        if not text or not isinstance(text, str):
            return True, ""

        low = text.lower()

        for pat in BLOCKED_SUBSTRINGS:
            if pat.lower() in low:
                return False, f"الگوی ممنوعه: {pat}"

        for pat in BLOCKED_PATTERNS_REGEX:
            try:
                if re.search(pat, low, re.IGNORECASE):
                    return False, f"الگوی ممنوعه: {pat}"
            except re.error:
                continue

        return True, ""

    # ═══════════════════════════════════════════
    # process — نقطهٔ ورود اصلی
    # ═══════════════════════════════════════════
    async def process(self, text: str) -> str | None:
        """پردازش ورودی کاربر.

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
            f"▶️ [SAFEBRAIN:process] "
            f"#{self.stats['processed']} | "
            f"len={len(text)} | "
            f"full_access={self.full_access} | "
            f"in={text[:_LOG_PREVIEW_LEN]!r}"
        )

        try:
            result = await self._process_inner(text, t_all)
            if isinstance(result, _DelegateType):
                return None
            return result
        except Exception as e:
            self.stats["errors"] += 1
            elapsed_ms = (
                time.monotonic() - t_all
            ) * 1000
            log_error(
                f"❌ [SAFEBRAIN:process] "
                f"{type(e).__name__}: {e} | "
                f"{elapsed_ms:.0f}ms — fallback"
            )
            self.stats["fallback"] += 1
            return self._fallback_response(text)

    async def _process_inner(
        self, text: str, t_all: float
    ) -> Any:
        # ─── ۱) امنیت ───
        safe, reason = self._is_safe(text)
        if not safe:
            self.stats["blocked"] += 1
            log_warning(
                f"⛔ [SAFEBRAIN:process] مسدود | {reason}"
            )
            return _err(
                "blocked_security",
                f"⛔ درخواست مسدود شد: {reason}",
                reason=reason,
            )

        # ─── ۲) کش ───
        cached = self.cache.get(text)
        if cached:
            self.stats["cached"] += 1
            elapsed_ms = (
                time.monotonic() - t_all
            ) * 1000
            log_info(
                f"💡 [SAFEBRAIN:process] cache HIT | "
                f"{elapsed_ms:.0f}ms"
            )
            return cached

        # ═══════════════════════════════════════════
        # 🆕 ۳) PersistentMemory recall
        # ═══════════════════════════════════════════
        persistent_answer = self._try_persistent_recall(text)
        if persistent_answer:
            # کش هم بگذار
            self.cache.set(text, persistent_answer)

            elapsed_ms = (
                time.monotonic() - t_all
            ) * 1000
            log_success(
                f"✅ [SAFEBRAIN:process] persistent HIT | "
                f"{elapsed_ms:.0f}ms"
            )
            return persistent_answer

        # ─── ۴) تشخیص دستور ───
        action, value = self._rule_parse(text)
        log_info(
            f"🎯 [SAFEBRAIN:parse] action={action} "
            f"value={value!r}"
        )

        if action is not None:
            return await self._handle_parsed_action(
                action, value, text, t_all
            )

        # ─── ۵) AI ───
        ai_result = await self._try_ai(text, t_all)
        if ai_result is not None:
            # 🆕 ذخیره در PersistentMemory
            if _PERSISTENT_REMEMBER:
                self._try_persistent_remember(
                    text, ai_result,
                    category="conversation",
                    importance=0.5,
                )
            return ai_result

        # ─── ۶) Brain.agent ───
        # 🎯 FIX: استفاده از _can_call_api فقط وقتی واقعاً می‌خواهیم
        if self.api_key and self._can_call_api():
            brain = self._get_brain()
            if brain:
                try:
                    self.project_memory.add("user", text)
                    self.global_memory.add("user", text)
                    t_b = time.monotonic()
                    result = await brain.agent(text)
                    b_ms = (time.monotonic() - t_b) * 1000
                    self.project_memory.add(
                        "assistant", result
                    )
                    self.global_memory.add(
                        "assistant", result
                    )
                    self.cache.set(text, result)

                    # 🆕 ذخیره در PersistentMemory
                    if _PERSISTENT_REMEMBER:
                        self._try_persistent_remember(
                            text, result,
                            category="conversation",
                            importance=0.6,
                        )

                    elapsed_ms = (
                        time.monotonic() - t_all
                    ) * 1000
                    log_success(
                        f"✅ [SAFEBRAIN:process] "
                        f"Brain.agent | agent={b_ms:.0f}ms "
                        f"total={elapsed_ms:.0f}ms"
                    )
                    return result
                except Exception as e:
                    log_error(
                        f"❌ [SAFEBRAIN:process] "
                        f"Brain.agent | "
                        f"{type(e).__name__}: {e}"
                    )
                    self.stats["errors"] += 1

        # ─── ۷) Fallback ───
        self.stats["fallback"] += 1
        elapsed_ms = (time.monotonic() - t_all) * 1000
        log_warning(
            f"⚠️ [SAFEBRAIN:process] fallback | "
            f"{elapsed_ms:.0f}ms"
        )
        return self._fallback_response(text)

    async def _handle_parsed_action(
        self,
        action: str,
        value: str | None,
        text: str,
        t_all: float,
    ) -> Any:
        if self.full_access or action in SAFE_ACTIONS:
            result = await self._execute_safe_action(
                action, value, text
            )

            if isinstance(result, _DelegateType):
                self.stats["delegated"] += 1
                elapsed_ms = (
                    time.monotonic() - t_all
                ) * 1000
                log_info(
                    f"↪️ [SAFEBRAIN:process] واگذاری | "
                    f"action={action} value={value!r} | "
                    f"{elapsed_ms:.0f}ms"
                )
                return _DELEGATE

            if result:
                self.cache.set(text, result)
                self.global_memory.add(
                    "assistant", result
                )

            elapsed_ms = (time.monotonic() - t_all) * 1000
            log_success(
                f"✅ [SAFEBRAIN:process] {action} | "
                f"{len(result or '')}ch | "
                f"{elapsed_ms:.0f}ms"
            )
            return result

        self.stats["blocked"] += 1
        log_warning(
            f"⛔ [SAFEBRAIN:process] اکشن غیرمجاز: {action}"
        )
        return _err(
            "not_allowed_action",
            f"⛔ اکشن '{action}' غیرمجاز است.",
            action=action,
        )

    async def _try_ai(
        self, text: str, t_all: float
    ) -> str | None:
        """تلاش برای پاسخ با AI.

        🐛 FIX: حالا از `get_provider_order()` public و
        `_has_api_budget()` استفاده می‌کند (budget را هدر نمی‌دهد).

        - rate limit روی DeepSeek.
        - `chat_with_status()` برای دقت.
        - پاسخ fallback کش نمی‌شود.
        - 🆕 context از PersistentMemory.
        """
        ai = self._get_ai()
        if not ai:
            return None

        if not isinstance(AI_CONFIG, dict):
            return None
        if not AI_CONFIG.get("enabled", True):
            return None

        try:
            if not await ai.available():
                return None

            # ═══════════════════════════════════════════
            # 🐛 FIX: provider order از public API
            # ═══════════════════════════════════════════
            try:
                if hasattr(ai, "get_provider_order"):
                    provider_order = ai.get_provider_order()
                else:
                    # fallback: اگر متد public نبود، از _provider_order
                    provider_order = ai._provider_order()
            except Exception as e:
                log_warning(
                    f"⚠️ [SAFEBRAIN:_try_ai] "
                    f"provider_order | "
                    f"{type(e).__name__}: {e}"
                )
                provider_order = []

            # ═══════════════════════════════════════════
            # 🐛 FIX: چک بودجه بدون هدر دادن
            # ═══════════════════════════════════════════
            if "deepseek" in provider_order:
                if not self._has_api_budget():
                    log_warning(
                        "⚠️ [SAFEBRAIN:_try_ai] "
                        "محدودیت API DeepSeek — "
                        "AI صدا زده نشد"
                    )
                    return None

            # ─── context از حافظه‌های محلی ───
            context = self.global_memory.get_context(
                _CONTEXT_N
            )

            # 🆕 context از PersistentMemory
            persistent_ctx = self._persistent_context(text)

            system = (
                "تو «مغز هوشمند ربات پایش سرنخ سپاهان "
                "رویش» هستی؛ یک دستیار داخلی ربات. "
                "همیشه به فارسی، کوتاه، دقیق و حرفه‌ای "
                "جواب بده. هرگز نگو که «کلود» یا یک مدل "
                "هوش مصنوعی بیرونی هستی. اگر چیزی را "
                "نمی‌دانی، صادقانه بگو.\n\n"
            )
            if context:
                system += f"زمینه گفتگو:\n{context}\n\n"
            if persistent_ctx:
                system += (
                    f"\n{persistent_ctx}\n\n"
                )

            t_ai = time.monotonic()
            try:
                if hasattr(ai, "chat_with_status"):
                    ans, ok = await asyncio.wait_for(
                        ai.chat_with_status(
                            text, system=system
                        ),
                        timeout=_AI_TIMEOUT_S,
                    )
                else:
                    ans = await asyncio.wait_for(
                        ai.chat(text, system=system),
                        timeout=_AI_TIMEOUT_S,
                    )
                    ok = bool(ans) and not ans.startswith(
                        "⚠️ هوش در دسترس نیست"
                    )
            except TimeoutError:
                elapsed_ms = (
                    time.monotonic() - t_ai
                ) * 1000
                log_warning(
                    f"⏰ [SAFEBRAIN:ai] timeout | "
                    f"{elapsed_ms:.0f}ms > "
                    f"{_AI_TIMEOUT_S}s"
                )
                return None

            ai_ms = (time.monotonic() - t_ai) * 1000

            if not ans or not isinstance(ans, str):
                log_info(
                    f"ℹ️ [SAFEBRAIN:ai] پاسخ خالی | "
                    f"{ai_ms:.0f}ms"
                )
                return None

            is_fallback = not ok

            self.global_memory.add("user", text)
            self.global_memory.add("assistant", ans)

            if not is_fallback:
                self.cache.set(text, ans)

            elapsed_ms = (time.monotonic() - t_all) * 1000
            log_success(
                f"✅ [SAFEBRAIN:process] AI | "
                f"ai={ai_ms:.0f}ms total={elapsed_ms:.0f}ms "
                f"| len={len(ans)} | "
                f"fallback={is_fallback}"
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
        self,
        action: str,
        value: str | None,
        raw_text: str,
    ) -> Any:
        """اجرای اکشن امن."""
        t0 = time.monotonic()
        try:
            if action in SAFE_ACTIONS_HANDLED_LOCALLY:
                if action == "reload_prompt":
                    try:
                        from config import reload_ai_prompts
                        reload_ai_prompts()
                        return (
                            "✅ AI prompt ها بارگذاری "
                            "مجدد شدند."
                        )
                    except Exception as e:
                        log_error(
                            f"❌ [SAFEBRAIN:reload_prompt] "
                            f"{type(e).__name__}: {e}"
                        )
                        return f"❌ خطا: {e}"

                if action == "help":
                    return self._help()
                if action == "add_keyword":
                    return self._add_keyword(
                        value
                        or self._extract_value(raw_text)
                        or ""
                    )
                if action == "remove_keyword":
                    return self._remove_keyword(
                        value
                        or self._extract_value(raw_text)
                        or ""
                    )
                if action == "feature_request":
                    return self._log_request(raw_text)

            if action in SAFE_ACTIONS:
                return _DELEGATE

            if self.full_access:
                log_warning(
                    f"⚠️ [SAFEBRAIN:exec] ناشناخته "
                    f"«{action}» (full_access) — واگذاری"
                )
                return _DELEGATE

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
            "• درخواست قابلیت جدید (با «می‌خوام» + "
            "«قابلیت»)\n\n"
            "برای بقیه (وضعیت، گزارش، CRM، لاگ، سلامت، "
            "ری‌استارت) → AdminHandler جواب می‌دهد."
        )

    @staticmethod
    def _add_keyword(value: str) -> str:
        if not value:
            return _err(
                "empty_keyword",
                "کلیدواژه را داخل « » بنویس.",
            )
        kw = str(value).strip()
        current = _read_custom_kw_dict()
        if kw in current:
            log_info(
                f"ℹ️ [SAFEBRAIN:add_kw] «{kw}» از قبل هست"
            )
            return _err(
                "keyword_exists",
                f"«{kw}» از قبل هست.",
                kw=kw,
            )
        current[kw] = "T1"
        if _write_custom_kw_dict(current):
            log_success(
                f"✅ [SAFEBRAIN:add_kw] «{kw}» اضافه شد "
                f"| total={len(current)}"
            )
            return _err(
                "keyword_added",
                f"✅ «{kw}» اضافه شد "
                f"(چند ثانیهٔ دیگر خودکار فعال می‌شود).",
                kw=kw,
            )
        return _err("save_failed", "❌ ذخیره ناموفق.")

    @staticmethod
    def _remove_keyword(value: str) -> str:
        if not value:
            return _err(
                "remove_keyword_hint",
                "کدام کلیدواژه؟ داخل « » بنویس.",
            )
        kw = str(value).strip()
        current = _read_custom_kw_dict()
        if kw not in current:
            log_info(
                f"ℹ️ [SAFEBRAIN:rm_kw] «{kw}» نبود"
            )
            return _err(
                "keyword_not_found",
                f"«{kw}» در لیست کلیدواژه‌های سفارشی نیست.",
                kw=kw,
            )
        current.pop(kw, None)
        if _write_custom_kw_dict(current):
            log_success(
                f"🗑️ [SAFEBRAIN:rm_kw] «{kw}» حذف شد "
                f"| remaining={len(current)}"
            )
            return _err(
                "keyword_removed",
                f"🗑️ «{kw}» حذف شد.",
                kw=kw,
            )
        return _err("save_failed", "❌ ذخیره ناموفق.")

    @staticmethod
    def _log_request(text: str) -> str:
        """مسیر از data.paths."""
        try:
            try:
                from data import paths as data_paths
                path = str(data_paths.REQUESTS_FILE)
            except Exception:
                path = os.path.join(
                    STORAGE_DIR, "requests.jsonl"
                )

            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            with open(path, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "ts": datetime.now().isoformat(),  # noqa: DTZ005
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
            return _err(
                "request_saved",
                "📝 درخواست قابلیت جدید ثبت شد.",
            )
        except Exception as e:
            log_error(
                f"❌ [SAFEBRAIN:request] "
                f"{type(e).__name__}: {e}"
            )
            return _err(
                "request_save_failed",
                f"❌ خطا در ثبت: {e}",
                err=e,
            )

    def _fallback_response(self, text: str = "") -> str:
        """پاسخ fallback — با hint هوشمند."""
        hint = ""
        if text:
            t_low = text.lower()
            if any(
                k in t_low
                for k in ("وضعیت", "status", "آمار")
            ):
                hint = "\n💡 «وضعیت» → آمار زنده"
            elif any(
                k in t_low
                for k in ("گزارش", "report", "crm")
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
                k in t_low
                for k in ("لاگ", "log", "خطا", "error")
            ):
                hint = "\n💡 «لاگ» → آخرین خطاها"

        base = _err(
            "fallback_ai_offline",
            "🤖 WinDoctor:\n"
            "هوش مصنوعی در دسترس نیست. از دستورات "
            "مستقیم استفاده کنید:\n"
            "• «وضعیت» → آمار\n"
            "• «راهنما» → دستورات\n"
            "• «کلیدواژه‌ها» → لیست\n"
            "• «سلامت» → بررسی سیستم",
        )
        return base + hint

    # ═══════════════════════════════════════════
    # flush / close
    # ═══════════════════════════════════════════
    def flush(self) -> None:
        """ذخیرهٔ اجباری همهٔ state در دیسک."""
        try:
            self.cache.flush()
            self.project_memory.flush()
            self.global_memory.flush()

            # 🆕 flush PersistentMemory (no-op ولی مفید)
            if self.persistent_memory is not None:
                try:
                    self.persistent_memory.close()
                except Exception:
                    pass

            log_info(
                "💾 [SAFEBRAIN:flush] همه ذخیره شدند"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [SAFEBRAIN:flush] "
                f"{type(e).__name__}: {e}"
            )

    def close(self) -> None:
        """shutdown تمیز."""
        self.flush()
        log_info("🔌 [SAFEBRAIN:close] بسته شد")

    # ═══════════════════════════════════════════
    # debug
    # ═══════════════════════════════════════════
    def reset_stats(self) -> None:
        """پاک کردن آمار — برای تست."""
        with self._api_lock:
            self._api_calls_this_hour = 0
            self._api_hour_start = time.monotonic()
        self.stats = {
            "processed": 0,
            "cached": 0,
            "api_calls": 0,
            "api_blocked": 0,
            "blocked": 0,
            "errors": 0,
            "delegated": 0,
            "fallback": 0,
            "persistent_recall": 0,
            "persistent_remember": 0,
            "started_at": datetime.now().isoformat(),  # noqa: DTZ005
            "started_monotonic": time.monotonic(),
        }
        log_info("🗑️ [SAFEBRAIN:stats] reset شد")

    def get_stats(self) -> dict[str, Any]:
        """آمار کامل — برای debug."""
        brain_age = (
            time.monotonic() - self._brain_failed_at
            if self._brain_failed_at > 0
            else 0.0
        )

        processed = self.stats.get("processed", 0)
        delegated = self.stats.get("delegated", 0)
        delegated_rate = (
            round(delegated / processed, 3)
            if processed else 0.0
        )

        started = self.stats.get("started_monotonic", 0)
        uptime_s = (
            round(time.monotonic() - started, 1)
            if started else 0.0
        )

        stats_clean = {
            k: v
            for k, v in self.stats.items()
            if k != "started_monotonic"
        }

        # 🆕 آمار PersistentMemory
        persistent_stats: dict[str, Any] = {}
        if self.persistent_memory is not None:
            try:
                persistent_stats = (
                    self.persistent_memory.get_stats()
                )
            except Exception as e:
                persistent_stats = {
                    "error": f"{type(e).__name__}: {e}"
                }

        return {
            **stats_clean,
            "uptime_s": uptime_s,
            "cache": self.cache.stats(),
            "project_memory": self.project_memory.count,
            "global_memory": self.global_memory.count,
            "persistent_memory": persistent_stats,
            "api_calls_this_hour": self._api_calls_this_hour,
            "api_hour_started_ago_s": round(
                time.monotonic() - self._api_hour_start, 1
            ),
            "brain_connected": self._brain_instance is not None,
            "brain_failed": self._brain_failed_at > 0,
            "brain_retry_in_s": (
                max(
                    0,
                    int(_BRAIN_FAIL_RETRY_S - brain_age),
                )
                if self._brain_failed_at > 0
                else 0
            ),
            "ai_available": self._ai_assistant is not None,
            "delegated_rate": delegated_rate,
        }

    def __repr__(self) -> str:
        persistent = (
            "✅" if self.persistent_memory else "❌"
        )
        return (
            f"<SafeBrain "
            f"full_access={self.full_access} "
            f"api_key={'✅' if self.api_key else '❌'} "
            f"brain={'✅' if self._brain_instance else '—'} "
            f"persistent={persistent}>"
        )


# ═══════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════
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
            log_info(
                "🏗️ [SAFEBRAIN:singleton] ساخت نمونهٔ اول"
            )
            _shared_safe_brain = SafeBrain(
                api_key=api_key,
                full_access=full_access,
                use_api_memory=use_api_memory,
                memory_api_url=memory_api_url,
                memory_api_key=memory_api_key,
            )
        else:
            updated: list[str] = []
            if (
                full_access
                and not _shared_safe_brain.full_access
            ):
                _shared_safe_brain.full_access = True
                updated.append("full_access=True")
            if use_api_memory and memory_api_url:
                _shared_safe_brain.use_api_memory = True
                _shared_safe_brain.memory_api_url = (
                    memory_api_url
                )
                _shared_safe_brain.memory_api_key = (
                    memory_api_key
                )
                updated.append("memory_api")
            if updated:
                log_info(
                    f"♻️ [SAFEBRAIN:singleton] به‌روزرسانی"
                    f" | fields={updated}"
                )
    return _shared_safe_brain


def reset_safe_brain() -> None:
    """پاک‌کردن singleton — فقط برای تست."""
    global _shared_safe_brain
    with _shared_lock:
        if _shared_safe_brain is not None:
            try:
                _shared_safe_brain.close()
            except Exception:
                pass
            log_info("🗑️ [SAFEBRAIN:singleton] reset شد")
        _shared_safe_brain = None


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "SafeBrain",
    "get_safe_brain",
    "reset_safe_brain",
]