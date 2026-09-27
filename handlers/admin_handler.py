"""
ChatOps admin handler — نسخهٔ ۸ (ماژولار + async-correct).

این فایل فقط coordinator است:
  - گیت‌های امنیتی (خصوصی + ADMIN_IDS)
  - مسیرهای handle / process_text
  - ارسال پاسخ

منطق به این ماژول‌ها واگذار شده:
  - core/command_parser.py      → parse دستور
  - handlers/keyword_admin.py   → CRUD کلیدواژه
  - handlers/admin_memory.py    → حافظهٔ گفتگو
  - handlers/admin_actions.py   → اجرای دستورها

نسخهٔ ۸ (این نسخه):
  - 🐛 FIX: پیام پیش‌فرض برای reply خالی (نه ارسال None)
  - 🐛 FIX: _MAX_REPLY_LEN حذف شد (استفاده نمی‌شد)
  - 🐛 FIX: _handle_command حالا "" را به پیام راهنما تبدیل می‌کند
  - 🐛 FIX: _handle_free_text حالا fallback نهایی دارد
  - 🎯 لاگ واضح‌تر برای fallback نهایی
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۷.
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


# ─── مسیرها ───
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PID_FILE = os.path.join(ROOT, "storage", "bot.pid")
REQUESTS = os.path.join(ROOT, "storage", "requests.jsonl")
MEMORY = os.path.join(ROOT, "storage", "ai_memory.jsonl")

# ⭐ مسیرها از data/paths.py (منبع یکتا)
from data import paths as data_paths

CUSTOM_KW = str(data_paths.KEYWORDS_CUSTOM)
KNOWLEDGE = str(data_paths.BOT_KNOWLEDGE)
MEMORY_MAX = 200

# ─── ثابت‌ها ───
_LOG_PREVIEW_LEN = 100
_SLOW_HANDLE_MS = 5000
_SLOW_LAYER_MS = 3000
_SLOW_REPLY_MS = 3000

# سقف متن برای ارسال به بله (بله حدود ۴۰۰۰ کاراکتر در هر پیام)
_REPLY_CHUNK = 3900

# prefixهای SafeBrain — برای تشخیص «واگذاری به لایهٔ بعد»
_SAFEBRAIN_PREFIXES = ("🤖 SafeBrain", "🤖 WinDoctor")

# تعداد کاراکتر برای اینکه SafeBrain real باشد (نه fallback)
_SAFEBRAIN_MIN_REAL_LEN = 250

# 🐛 FIX: پیام پیش‌فرض وقتی هیچ لایه‌ای پاسخ نداد
_EMPTY_REPLY_FALLBACK = (
    "🤔 متوجه نشدم یا پاسخی پیدا نشد.\n"
    "می‌توانی:\n"
    "• «راهنما» بزنی\n"
    "• «وضعیت» یا «گزارش» بپرسی\n"
    "• واضح‌تر بپرسی"
)


class AdminHandler(BaseHandler):
    """ChatOps: تنها مسیر دستور دادن. سختگیر: فقط خصوصی، فقط ADMIN_IDS."""

    def __init__(self, db, scorer=None, **kwargs):
        super().__init__()
        self.db = db
        self.kw = get_keyword_finder()
        self.ai = get_ai_assistant()

        self.db_path = getattr(
            db, "db_path",
            os.path.join(ROOT, "storage", "database", "messages.db"),
        )

        # ─── SafeBrain (singleton) ───
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        self.safe_brain: SafeBrain = get_safe_brain(api_key=api_key)

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

        # ─── config ───
        from config import PLUGINS_CONFIG
        cfg = PLUGINS_CONFIG.get("adminhandler", {})
        # ⚠️ هاردکد غیرفعال (پایشگر فقط‌خواندنی)
        self.enabled = False
        self.admin_ids = set()   # هیچ مدیری نیست
        self.platform = cfg.get("platform", "bale")

        # چک: آیا پلتفرم مدیر فعال است؟ (برای لاگ)
        self._platform_enabled = PLUGINS_CONFIG.get(
            f"{self.platform}collector", {}
        ).get("enabled", False)
        if not self._platform_enabled and self.enabled:
            log_warning(
                f"⚠️ [ADMIN:init] پلتفرم مدیر «{self.platform}» غیرفعال است "
                f"ولی ADMIN_ENABLED=true → پاسخ‌ها ارسال نمی‌شوند. "
                f"BALE_ENABLED=true کن یا ADMIN_PLATFORM را عوض کن."
            )

        # ─── دانش ───
        self._knowledge = self._load_knowledge()

        # ─── زیرماژول‌ها ───
        self._keyword_admin = KeywordAdmin(CUSTOM_KW)
        self._memory = AdminMemory(MEMORY, MEMORY_MAX)
        self._actions = AdminActions(
            db=self.db,
            db_path=self.db_path,
            kw=self.kw,
            ai=self.ai,
            knowledge=self._knowledge,
            keyword_admin=self._keyword_admin,
            memory=self._memory,
            root=ROOT,
            platform=self.platform,
            pid_file=PID_FILE,
            get_requests_path=lambda: REQUESTS,
            get_custom_kw_path=lambda: CUSTOM_KW,
        )

        log_info(
            f"⏭️ [ADMIN:init] غیرفعال — پایشگر فقط‌خواندنی | "
            f"platform={self.platform}"
        )
    # ═══════════════════════════════════════
    # knowledge
    # ═══════════════════════════════════════
    def _load_knowledge(self) -> str:
        try:
            with open(KNOWLEDGE, encoding="utf-8") as f:
                text = f.read()
            log_info(
                f"📚 [ADMIN:init] knowledge بارگذاری شد | {len(text)}ch"
            )
            return text
        except FileNotFoundError:
            log_warning(f"⚠️ [ADMIN:init] knowledge نبود | {KNOWLEDGE}")
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
        """آیا این پاسخ، پیامِ «هوش در دسترس نیست» SafeBrain است؟

        تشخیص:
          - خالی → fallback
          - با prefix شروع شود + متن کوتاه → fallback
          - با prefix شروع شود + متن بلند → پاسخ واقعی (rare)
        """
        if not reply:
            return True
        s = reply.strip()
        if not s.startswith(_SAFEBRAIN_PREFIXES):
            return False
        # اگر با prefix شروع شده ولی متن کوتاه است → fallback
        return len(s) < _SAFEBRAIN_MIN_REAL_LEN

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
        # پیام‌های خطای شناخته‌شده AI
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

        # 🐛 FIX: پیام پیش‌فرض اگه reply خالی بود
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

        # ─── لایهٔ SafeBrain ───
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

        # ─── اگه SafeBrain مسئول نبود → AdminActions ───
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

        # 🐛 FIX: تبدیل None/"" به پیام پیش‌فرض
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

        # 🐛 FIX: fallback نهایی
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
            # 🐛 FIX: اگه reply خالی بود
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
        if uid is not None and str(chat_id) == str(uid):
            ct = "private"

        if not text:
            log_warning(
                f"⚠️ [ADMIN:reply] متن خالی | chat_id={chat_id}"
            )
            return

        # ─── شکستن متن بلند ───
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
                # ادامه بده — شاید chunk بعدی موفق بشه
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
            # سعی کن روی newline بشکن
            cut = remaining.rfind("\n", 0, size)
            if cut < size // 2:
                # newline نزدیک نبود → روی فاصله
                cut = remaining.rfind(" ", 0, size)
            if cut < size // 2:
                # هیچ‌کدام → cut هارد
                cut = size
            chunks.append(remaining[:cut])
            remaining = remaining[cut:].lstrip("\n")

        return chunks

    # ═══════════════════════════════════════
    # Backward-compat wrappers (تست‌ها + کد قدیمی)
    # ═══════════════════════════════════════
    def _rule_parse(self, text):
        return parse_command(text)

    def _extract_value(self, text):
        return extract_value(text)

    def _platform_in(self, low):
        return platform_in(low)

    def _add_keyword(self, value):
        return self._keyword_admin.add(value, CUSTOM_KW)

    def _remove_keyword(self, value):
        return self._keyword_admin.remove(value, CUSTOM_KW)

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

    # ⚠️ توجه: crm_action/report_action در admin_actions الان async شدند
    # → این wrapperها هم async شدند
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
        return KeywordAdmin(CUSTOM_KW).read(CUSTOM_KW)

    @staticmethod
    def _write_custom_kw_dict(kw_tier):
        return KeywordAdmin(CUSTOM_KW).write(kw_tier, CUSTOM_KW)
