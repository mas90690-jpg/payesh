"""
ChatOps admin handler — نسخهٔ ۱۰ (هماهنگ با config.AI + fallback صحیح).

این فایل فقط coordinator است:
  - گیت‌های امنیتی (خصوصی + ADMIN_IDS)
  - مسیرهای handle / process_text
  - ارسال پاسخ

منطق به این ماژول‌ها واگذار شده:
  - core/command_parser.py      → parse دستور
  - handlers/keyword_admin.py   → CRUD کلیدواژه
  - handlers/admin_memory.py    → حافظهٔ گفتگو
  - handlers/admin_actions.py   → اجرای دستورها

نسخهٔ ۱۰ (این نسخه):
  - 🐛 FIX: `api_key` از `config.AI["deepseek_api_key"]` می‌آید
    (نه `os.environ` مستقیم) — سازگار با hot-reload.
  - 🐛 FIX: `_is_safebrain_fallback` حالا هم prefix قدیمی
    («⚠️ هوش در دسترس نیست») و هم prefix SafeBrain را می‌شناسد.
  - 🐛 FIX: `_SAFEBRAIN_PREFIXES_ANY` به‌عنوان tuple واحد
    (به‌جای جستجوی دوگانه).
  - 🐛 FIX: `_SAFEBRAIN_MIN_REAL_LEN` حالا env-based.
  - 🎯 همهٔ ثابت‌ها حالا از env می‌آیند (throttle، timeout، length).
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۹.
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
from handlers.admin_actions import AdminActions
from handlers.admin_memory import AdminMemory
from handlers.keyword_admin import KeywordAdmin

# 🎯 P0: استفاده از config برای top-level params
from config import (
    ADMIN_ENABLED,
    ADMIN_IDS,
    ADMIN_PLATFORM,
    AI,
    MEMORY_MAX,
    PLATFORMS_ORDER,
    SQLITE_CONFIG,
)

# 🎯 P0: مسیرها از data.paths
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
# helpers (env-based)
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


# ═══════════════════════════════════════════
# ثابت‌ها (env-based)
# ═══════════════════════════════════════════
_LOG_PREVIEW_LEN: int = _env_int(
    "ADMIN_LOG_PREVIEW", 100, min_val=20, max_val=500
)
_SLOW_HANDLE_MS: int = _env_int(
    "ADMIN_SLOW_HANDLE_MS", 5000, min_val=100, max_val=60_000
)
_SLOW_LAYER_MS: int = _env_int(
    "ADMIN_SLOW_LAYER_MS", 3000, min_val=100, max_val=60_000
)
_SLOW_REPLY_MS: int = _env_int(
    "ADMIN_SLOW_REPLY_MS", 3000, min_val=100, max_val=60_000
)

_REPLY_CHUNK: int = _env_int(
    "ADMIN_REPLY_CHUNK", 3900, min_val=500, max_val=10_000
)

_SAFEBRAIN_PREFIXES_FALLBACK: tuple[str, ...] = (
    "🤖 SafeBrain",
    "🤖 WinDoctor",
)

_AI_FALLBACK_PREFIXES: tuple[str, ...] = (
    "⚠️ هوش در دسترس نیست",
    "🤖 SafeBrain: هوش در دسترس نیست",
)

_SAFEBRAIN_MIN_REAL_LEN: int = _env_int(
    "ADMIN_SAFEBRAIN_MIN_REAL_LEN", 40,
    min_val=5, max_val=500,
)

_EMPTY_REPLY_FALLBACK = (
    "🤔 متوجه نشدم یا پاسخی پیدا نشد.\n"
    "می‌توانی:\n"
    "• «راهنما» بزنی\n"
    "• «وضعیت» یا «گزارش» بپرسی\n"
    "• واضح‌تر بپرسی"
)

_VALID_PLATFORMS: frozenset = frozenset(PLATFORMS_ORDER)


def _get_safebrain_prefixes() -> tuple[str, ...]:
    """🎯 FIX: prefixهای پویا از SafeBrain."""
    try:
        from core.safe_brain.main import SafeBrain as _SB
        prefixes = getattr(_SB, "_fallback_prefixes", None)
        if prefixes:
            return tuple(prefixes)
    except Exception:
        pass
    return _SAFEBRAIN_PREFIXES_FALLBACK


def _get_all_fallback_prefixes() -> tuple[str, ...]:
    """🎯 FIX: ترکیب همهٔ prefixهای ممکن."""
    return _get_safebrain_prefixes() + _AI_FALLBACK_PREFIXES


class AdminHandler(BaseHandler):
    """ChatOps: تنها مسیر دستور دادن. سختگیر: فقط خصوصی، فقط ADMIN_IDS."""

    def __init__(self, db, scorer=None, **kwargs):
        super().__init__()
        self.db = db
        self.kw = get_keyword_finder()
        self.ai = get_ai_assistant()

        # 🎯 FIX: db_path از db یا config
        default_db_path = SQLITE_CONFIG.get(
            "database_path",
            str(data_paths.DB_FILE),
        )
        self.db_path = getattr(db, "db_path", default_db_path)

        # ═══════════════════════════════════════════
        # 🎯 FIX مهم: api_key از config.AI (منبع یکتا)
        # ═══════════════════════════════════════════
        api_key: str = (
            (AI or {}).get("deepseek_api_key")
            or os.environ.get("DEEPSEEK_API_KEY", "")
            or ""
        )
        self._api_key_source: str = (
            "config" if (AI or {}).get("deepseek_api_key")
            else ("env" if os.environ.get("DEEPSEEK_API_KEY") else "none")
        )
        self.safe_brain: SafeBrain = get_safe_brain(api_key=api_key)

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

        # ─── Brain — per-instance (نه global) ───
        self._brain: Any | None = None

        # 🎯 FIX: از config top-level (نه PLUGINS_CONFIG)
        self.enabled = bool(ADMIN_ENABLED)
        self.admin_ids = {
            str(x).strip()
            for x in ADMIN_IDS
            if x and str(x).strip()
        }
        self.platform = ADMIN_PLATFORM or "bale"

        # چک: پلتفرم معتبر؟
        if self.platform not in _VALID_PLATFORMS:
            log_warning(
                f"⚠️ [ADMIN:init] پلتفرم نامعتبر «{self.platform}» "
                f"→ پیش‌فرض bale"
            )
            self.platform = "bale"

        # چک: پلتفرم فعال است؟
        self._platform_enabled = self._check_platform_enabled(
            self.platform
        )
        if not self._platform_enabled and self.enabled:
            log_warning(
                f"⚠️ [ADMIN:init] پلتفرم مدیر «{self.platform}» غیرفعال است "
                f"ولی ADMIN_ENABLED=true → پاسخ‌ها ارسال نمی‌شوند. "
                f"{self.platform.upper()}_ENABLED=true کن یا "
                f"ADMIN_PLATFORM را عوض کن."
            )

        # ─── دانش ───
        self._knowledge = self._load_knowledge()

        # 🎯 P0: مسیرها از data.paths
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

        # لاگ نهایی
        if self.enabled:
            log_success(
                f"✅ [ADMIN:init] فعال | "
                f"platform={self.platform} | "
                f"admins={len(self.admin_ids)} | "
                f"platform_enabled={self._platform_enabled} | "
                f"api_key={self._api_key_source}"
            )
        else:
            log_info(
                f"⏭️ [ADMIN:init] غیرفعال | "
                f"platform={self.platform} | "
                f"admins={len(self.admin_ids)}"
            )

    @staticmethod
    def _check_platform_enabled(platform: str) -> bool:
        """بررسی فعال بودن پلتفرم — از config."""
        try:
            from config import PLUGINS_CONFIG
            return bool(
                PLUGINS_CONFIG.get(f"{platform}collector", {})
                .get("enabled", False)
            )
        except Exception:
            return False

    # ═══════════════════════════════════════
    # knowledge
    # ═══════════════════════════════════════
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
        """🎯 FIX: تشخیص پاسخ «هوش در دسترس نیست»."""
        if not reply:
            return True

        s = reply.strip()
        if not s:
            return True

        # ─── حالت ۲: AIAssistant fallback ───
        for prefix in _AI_FALLBACK_PREFIXES:
            if s.startswith(prefix):
                return True

        # ─── حالت ۳: SafeBrain prefix ───
        sb_prefixes = _get_safebrain_prefixes()
        if s.startswith(sb_prefixes):
            return len(s) < _SAFEBRAIN_MIN_REAL_LEN

        # ─── حالت ۴: پاسخ واقعی ───
        return False

    # ═══════════════════════════════════════
    # Brain — lazy + per-instance
    # ═══════════════════════════════════════
    def _get_brain(self):
        """Lazy ساخت Brain (per-instance، نه global)."""
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
    # AI fallback (for manual_brain)
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

                if ans and self._is_valid_ai_answer(ans):
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
            )
            t_layer = time.monotonic()
            ans = await self.ai.chat(question, system=system)
            layer_ms = (time.monotonic() - t_layer) * 1000

            if ans and self._is_valid_ai_answer(ans):
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

    @staticmethod
    def _is_valid_ai_answer(ans: str | None) -> bool:
        """پاسخ AI معتبر است یا پیام خطا؟"""
        if not ans:
            return False
        s = str(ans).strip()
        if not s:
            return False
        _bad_prefixes = ("⚠️", "❌", "🚫", "⛔")
        if s.startswith(_bad_prefixes):
            return False
        _bad_markers = (
            "در دسترس نیست",
            "خطای API",
            "Error code:",
            "rate limit",
            "timed out",
        )
        for m in _bad_markers:
            if m in s:
                return False
        return True

    # ═══════════════════════════════════════
    # entry point (messenger)
    # ═══════════════════════════════════════
    async def handle(self, event):
        t0 = time.monotonic()
        raw = event.data if isinstance(event.data, dict) else {}

        # ─── فیلتر پلتفرم ───
        if raw.get("platform") != self.platform:
            return

        sender = str(raw.get("user_id") or "").strip()
        chat_id = str(raw.get("chat_id") or "").strip()
        ct = (raw.get("chat_type") or "").lower()
        is_admin = sender in self.admin_ids

        if is_admin:
            log_info(
                f"🛡️ [ADMIN:handle] پیام | chat_type={ct!r} "
                f"chat_id={chat_id} sender={sender}"
            )

        # ─── فقط خصوصی ───
        is_private = (
            ct in ("private", "user")
            or (not ct and chat_id == sender)
        )
        if not is_private:
            if is_admin:
                log_info(
                    f"⏭️ [ADMIN:handle] غیرخصوصی — رد | "
                    f"chat_type={ct!r} chat_id={chat_id}"
                )
            return

        text = (raw.get("text") or raw.get("caption") or "").strip()
        if not text:
            return

        # ─── لایهٔ ManualBrain ───
        if self.admin_ids and is_admin and self.manual_brain:
            try:
                t_layer = time.monotonic()
                manual_reply = await self.manual_brain.process(
                    text, sender=sender, ai_fallback=self._ai_fallback,
                )
                layer_ms = (time.monotonic() - t_layer) * 1000

                if manual_reply:
                    log_success(
                        f"✅ [ADMIN:layer:manual] پاسخ | "
                        f"{layer_ms:.0f}ms | len={len(manual_reply)}"
                    )
                    await self._reply(raw, manual_reply)
                    total_ms = (time.monotonic() - t0) * 1000
                    log_info(
                        f"🏁 [ADMIN:handle] کامل | total={total_ms:.0f}ms"
                    )
                    return

                log_info(
                    f"ℹ️ [ADMIN:layer:manual] نداد | "
                    f"{layer_ms:.0f}ms — واگذاری به لایهٔ بعد"
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [ADMIN:layer:manual] {type(e).__name__}: {e}"
                )

        # ─── pairing ───
        if not self.admin_ids:
            log_warning(
                f"🔑 [ADMIN:pairing] platform={self.platform} "
                f"user_id={sender} | .env → ADMIN_IDS={sender}"
            )
            await self._reply(
                raw,
                f"👋 آیدیِ تو: {sender}\n"
                f"آن را در .env بگذار:\nADMIN_IDS={sender}\n"
                f"بعد ربات را ری‌استارت کن تا مدیر شوی.",
            )
            return

        if not is_admin:
            log_info(
                f"⏭️ [ADMIN:handle] کاربر غیرمجاز | sender={sender}"
            )
            return

        # ─── مسیر اصلی ───
        log_info(
            f"🛡️ [ADMIN:handle] دستور | {text[:_LOG_PREVIEW_LEN]!r}"
        )

        action, value = parse_command(text)
        log_info(f"🎯 [ADMIN:parse] action={action} value={value!r}")

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
            log_info("ℹ️ [ADMIN:handle] reply خالی — پیام پیش‌فرض")
            reply = _EMPTY_REPLY_FALLBACK

        await self._reply(raw, reply)

        total_ms = (time.monotonic() - t0) * 1000
        if total_ms > _SLOW_HANDLE_MS:
            log_warning(
                f"🐌 [ADMIN:handle] کند | {total_ms:.0f}ms | "
                f"action={action}"
            )
        else:
            log_info(
                f"🏁 [ADMIN:handle] کامل | total={total_ms:.0f}ms | "
                f"reply_len={len(reply or '')}"
            )

    # ═══════════════════════════════════════
    # routing
    # ═══════════════════════════════════════
    async def _handle_command(
        self, action: str, value, raw_text: str
    ) -> str:
        t0 = time.monotonic()

        reply: str | None = None
        try:
            t_sb = time.monotonic()
            reply = await self.safe_brain.process(raw_text)
            sb_ms = (time.monotonic() - t_sb) * 1000
            log_info(
                f"🧠 [ADMIN:safe_brain] {sb_ms:.0f}ms | "
                f"reply={'✅' if reply else '❌ خالی'}"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:safe_brain] {type(e).__name__}: {e}"
            )
            reply = None

        if self._is_safebrain_fallback(reply):
            t_exec = time.monotonic()
            reply = await self._actions.execute(action, value, raw_text)
            exec_ms = (time.monotonic() - t_exec) * 1000
            log_info(
                f"⚙️ [ADMIN:execute] {exec_ms:.0f}ms | "
                f"action={action} | len={len(reply or '')}"
            )

        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms > _SLOW_LAYER_MS:
            log_warning(
                f"🐌 [ADMIN:handle_command] کند | {elapsed_ms:.0f}ms"
            )

        return reply or _EMPTY_REPLY_FALLBACK

    async def _handle_free_text(self, text: str) -> str:
        t0 = time.monotonic()

        # ─── لایهٔ ۱: SafeBrain ───
        try:
            reply = await self.safe_brain.process(text)
            if reply and not self._is_safebrain_fallback(reply):
                log_info(
                    f"✅ [ADMIN:free] safe_brain پاسخ | "
                    f"{time.monotonic() - t0:.2f}s"
                )
                return reply
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:free] safe_brain | "
                f"{type(e).__name__}: {e}"
            )

        # ─── لایهٔ ۲: Brain ───
        brain = self._get_brain()
        if brain:
            try:
                t_b = time.monotonic()
                ans = await brain.process_admin_command(text)
                b_ms = (time.monotonic() - t_b) * 1000
                if ans and self._is_valid_ai_answer(ans):
                    log_info(
                        f"✅ [ADMIN:free] brain پاسخ | {b_ms:.0f}ms"
                    )
                    return ans
                log_info(
                    f"ℹ️ [ADMIN:free] brain نامعتبر | {b_ms:.0f}ms"
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [ADMIN:free] brain | {type(e).__name__}: {e}"
                )

        # ─── لایهٔ ۳: AIAssistant ───
        log_info("🔄 [ADMIN:free] → _ask (AIAssistant)")
        try:
            ans = await self._actions.ask(text)
            if ans and self._is_valid_ai_answer(ans):
                return ans
        except Exception as e:
            log_warning(
                f"⚠️ [ADMIN:free] ask | {type(e).__name__}: {e}"
            )

        log_warning(
            f"❌ [ADMIN:free] هیچ لایه‌ای پاسخ نداد | "
            f"{time.monotonic() - t0:.2f}s"
        )
        return _EMPTY_REPLY_FALLBACK

    # ═══════════════════════════════════════
    # web path
    # ═══════════════════════════════════════
    async def process_text(self, text: str) -> str:
        """مسیر وب (بدون messenger) — برای /chat در OBS server."""
        text = (text or "").strip()
        if not text:
            return ""

        log_info(
            f"🌐 [ADMIN:web] درخواست | "
            f"{text[:_LOG_PREVIEW_LEN]!r}"
        )
        t0 = time.monotonic()

        # ─── لایهٔ ManualBrain ───
        if self.manual_brain:
            try:
                t_layer = time.monotonic()
                manual_reply = await self.manual_brain.process(
                    text,
                    sender="local_web",
                    ai_fallback=self._ai_fallback,
                )
                layer_ms = (time.monotonic() - t_layer) * 1000
                if manual_reply:
                    log_success(
                        f"✅ [ADMIN:web:manual] پاسخ | {layer_ms:.0f}ms"
                    )
                    return manual_reply
            except Exception as e:
                log_warning(
                    f"⚠️ [ADMIN:web:manual] {type(e).__name__}: {e}"
                )

        # ─── parse یک‌بار ───
        action, value = parse_command(text)
        log_info(f"🎯 [ADMIN:web:parse] action={action}")

        try:
            if action is None:
                reply = await self._handle_free_text(text)
            else:
                reply = await self._handle_command(
                    action, value, text
                )
            total_ms = (time.monotonic() - t0) * 1000
            log_info(
                f"🏁 [ADMIN:web] کامل | {total_ms:.0f}ms | "
                f"len={len(reply or '')}"
            )
            return reply or _EMPTY_REPLY_FALLBACK
        except Exception as e:
            log_error(f"❌ [ADMIN:web] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════
    # reply
    # ═══════════════════════════════════════
    async def _reply(self, raw: dict, text: str):
        t0 = time.monotonic()
        sender = messenger_registry.get_sender(self.platform)
        if not sender:
            log_warning(
                f"⚠️ [ADMIN:reply] sender نیست برای {self.platform}"
            )
            return

        chat_id = raw.get("chat_id") or raw.get("user_id")

        ct = raw.get("chat_type")
        uid = raw.get("user_id")

        if ct in ("private", "user"):
            ct = "private"
        elif uid is not None and str(chat_id) == str(uid):
            ct = "private"

        if not text:
            log_warning(
                f"⚠️ [ADMIN:reply] متن خالی | chat_id={chat_id}"
            )
            return

        chunks = self._chunk_text(text, _REPLY_CHUNK)

        for i, chunk in enumerate(chunks):
            try:
                t_c = time.monotonic()
                await sender(chat_id, ct, chunk)
                c_ms = (time.monotonic() - t_c) * 1000

                if c_ms > _SLOW_REPLY_MS:
                    log_warning(
                        f"🐌 [ADMIN:reply] chunk {i+1}/{len(chunks)} کند | "
                        f"{c_ms:.0f}ms | chat_id={chat_id}"
                    )
            except Exception as e:
                elapsed_ms = (time.monotonic() - t0) * 1000
                log_error(
                    f"❌ [ADMIN:reply] chunk {i+1}/{len(chunks)} | "
                    f"{type(e).__name__}: {e} | "
                    f"{elapsed_ms:.0f}ms | chat_id={chat_id}"
                )
                continue

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_info(
            f"✅ [ADMIN:reply] ارسال شد | {elapsed_ms:.0f}ms | "
            f"chat_id={chat_id} ({ct}) | "
            f"chunks={len(chunks)} | len={len(text)}"
        )

    @staticmethod
    def _chunk_text(text: str, size: int) -> list[str]:
        """متن بلند را به chunkهای ≤size می‌شکند (روی newline)."""
        if not text:
            return [""]
        if len(text) <= size:
            return [text]

        chunks: list[str] = []
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
    # Backward-compat wrappers
    # ═══════════════════════════════════════
    def _rule_parse(self, text):
        return parse_command(text)

    def _extract_value(self, text):
        return extract_value(text)

    def _platform_in(self, low):
        return platform_in(low)

    def _add_keyword(self, value):
        return self._keyword_admin.add(
            value, str(data_paths.KEYWORDS_CUSTOM)
        )

    def _remove_keyword(self, value):
        return self._keyword_admin.remove(
            value, str(data_paths.KEYWORDS_CUSTOM)
        )

    def _remember(self, q, a):
        self._memory.remember(q, a)

    def _recent_memory(self, n=6):
        return self._memory.recent(n)

    async def _execute(self, action, value, raw_text):
        return await self._actions.execute(action, value, raw_text)

    def _status(self):
        return self._actions.status()

    def _live_context(self):
        return self._actions.live_context()

    async def _health(self):
        return await self._actions.health()

    async def _lead_details(self, which):
        return await self._actions.lead_details(which)

    async def _platform_status(self, platform):
        return await self._actions.platform_status(platform)

    def _logs(self, n=12):
        return self._actions.logs(n)

    async def _explain_logs(self):
        return await self._actions.explain_logs()

    def _help(self):
        return self._actions.help_text()

    def _get_param(self, name):
        return self._actions.get_param(name)

    def _set_param(self, raw_text):
        return self._actions.set_param(raw_text)

    async def _crm_action(self, raw_text):
        return await self._actions.crm_action(raw_text)

    async def _report_action(self, raw_text):
        return await self._actions.report_action(raw_text)

    def _log_request(self, text):
        return self._actions.log_request(text)

    def _update_env(self, key, value):
        return self._actions._update_env(key, value)

    @staticmethod
    def _read_custom_kw_dict():
        path = str(data_paths.KEYWORDS_CUSTOM)
        return KeywordAdmin(path).read(path)

    @staticmethod
    def _write_custom_kw_dict(kw_tier):
        path = str(data_paths.KEYWORDS_CUSTOM)
        return KeywordAdmin(path).write(kw_tier, path)