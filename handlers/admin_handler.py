"""
ChatOps admin handler — نسخهٔ ۱۷ (ترتیب درست: parse_command اول).

تغییرات نسخهٔ ۱۷:
  - 🎯 FIX بحرانی: در `process_text`، `parse_command` **قبل از** 
    `_recall_persistent` و `manual_brain` صدا زده می‌شود.
    قبلاً حافظه اول اجرا می‌شد و پاسخ غلط برمی‌گرداند.
  - 🎯 FIX بحرانی: در `handle`، `parse_command` **قبل از** 
    `_recall_persistent` و `manual_brain` صدا زده می‌شود.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۱۶.

نسخهٔ ۱۶:
  - 🐛 FIX: `_remember` پاسخ‌های fallback را ذخیره نمی‌کند.
  - 🐛 FIX: `_recall_persistent` پاسخ‌های fallback را رد می‌کند.
"""
from __future__ import annotations

import os
import time
from typing import Any

from analyzers.keyword_finder import get_keyword_finder
from core.adapters import messenger_registry
from core.adapters.command_parser import parse_command
from core.contracts.base_handler import BaseHandler
from core.kernel.logger import log_error, log_info, log_success, log_warning
from core.safe_brain import get_safe_brain
from core.services.ai_assistant import get_ai_assistant
from data.loader import load_json
from handlers._ai_utils import is_valid_ai_answer
from handlers.admin_actions import AdminActions
from handlers.admin_memory import AdminMemory
from handlers.keyword_admin import KeywordAdmin

from config import (
    ADMIN_ENABLED,
    ADMIN_IDS,
    ADMIN_PLATFORM,
    AI,
    MEMORY_MAX,
    PLATFORMS_ORDER,
    SQLITE_CONFIG,
)

from data import paths as data_paths


# ─── Optional imports (graceful) ───
try:
    from core.manual.brain import get_manual_brain
    _manual_brain_available = True
except ImportError as e:
    _manual_brain_available = False
    log_warning(f"⚠️ [ADMIN:init] manual_brain در دسترس نیست | {e}")

try:
    from core.brain import get_brain
    _brain_available = True
except ImportError as e:
    _brain_available = False
    log_warning(f"⚠️ [ADMIN:init] brain در دسترس نیست | {e}")


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _env_int(name, default, min_val=None, max_val=None) -> int:
    try:
        v = int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


def _env_float(name, default, min_val=None, max_val=None) -> float:
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
# ثابت‌ها (فقط env-based)
# ═══════════════════════════════════════════
_LOG_PREVIEW_LEN = _env_int("ADMIN_LOG_PREVIEW", 100, 20, 500)
_SLOW_HANDLE_MS = _env_int("ADMIN_SLOW_HANDLE_MS", 5000, 100, 60_000)
_SLOW_LAYER_MS = _env_int("ADMIN_SLOW_LAYER_MS", 3000, 100, 60_000)
_SLOW_REPLY_MS = _env_int("ADMIN_SLOW_REPLY_MS", 3000, 100, 60_000)
_REPLY_CHUNK = _env_int("ADMIN_REPLY_CHUNK", 3900, 500, 10_000)
_DOMAIN_RELOAD_SECONDS = _env_float(
    "ADMIN_DOMAIN_RELOAD_S", 120.0, 10.0, 3600.0
)

_PERSISTENT_ENABLED = _env_bool(
    "BRAIN_PERSISTENT_MEMORY", True
)
_PERSISTENT_REMEMBER = _env_bool(
    "SAFE_BRAIN_REMEMBER_CONVERSATIONS", True
)
_PERSISTENT_CATEGORY = "conversation"
_PERSISTENT_IMPORTANCE = _env_float(
    "ADMIN_PERSISTENT_IMPORTANCE", 0.5,
    min_val=0.0, max_val=1.0,
)

_VALID_PLATFORMS = frozenset(PLATFORMS_ORDER)


# ═══════════════════════════════════════════
# Fallback prefixes cache
# ═══════════════════════════════════════════
_fallback_cache: dict | None = None


