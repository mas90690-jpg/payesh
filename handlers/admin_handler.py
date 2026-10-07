"""
ChatOps admin handler — نسخهٔ ۱۳ (داده‌محور).

همهٔ لیست‌ها و پیام‌ها از data/ می‌آید:
  - data/configs/domain.json     → in_domain / anti_domain / reply
  - data/configs/messages.json   → empty_reply / fallback prefixes

نسخهٔ ۱۳ (این نسخه):
  - 🎯 `DomainGuard.config_path` حالا از `data_paths.DOMAIN` می‌آید
    (به‌جای hardcode کردن مسیر).
  - 🐛 FIX: `_is_safebrain_fallback` حالا fallback prefixes رو
    یک‌بار cache می‌کنه (به‌جای خواندن هر بار از فایل).
  - ✅ حفظ همهٔ رفتار نسخهٔ ۱۲.
"""
from __future__ import annotations

import os
import time
from typing import Any

from analyzers.keyword_finder import get_keyword_finder
from core.adapters import messenger_registry
from core.adapters.command_parser import (
    extract_value,
    parse_command,
    platform_in,
)
from core.contracts.base_handler import BaseHandler
from core.kernel.logger import log_error, log_info, log_success, log_warning
from core.safe_brain import SafeBrain, get_safe_brain
from core.services.ai_assistant import get_ai_assistant
from data.loader import load_json
from handlers._ai_utils import fa_platform, is_valid_ai_answer
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

_VALID_PLATFORMS = frozenset(PLATFORMS_ORDER)


# ═══════════════════════════════════════════
# Fallback prefixes cache (خوانده‌شده یک‌بار)
# ═══════════════════════════════════════════
_fallback_cache: dict | None = None