def _get_fallback_config() -> dict:
    """خواندن fallback prefixes از domain.json — با cache."""
    global _fallback_cache
    if _fallback_cache is not None:
        return _fallback_cache

    try:
        domain = load_json("domain") or {}
    except Exception:
        domain = {}

    _fallback_cache = {
        "ai_fallback_prefixes": tuple(
            domain.get("ai_fallback_prefixes", [])
        ),
        "safebrain_prefixes": tuple(
            domain.get("safebrain_prefixes", [])
        ),
        "safebrain_min_real_len": int(
            domain.get("safebrain_min_real_len", 40)
        ),
    }
    return _fallback_cache


# ═══════════════════════════════════════════
# DomainGuard
# ═══════════════════════════════════════════
class DomainGuard:
    """گارد حوزه — همه چیز از data/configs/domain.json."""

    def __init__(self, config_path: str | None = None):
        self.config_path = (
            config_path or str(data_paths.DOMAIN)
        )
        self._data: dict = {}
        self._mtime: float = 0.0
        self._last_check: float = 0.0
        self._load(initial=True)

    def _load(self, initial: bool = False) -> None:
        if not os.path.exists(self.config_path):
            if initial:
                log_warning(
                    f"⚠️ [DOMAIN:init] فایل نیست → گارد خاموش | "
                    f"path={self.config_path}"
                )
            self._data = {}
            return

        try:
            self._mtime = os.path.getmtime(self.config_path)
            self._data = load_json("domain", force=True)

            if initial:
                log_success(
                    f"✅ [DOMAIN:init] بارگذاری | "
                    f"in={len(self.in_domain)} | "
                    f"anti={len(self.anti_domain)}"
                )
            else:
                log_info(
                    f"🔄 [DOMAIN:reload] in={len(self.in_domain)} | "
                    f"anti={len(self.anti_domain)}"
                )
        except Exception as e:
            log_warning(
                f"⚠️ [DOMAIN:load] {type(e).__name__}: {e}"
            )
            self._data = {}

    def _maybe_reload(self) -> None:
        now = time.monotonic()
        if now - self._last_check < _DOMAIN_RELOAD_SECONDS:
            return
        self._last_check = now
        try:
            current_mtime = (
                os.path.getmtime(self.config_path)
                if os.path.exists(self.config_path)
                else 0.0
            )
        except OSError:
            return
        if current_mtime != self._mtime:
            self._load(initial=False)

    @property
    def in_domain(self) -> tuple[str, ...]:
        return tuple(
            str(x).strip().lower()
            for x in self._data.get("in_domain", [])
            if str(x).strip()
        )

    @property
    def anti_domain(self) -> tuple[str, ...]:
        return tuple(
            str(x).strip().lower()
            for x in self._data.get("anti_domain", [])
            if str(x).strip()
        )

    def is_in_domain(self, text: str) -> bool:
        if not text:
            return False

        self._maybe_reload()

        in_d = self.in_domain
        anti_d = self.anti_domain

        if not in_d and not anti_d:
            return True

        text_lower = text.strip().lower()

        for kw in anti_d:
            if kw and kw in text_lower:
                log_info(
                    f"🚫 [DOMAIN:anti] «{kw}» پیدا شد | "
                    f"text={text[:60]!r}"
                )
                return False

        for kw in in_d:
            if kw and kw in text_lower:
                return True

        return False

    def get_reply(self, query: str) -> str:
        tpl = self._data.get("out_of_domain_reply", "")

        if not tpl:
            msgs = load_json("messages")
            tpl = msgs.get(
                "out_of_domain_reply",
                "🤖 «{query}» خارج از حوزهٔ من است.",
            )

        try:
            return tpl.format(query=query[:80])
        except (KeyError, IndexError):
            return tpl

    def get_empty_reply(self) -> str:
        msgs = load_json("messages")
        return msgs.get(
            "empty_reply",
            "🤔 متوجه نشدم. «راهنما» بزن.",
        )

    def get_stats(self) -> dict:
        return {
            "config_path": self.config_path,
            "in_domain_count": len(self.in_domain),
            "anti_domain_count": len(self.anti_domain),
            "enabled": bool(self.in_domain or self.anti_domain),
            "mtime": self._mtime,
            "reload_seconds": _DOMAIN_RELOAD_SECONDS,
        }