def _get_fallback_config() -> dict:
    """🎯 خواندن fallback prefixes از domain.json — با cache.

    Returns:
        dict با کلیدهای ai_fallback_prefixes، safebrain_prefixes،
        safebrain_min_real_len.
    """
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
# DomainGuard — داده‌محور
# ═══════════════════════════════════════════
class DomainGuard:
    """گارد حوزه — همه چیز از data/configs/domain.json.

    hot-reload هر ۱۲۰ ثانیه.
    اگر فایل نبود → گارد خاموش (همه قبول می‌شوند).
    """

    def __init__(self, config_path: str | None = None):
        # 🎯 FIX: از data_paths.DOMAIN
        self.config_path = (
            config_path or str(data_paths.DOMAIN)
        )
        self._data: dict = {}
        self._mtime: float = 0.0
        self._last_check: float = 0.0
        self._load(initial=True)

    def _load(self, initial: bool = False) -> None:
        """بارگذاری از فایل — بدون defaults در کد."""
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
        """آیا سؤال در حوزهٔ ربات است؟

        قواعد:
          ۱) گارد خاموش → True
          ۲) anti_domain اول چک می‌شود
          ۳) in_domain
          ۴) ناشناخته → False
        """
        if not text:
            return False

        self._maybe_reload()

        in_d = self.in_domain
        anti_d = self.anti_domain

        # ─── گارد خاموش ───
        if not in_d and not anti_d:
            return True

        text_lower = text.strip().lower()

        # ─── anti_domain اول ───
        for kw in anti_d:
            if kw and kw in text_lower:
                log_info(
                    f"🚫 [DOMAIN:anti] «{kw}» پیدا شد | "
                    f"text={text[:60]!r}"
                )
                return False

        # ─── in_domain ───
        for kw in in_d:
            if kw and kw in text_lower:
                return True

        return False

    def get_reply(self, query: str) -> str:
        """پیام آماده برای سؤال خارج از حوزه — از messages.json."""
        # ─── اول: از domain.json ───
        tpl = self._data.get("out_of_domain_reply", "")

        # ─── fallback: از messages.json ───
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
        """پیام خالی — از messages.json."""
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
    """ChatOps — داده‌محور. سختگیر: فقط خصوصی، فقط ADMIN_IDS."""

    def __init__(self, db, scorer=None, **kwargs):
        super().__init__()
        self.db = db
        self.kw = get_keyword_finder()
        self.ai = get_ai_assistant()

        # ─── db_path ───
        default_db_path = SQLITE_CONFIG.get(
            "database_path", str(data_paths.DB_FILE),
        )
        self.db_path = getattr(db, "db_path", default_db_path)

        # ─── api_key ───
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
            log_success(
                f"✅ [ADMIN:init] فعال | "
                f"platform={self.platform} | "
                f"admins={len(self.admin_ids)} | "
                f"platform_enabled={self._platform_enabled} | "
                f"api_key={self._api_key_source} | "
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
    # BaseHandler contract
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
        """تشخیص پاسخ «هوش در دسترس نیست» — با cache.

        🐛 FIX: از `_get_fallback_config()` استفاده می‌کند
        (به‌جای خواندن فایل در هر بار).
        """
        if not reply:
            return True

        s = reply.strip()
        if not s:
            return True

        cfg = _get_fallback_config()
        ai_fallbacks = cfg["ai_fallback_prefixes"]
        sb_prefixes = cfg["safebrain_prefixes"]
        min_len = cfg["safebrain_min_real_len"]

        # ─── AIAssistant fallback ───
        for prefix in ai_fallbacks:
            if prefix and s.startswith(prefix):
                return True

        # ─── SafeBrain prefix ───
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
    # Brain — lazy
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
    # entry point
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
        # ۱) pairing — اول از همه
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
        # ۳) ManualBrain
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
                    await self._reply(raw, manual_reply)
                    return
            except Exception as e:
                log_warning(
                    f"⚠️ [ADMIN:layer:manual] {type(e).__name__}: {e}"
                )

        # ═══════════════════════════════════════════
        # ۴) کاربر غیرمجاز
        # ═══════════════════════════════════════════
        if not is_admin:
            return

        # ═══════════════════════════════════════════
        # ۵) مسیر اصلی
        # ═══════════════════════════════════════════
        log_info(
            f"🛡️ [ADMIN:handle] دستور | {text[:_LOG_PREVIEW_LEN]!r}"
        )

        action, value = parse_command(text)

        try:
            if action is None:
                reply = await self._handle_free_text(text)
            else:
                reply = await self._handle_command(
                    action, value, text
                )
        except Exception as e:
            log_error(f"❌ [ADMIN:handle] {type(e).__name__}: {e}")
            reply = f"❌ خطا در اجرای دستور: {e}"

        if not reply:
            reply = self._domain.get_empty_reply()

        await self._reply(raw, reply)

        total_ms = (time.monotonic() - t0) * 1000
        if total_ms > _SLOW_HANDLE_MS:
            log_warning(
                f"🐌 [ADMIN:handle] کند | {total_ms:.0f}ms"
            )

    async def _handle_command(self, action, value, raw_text):
        t0 = time.monotonic()
        reply = None
        try:
            reply = await self.safe_brain.process(raw_text)
        except Exception as e:
            log_warning(f"⚠️ [ADMIN:safe_brain] {type(e).__name__}: {e}")

        if self._is_safebrain_fallback(reply):
            reply = await self._actions.execute(action, value, raw_text)

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

    async def process_text(self, text: str) -> str:
        """مسیر وب — برای /chat در OBS server."""
        text = (text or "").strip()
        if not text:
            return ""

        if not self._is_in_domain(text):
            return self._out_of_domain_reply(text)

        if self.manual_brain:
            try:
                manual_reply = await self.manual_brain.process(
                    text, sender="local_web", ai_fallback=self._ai_fallback,
                )
                if manual_reply:
                    return manual_reply
            except Exception as e:
                log_warning(f"⚠️ [ADMIN:web:manual] {type(e).__name__}: {e}")

        action, value = parse_command(text)

        try:
            if action is None:
                reply = await self._handle_free_text(text)
            else:
                reply = await self._handle_command(action, value, text)
            return reply or self._domain.get_empty_reply()
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
    # wrappers (backward-compat)
    # ═══════════════════════════════════════
    def _get_param(self, name):
        return self._actions.get_param(name)

    def _set_param(self, raw_text):
        return self._actions.set_param(raw_text)

    def get_domain_stats(self) -> dict:
        return self._domain.get_stats()