# ═══════════════════════════════════════════
# AdminHandler
# ═══════════════════════════════════════════
class AdminHandler(BaseHandler):
    """ChatOps — داده‌محور."""

    def __init__(self, db, scorer=None, **kwargs):
        super().__init__()
        self.db = db
        self.kw = get_keyword_finder()
        self.ai = get_ai_assistant()

        default_db_path = SQLITE_CONFIG.get(
            "database_path", str(data_paths.DB_FILE),
        )
        self.db_path = getattr(db, "db_path", default_db_path)

        api_key = (
            (AI or {}).get("deepseek_api_key")
            or os.environ.get("DEEPSEEK_API_KEY", "")
            or ""
        )
        self._api_key_source = (
            "config" if (AI or {}).get("deepseek_api_key")
            else ("env" if os.environ.get("DEEPSEEK_API_KEY") else "none")
        )
        self.safe_brain = get_safe_brain(api_key=api_key)

        # ═══════════════════════════════════════════
        # PersistentMemory
        # ═══════════════════════════════════════════
        self.persistent_memory: Any | None = None
        if _PERSISTENT_ENABLED:
            try:
                pm = getattr(
                    self.safe_brain, "persistent_memory", None
                )
                if pm is not None:
                    self.persistent_memory = pm
                    log_success(
                        f"✅ [ADMIN:init:persistent] اتصال | "
                        f"count={pm.count()}"
                    )
                else:
                    log_warning(
                        "⚠️ [ADMIN:init:persistent] "
                        "SafeBrain.persistent_memory = None"
                    )
            except Exception as e:
                log_warning(
                    f"⚠️ [ADMIN:init:persistent] "
                    f"{type(e).__name__}: {e}"
                )
                self.persistent_memory = None
        else:
            log_info(
                "ℹ️ [ADMIN:init:persistent] غیرفعال "
                "(BRAIN_PERSISTENT_MEMORY=false)"
            )

        log_info(
            f"🔑 [ADMIN:init:safebrain] api_key "
            f"source={self._api_key_source} | "
            f"has_key={'✅' if api_key else '❌'}"
        )

        # ─── ManualBrain ───
        self.manual_brain = None
        if _manual_brain_available:
            try:
                self.manual_brain = get_manual_brain(self.db_path)
                log_success("✅ [ADMIN:init] ManualBrain بارگذاری شد")
            except Exception as e:
                log_warning(
                    f"⚠️ [ADMIN:init] manual_brain init ناموفق | "
                    f"{type(e).__name__}: {e}"
                )

        # ─── Brain lazy ───
        self._brain: Any | None = None

        # ─── گارد حوزه ───
        self._domain = DomainGuard()
        log_success(
            f"✅ [ADMIN:init:domain] آماده | "
            f"in={len(self._domain.in_domain)} | "
            f"anti={len(self._domain.anti_domain)}"
        )

        # ─── config top-level ───
        self.enabled = bool(ADMIN_ENABLED)
        self.admin_ids = {
            str(x).strip()
            for x in ADMIN_IDS
            if x and str(x).strip()
        }
        self.platform = ADMIN_PLATFORM or "bale"

        if self.platform not in _VALID_PLATFORMS:
            log_warning(
                f"⚠️ [ADMIN:init] پلتفرم نامعتبر «{self.platform}» "
                f"→ پیش‌فرض bale"
            )
            self.platform = "bale"

        self._platform_enabled = self._check_platform_enabled(self.platform)

        # ─── دانش ───
        self._knowledge = self._load_knowledge()

        # ─── مسیرها ───
        custom_kw_path = str(data_paths.KEYWORDS_CUSTOM)
        memory_path = str(data_paths.AI_MEMORY)

        # ─── زیرماژول‌ها ───
        self._keyword_admin = KeywordAdmin(custom_kw_path)
        self._memory = AdminMemory(memory_path, MEMORY_MAX)
        self._actions = AdminActions(
            db=self.db,
            db_path=self.db_path,
            kw=self.kw,
            ai=self.ai,
            knowledge=self._knowledge,
            keyword_admin=self._keyword_admin,
            memory=self._memory,
            root=str(data_paths.PROJECT_ROOT),
            platform=self.platform,
            pid_file=str(data_paths.BOT_PID),
            get_requests_path=lambda: str(data_paths.REQUESTS_FILE),
            get_custom_kw_path=lambda: custom_kw_path,
        )

        if self.enabled:
            persistent_status = (
                "✅" if self.persistent_memory else "❌"
            )
            log_success(
                f"✅ [ADMIN:init] فعال | "
                f"platform={self.platform} | "
                f"admins={len(self.admin_ids)} | "
                f"platform_enabled={self._platform_enabled} | "
                f"api_key={self._api_key_source} | "
                f"memory_path={memory_path} | "
                f"persistent={persistent_status} | "
                f"domain_guard=✅"
            )
        else:
            log_info(
                f"⏭️ [ADMIN:init] غیرفعال | "
                f"platform={self.platform} | "
                f"admins={len(self.admin_ids)}"
            )

    @staticmethod
    def _check_platform_enabled(platform: str) -> bool:
        try:
            from config import PLUGINS_CONFIG
            return bool(
                PLUGINS_CONFIG.get(f"{platform}collector", {})
                .get("enabled", False)
            )
        except Exception:
            return False

    def _load_knowledge(self) -> str:
        path = str(data_paths.BOT_KNOWLEDGE)
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            log_info(
                f"📚 [ADMIN:init] knowledge بارگذاری شد | {len(text)}ch"
            )
            return text
        except FileNotFoundError:
            log_warning(f"⚠️ [ADMIN:init] knowledge نبود | {path}")
            return ""
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:init] knowledge | {type(e).__name__}: {e}"
            )
            return ""

    # ═══════════════════════════════════════
    # ذخیره در حافظه
    # ═══════════════════════════════════════
    def _remember(self, text: str, reply: str) -> None:
        if not text or not reply:
            return

        if self._is_safebrain_fallback(reply):
            log_info(
                f"⏭️ [ADMIN:memory] fallback — ذخیره نشد | "
                f"{reply[:60]!r}"
            )
            return

        try:
            self._memory.remember(text, reply)
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:memory] ذخیره JSONL ناموفق | "
                f"{type(e).__name__}: {e}"
            )

        if self.persistent_memory and _PERSISTENT_REMEMBER:
            try:
                self.persistent_memory.remember(
                    category=_PERSISTENT_CATEGORY,
                    key=text[:500],
                    value=reply[:10000],
                    importance=_PERSISTENT_IMPORTANCE,
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [ADMIN:memory:persistent] ذخیره ناموفق | "
                    f"{type(e).__name__}: {e}"
                )

    # ═══════════════════════════════════════
    # recall
    # ═══════════════════════════════════════
    def _recall_persistent(self, text: str) -> str | None:
        if not self.persistent_memory:
            return None
        if not text or len(text) < 3:
            return None

        try:
            results = self.persistent_memory.recall(
                query=text,
                category=_PERSISTENT_CATEGORY,
                limit=1,
            )
            if not results:
                return None

            best = results[0]
            answer = best.get("value") or ""
            if not answer or len(answer) < 10:
                return None

            if self._is_safebrain_fallback(answer):
                log_info(
                    f"⏭️ [ADMIN:recall:persistent] "
                    f"fallback در حافظه — رد شد | "
                    f"'{text[:40]}'"
                )
                return None

            log_success(
                f"🎯 [ADMIN:recall:persistent] '{text[:40]}' → "
                f"'{answer[:40]}' | "
                f"imp={best.get('importance', 0)}"
            )
            return answer
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:recall:persistent] "
                f"{type(e).__name__}: {e}"
            )
            return None

    # ═══════════════════════════════════════
    # BaseHandler
    # ═══════════════════════════════════════
    @property
    def handler_name(self) -> str:
        return "admin_handler"

    def can_handle(self, event_name: str) -> bool:
        return self.enabled and event_name == "message.received"

    # ═══════════════════════════════════════
    # SafeBrain fallback detection
    # ═══════════════════════════════════════
    @staticmethod
    def _is_safebrain_fallback(reply: str | None) -> bool:
        if not reply:
            return True

        s = reply.strip()
        if not s:
            return True

        cfg = _get_fallback_config()
        ai_fallbacks = cfg["ai_fallback_prefixes"]
        sb_prefixes = cfg["safebrain_prefixes"]
        min_len = cfg["safebrain_min_real_len"]

        for prefix in ai_fallbacks:
            if prefix and s.startswith(prefix):
                return True

        if sb_prefixes and s.startswith(sb_prefixes):
            return len(s) < min_len

        return False

    # ═══════════════════════════════════════
    # گارد حوزه
    # ═══════════════════════════════════════
    def _is_in_domain(self, text: str) -> bool:
        return self._domain.is_in_domain(text)

    def _out_of_domain_reply(self, query: str) -> str:
        return self._domain.get_reply(query)

    # ═══════════════════════════════════════
    # Brain lazy
    # ═══════════════════════════════════════
    def _get_brain(self):
        if not _brain_available:
            return None
        if self._brain is not None:
            return self._brain
        try:
            self._brain = get_brain(db=self.db)
            log_info("🏗️ [ADMIN:brain] Brain ساخته شد (lazy)")
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:brain] ساخت ناموفق | "
                f"{type(e).__name__}: {e}"
            )
            self._brain = None
        return self._brain

    # ═══════════════════════════════════════
    # AI fallback
    # ═══════════════════════════════════════
    async def _ai_fallback(self, question: str) -> str | None:
        log_info(
            f"🧠 [ADMIN:fallback] شروع | "
            f"q={question[:_LOG_PREVIEW_LEN]!r}"
        )
        t0 = time.monotonic()

        # ─── لایهٔ ۱: Brain ───
        brain = self._get_brain()
        if brain:
            try:
                t_layer = time.monotonic()
                ans = await brain.process_admin_command(question)
                layer_ms = (time.monotonic() - t_layer) * 1000

                if ans and is_valid_ai_answer(ans):
                    log_info(
                        f"✅ [ADMIN:fallback:brain] پاسخ | "
                        f"{layer_ms:.0f}ms | len={len(ans)}"
                    )
                    return ans
                log_info(
                    f"ℹ️ [ADMIN:fallback:brain] نامعتبر/خالی | "
                    f"{layer_ms:.0f}ms"
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [ADMIN:fallback:brain] "
                    f"{type(e).__name__}: {e}"
                )

        # ─── لایهٔ ۲: AIAssistant ───
        try:
            from core.services.ai_assistant import PERSONA
            system = (
                PERSONA
                + "\n\n# دانشِ ربات:\n" + self._knowledge
                + "\n\n# وضعیتِ زندهٔ همین الان (از دیتابیس):\n"
                + self._actions.live_context()
                + "\n\n# قاعدهٔ حوزه:\n"
                + "تو فقط ربات پایش سرنخ هستی. اگر سؤال خارج از "
                + "این حوزه بود (هوا، دلار، بورس، اخبار، ورزش، …) "
                + "صریح بگو «خارج از حوزهٔ من است» و لیست کارهایی که "
                + "می‌توانی بکنی را بده."
            )
            t_layer = time.monotonic()
            ans = await self.ai.chat(question, system=system)
            layer_ms = (time.monotonic() - t_layer) * 1000

            if ans and is_valid_ai_answer(ans):
                log_info(
                    f"✅ [ADMIN:fallback:ai] پاسخ | "
                    f"{layer_ms:.0f}ms | len={len(ans)}"
                )
                return ans
            log_info(
                f"ℹ️ [ADMIN:fallback:ai] نامعتبر/خالی | "
                f"{layer_ms:.0f}ms"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:fallback:ai] {type(e).__name__}: {e}"
            )

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_warning(
            f"❌ [ADMIN:fallback] هیچ لایه‌ای جواب نداد | "
            f"{elapsed_ms:.0f}ms"
        )
        return None

    # ═══════════════════════════════════════
    # entry point (بله) — نسخهٔ ۱۷
    # ═══════════════════════════════════════
    async def handle(self, event):
        t0 = time.monotonic()
        raw = event.data if isinstance(event.data, dict) else {}

        if raw.get("platform") != self.platform:
            return

        sender = str(raw.get("user_id") or "").strip()
        chat_id = str(raw.get("chat_id") or "").strip()
        ct = (raw.get("chat_type") or "").lower()
        is_admin = sender in self.admin_ids

        # ─── فقط خصوصی ───
        is_private = (
            ct in ("private", "user")
            or (not ct and chat_id == sender)
        )
        if not is_private:
            return

        text = (raw.get("text") or raw.get("caption") or "").strip()
        if not text:
            return

        # ═══════════════════════════════════════════
        # ۱) pairing
        # ═══════════════════════════════════════════
        if not self.admin_ids:
            log_warning(
                f"🔑 [ADMIN:pairing] platform={self.platform} "
                f"user_id={sender}"
            )
            await self._reply(
                raw,
                f"👋 آیدیِ تو: {sender}\n"
                f"آن را در .env بگذار:\nADMIN_IDS={sender}\n"
                f"بعد ربات را ری‌استارت کن تا مدیر شوی.",
            )
            return

        # ═══════════════════════════════════════════
        # ۲) گارد حوزه
        # ═══════════════════════════════════════════
        if not self._is_in_domain(text):
            log_info(
                f"🚫 [ADMIN:handle] خارج از حوزه | "
                f"text={text[:_LOG_PREVIEW_LEN]!r}"
            )
            if is_admin:
                reply = self._out_of_domain_reply(text)
                await self._reply(raw, reply)
            return

        # ═══════════════════════════════════════════
        # ★ ۲.۵) parse_command — اکشن قطعی، اول از همه ★
        # ═══════════════════════════════════════════
        if is_admin:
            action_early, value_early = parse_command(text)
            if action_early is not None:
                log_info(
                    f"🎬 [ADMIN:handle:action] {action_early} | "
                    f"text={text[:_LOG_PREVIEW_LEN]!r}"
                )
                try:
                    reply = await self._handle_command(
                        action_early, value_early, text
                    )
                    reply = reply or self._domain.get_empty_reply()
                    self._remember(text, reply)
                    await self._reply(raw, reply)
                    return
                except Exception as e:
                    log_error(
                        f"❌ [ADMIN:handle:action] "
                        f"{type(e).__name__}: {e}"
                    )
                    await self._reply(raw, f"❌ خطا در اجرای دستور: {e}")
                    return

        # ═══════════════════════════════════════════
        # ۳) PersistentMemory — فقط برای چت
        # ═══════════════════════════════════════════
        if is_admin:
            cached = self._recall_persistent(text)
            if cached:
                elapsed_ms = (time.monotonic() - t0) * 1000
                log_success(
                    f"✅ [ADMIN:handle:persistent] "
                    f"پاسخ از حافظه | {elapsed_ms:.0f}ms"
                )
                await self._reply(raw, cached)
                return

        # ═══════════════════════════════════════════
        # ۴) ManualBrain
        # ═══════════════════════════════════════════
        if is_admin and self.manual_brain:
            try:
                t_layer = time.monotonic()
                manual_reply = await self.manual_brain.process(
                    text, sender=sender, ai_fallback=self._ai_fallback,
                )
                layer_ms = (time.monotonic() - t_layer) * 1000

                if manual_reply:
                    log_success(
                        f"✅ [ADMIN:layer:manual] پاسخ | "
                        f"{layer_ms:.0f}ms"
                    )
                    self._remember(text, manual_reply)
                    await self._reply(raw, manual_reply)
                    return
            except Exception as e:
                log_warning(
                    f"⚠️ [ADMIN:layer:manual] {type(e).__name__}: {e}"
                )

        # ═══════════════════════════════════════════
        # ۵) کاربر غیرمجاز
        # ═══════════════════════════════════════════
        if not is_admin:
            return

        # ═══════════════════════════════════════════
        # ۶) free text
        # ═══════════════════════════════════════════
        log_info(
            f"🛡️ [ADMIN:handle:free] {text[:_LOG_PREVIEW_LEN]!r}"
        )

        try:
            reply = await self._handle_free_text(text)
        except Exception as e:
            log_error(f"❌ [ADMIN:handle:free] {type(e).__name__}: {e}")
            reply = f"❌ خطا: {e}"

        if not reply:
            reply = self._domain.get_empty_reply()

        self._remember(text, reply)

        await self._reply(raw, reply)

        total_ms = (time.monotonic() - t0) * 1000
        if total_ms > _SLOW_HANDLE_MS:
            log_warning(
                f"🐌 [ADMIN:handle] کند | {total_ms:.0f}ms"
            )

    async def _handle_command(self, action, value, raw_text):
        _DIRECT_ACTIONS = {
            "start_platform",
            "stop_platform",
            "search_platform",
            "join_group",
            "status",
        }

        if action in _DIRECT_ACTIONS:
            log_info(
                f"🎯 [ADMIN:handle:direct] action={action} | "
                f"value={value!r}"
            )
            try:
                reply = await self._actions.execute(
                    action, value, raw_text
                )
                return reply or self._domain.get_empty_reply()
            except Exception as e:
                log_error(
                    f"❌ [ADMIN:handle:direct] {action} | "
                    f"{type(e).__name__}: {e}"
                )
                return f"❌ خطا: {e}"

        reply = None
        try:
            reply = await self.safe_brain.process(raw_text)
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:safe_brain] {type(e).__name__}: {e}"
            )

        if self._is_safebrain_fallback(reply):
            reply = await self._actions.execute(
                action, value, raw_text
            )

        return reply or self._domain.get_empty_reply()

    async def _handle_free_text(self, text: str) -> str:
        # ─── لایهٔ ۱: SafeBrain ───
        try:
            reply = await self.safe_brain.process(text)
            if reply and not self._is_safebrain_fallback(reply):
                return reply
        except Exception as e:
            log_warning(f"⚠️ [ADMIN:free] safe_brain | {type(e).__name__}: {e}")

        # ─── لایهٔ ۲: Brain ───
        brain = self._get_brain()
        if brain:
            try:
                ans = await brain.process_admin_command(text)
                if ans and is_valid_ai_answer(ans):
                    return ans
            except Exception as e:
                log_warning(f"⚠️ [ADMIN:free] brain | {type(e).__name__}: {e}")

        # ─── لایهٔ ۳: AIAssistant ───
        try:
            ans = await self._actions.ask(text)
            if ans and is_valid_ai_answer(ans):
                return ans
        except Exception as e:
            log_warning(f"⚠️ [ADMIN:free] ask | {type(e).__name__}: {e}")

        return self._domain.get_empty_reply()

    # ═══════════════════════════════════════
    # entry point (وب) — نسخهٔ ۱۷
    # ═══════════════════════════════════════
    async def process_text(self, text: str) -> str:
        """مسیر وب — برای /chat در OBS server.

        نسخهٔ ۱۷: parse_command قبل از حافظه و ManualBrain.
        """
        text = (text or "").strip()
        if not text:
            return ""

        # ═══════════════════════════════════════
        # گارد حوزه
        # ═══════════════════════════════════════
        if not self._is_in_domain(text):
            log_info(
                f"🚫 [ADMIN:web] خارج از حوزه | "
                f"text={text[:_LOG_PREVIEW_LEN]!r}"
            )
            reply = self._out_of_domain_reply(text)
            self._remember(text, reply)
            return reply

        # ═══════════════════════════════════════
        # ★ ۱) parse_command — اکشن قطعی، قبل از حافظه ★
        # ═══════════════════════════════════════
        action, value = parse_command(text)
        if action is not None:
            log_info(
                f"🎬 [ADMIN:web:action] {action} | "
                f"text={text[:_LOG_PREVIEW_LEN]!r}"
            )
            try:
                reply = await self._handle_command(action, value, text)
                reply = reply or self._domain.get_empty_reply()
                self._remember(text, reply)
                return reply
            except Exception as e:
                log_error(
                    f"❌ [ADMIN:web:action] {type(e).__name__}: {e}"
                )
                return f"❌ خطا در اجرای دستور: {e}"

        # ═══════════════════════════════════════
        # ۲) PersistentMemory — فقط برای چت
        # ═══════════════════════════════════════
        cached = self._recall_persistent(text)
        if cached:
            log_success("✅ [ADMIN:web:persistent] پاسخ از حافظه")
            return cached

        # ═══════════════════════════════════════
        # ۳) ManualBrain
        # ═══════════════════════════════════════
        if self.manual_brain:
            try:
                manual_reply = await self.manual_brain.process(
                    text, sender="local_web",
                    ai_fallback=self._ai_fallback,
                )
                if manual_reply:
                    log_success("✅ [ADMIN:web:manual] پاسخ")
                    self._remember(text, manual_reply)
                    return manual_reply
            except Exception as e:
                log_warning(
                    f"⚠️ [ADMIN:web:manual] {type(e).__name__}: {e}"
                )

        # ═══════════════════════════════════════
        # ۴) free text (AI fallback)
        # ═══════════════════════════════════════
        try:
            reply = await self._handle_free_text(text)
            reply = reply or self._domain.get_empty_reply()
            self._remember(text, reply)
            return reply
        except Exception as e:
            log_error(f"❌ [ADMIN:web] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════
    # reply
    # ═══════════════════════════════════════
    async def _reply(self, raw: dict, text: str):
        sender = messenger_registry.get_sender(self.platform)
        if not sender:
            log_warning(f"⚠️ [ADMIN:reply] sender نیست برای {self.platform}")
            return

        chat_id = raw.get("chat_id") or raw.get("user_id")
        ct = raw.get("chat_type")
        uid = raw.get("user_id")

        if ct in ("private", "user"):
            ct = "private"
        elif uid is not None and str(chat_id) == str(uid):
            ct = "private"

        if not text:
            return

        chunks = self._chunk_text(text, _REPLY_CHUNK)

        for i, chunk in enumerate(chunks):
            try:
                await sender(chat_id, ct, chunk)
            except Exception as e:
                log_error(
                    f"❌ [ADMIN:reply] chunk {i+1}/{len(chunks)} | "
                    f"{type(e).__name__}: {e}"
                )

    @staticmethod
    def _chunk_text(text: str, size: int) -> list[str]:
        if not text:
            return [""]
        if len(text) <= size:
            return [text]

        chunks = []
        remaining = text
        while remaining:
            if len(remaining) <= size:
                chunks.append(remaining)
                break
            cut = remaining.rfind("\n", 0, size)
            if cut < size // 2:
                cut = remaining.rfind(" ", 0, size)
            if cut < size // 2:
                cut = size
            chunks.append(remaining[:cut])
            remaining = remaining[cut:].lstrip("\n")

        return chunks

    # ═══════════════════════════════════════
    # wrappers
    # ═══════════════════════════════════════
    def _get_param(self, name):
        return self._actions.get_param(name)

    def _set_param(self, raw_text):
        return self._actions.set_param(raw_text)

    def get_domain_stats(self) -> dict:
        return self._domain.get_stats()

    # ═══════════════════════════════════════
    # debug
    # ═══════════════════════════════════════
    def get_persistent_stats(self) -> dict:
        if not self.persistent_memory:
            return {"enabled": False}
        try:
            return {
                "enabled": True,
                **self.persistent_memory.get_stats(),
            }
        except Exception as e:
            return {
                "enabled": True,
                "error": f"{type(e).__name__}: {e}",
            }

    def get_stats(self) -> dict:
        base = super().get_stats()
        return {
            **base,
            "domain": self._domain.get_stats(),
            "persistent_memory": self.get_persistent_stats(),
            "admin_ids_count": len(self.admin_ids),
            "platform": self.platform,
        }