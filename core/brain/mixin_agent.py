"""Agent loop — init_client, agent, tool execution, admin commands.

نسخهٔ ۸:
  - 🐛 FIX: set_database تکراری حذف شد.
  - 🐛 FIX: _DOMAIN_GUARD به بالای prompt منتقل شد (قبل از lessons).
  - 🆕 add_lesson — ذخیرهٔ درس (خطا → راه‌حل).
  - 🆕 _DOMAIN_GUARD — نگهبان دامنه (WinDoctor).
  - 🆕 _inject_knowledge: ۳ بلوک دستور (تست، audit، آپدیت).
  - 🆕 agent: self-healing loop (۳ تلاش).
  - 🆕 _inject_knowledge: تجربه‌های قبلی.
"""

from __future__ import annotations
from functools import lru_cache
import asyncio
import contextlib
import json
import os
import re
import time
from pathlib import Path

from config import AI, AI_ENABLED, AI_PRIORITY, OFFLINE_MODE_ENABLED
from core.brain.constants import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    LOG_PREVIEW_LEN,
    MAX_AGENT_STEPS,
    MAX_CONTENT_LENGTH,
    SLOW_ACTION_MS,
    SLOW_STEP_MS,
    SLOW_THINK_MS,
    SLOW_TOOL_MS,
)
from core.brain.tools_meta import (
    ALLOWED_DIRECT_COMMANDS,
    ALLOWED_TOOLS,
    build_tool_definitions,
)
from core.brain.utils import PersianBrainHandler
from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)
from data.loader import load_json as _load_json
from data.loader import load_text as _load_text


def _env_int(name, default, min_val=None, max_val=None):
    try:
        v = int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


def _env_float(name, default, min_val=None, max_val=None):
    try:
        v = float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


def _env_str(name, default=""):
    return (os.getenv(name) or default).strip()


_WRITE_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "delete_file",
        "move_file",
    }
)

_DEEPSEEK_URL = _env_str("DEEPSEEK_API_URL", "https://api.deepseek.com/v1")
_OPENAI_URL = _env_str("OPENAI_API_URL", "https://api.openai.com/v1")
_LOCAL_CLAUDE_URL = _env_str("LOCAL_CLAUDE_URL", "http://localhost:4000")

_AGENT_MAX_TOKENS = _env_int("BRAIN_AGENT_MAX_TOKENS", 4096, 256, 32000)
_AGENT_TEMPERATURE = _env_float("BRAIN_AGENT_TEMPERATURE", 0.1, 0.0, 2.0)
_THINK_MAX_TOKENS = _env_int("BRAIN_THINK_MAX_TOKENS", 4096, 256, 16000)
_THINK_TEMPERATURE = _env_float("BRAIN_THINK_TEMPERATURE", 0.1, 0.0, 2.0)
_API_TIMEOUT_S = _env_float("BRAIN_AGENT_API_TIMEOUT_S", 120.0, 5.0, 600.0)
_THINK_TIMEOUT_S = _env_float("BRAIN_THINK_TIMEOUT_S", 60.0, 5.0, 300.0)
_MAX_FALLBACK = _env_int("BRAIN_MAX_FALLBACK", 1, 0, 10)
_LONG_TASK_LEN = _env_int("BRAIN_LONG_TASK_LEN", 200, 50, 10000)


# 🆕 _QUICK_COMMANDS از data/configs/quick_commands.json

_quick_data = _load_json("configs/quick_commands.json")
_QUICK_COMMANDS = _quick_data.get("commands", {})

# # ═══════════════════════════════════════════════════════════
# 🆕 Domain Guard — نگهبان دامنه (از data/prompts/domain_guard.md)
# ═══════════════════════════════════════════════════════════
_DOMAIN_GUARD = _load_text("prompts/domain_guard.md")

@lru_cache(maxsize=16)
def _cached_base_prompt(mode: str) -> str:
    """بخش ثابت system prompt — cache شده (mode ثابت)."""
    cot_prefix = """
# روش کار:

**قانون طلایی:** اگر سؤال ساده است (سلام، احوالپرسی، سؤال عمومی)، مستقیم جواب بده - ابزار صدا نزن.

1. فهم دقیق سؤال
2. اگر سؤال ساده است → مستقیم جواب بده (بدون ابزار)
3. اگر سؤال فنی/کد است → از ابزارها استفاده کن
4. تحلیل و تصمیم
5. بازبینی نهایی

قوانین:
- حدس نزن
- برای سلام و احوالپرسی، ابزار صدا نزن
- اگر مطمئن نیستی بگو «نمی‌دونم»
"""
    base = ""
    try:
        from config import get_ai_system_prompt
        prompt = get_ai_system_prompt(mode)
        if prompt:
            base = prompt
    except Exception:
        pass
    if not base:
        base = (
            "You are a world-class software engineer. "
            "Always respond in Persian. Be concise, "
            "precise, and helpful."
        )
    return cot_prefix + base + _DOMAIN_GUARD

class AgentMixin:
    ALLOWED_TOOLS = ALLOWED_TOOLS
    ALLOWED_DIRECT_COMMANDS = ALLOWED_DIRECT_COMMANDS

    def set_event_bus(self, event_bus):
        self.event_bus = event_bus

    def set_database(self, db):
        self.db = db

    # ═══════════════════════════════════════════════════════
    # 🆕 add_lesson — ذخیرهٔ درس (خطا → راه‌حل)
    # ═══════════════════════════════════════════════════════
    def add_lesson(self, wrong: str, correct: str) -> None:
        """افزودن درس (خطا → راه‌حل) به حافظه."""
        try:
            if hasattr(self, "memory") and self.memory:
                self.memory.add_lesson(wrong=wrong, correct=correct)
                log_info(f"📚 [BRAIN:lesson] +1 | {wrong[:60]!r}")
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:add_lesson] {type(e).__name__}: {e}")

    async def _emit(self, event_name, data):
        if self.event_bus:
            try:
                await self.event_bus.emit(f"brain.{event_name}", data, source="brain")
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:emit] {event_name} | {
                        type(e).__name__}: {e}")

    def _init_client(self):
        """آماده‌سازی client — lazy (ساخت واقعی در _lazy_init_client)."""
        priority = (AI_PRIORITY or "auto").strip().lower()
        try:
            _cfg_api_key = ((AI or {}).get("deepseek_api_key") or "").strip()
        except Exception:
            _cfg_api_key = ""
        _env_api_key = (os.environ.get("DEEPSEEK_API_KEY", "") or "").strip()

        if self.api_key:
            effective_api_key = self.api_key
            key_source = "brain"
        elif _cfg_api_key:
            effective_api_key = _cfg_api_key
            key_source = "config"
            self.api_key = _cfg_api_key
        elif _env_api_key:
            effective_api_key = _env_api_key
            key_source = "env"
            self.api_key = _env_api_key
        else:
            effective_api_key = ""
            key_source = "none"

        has_key = bool(effective_api_key)
        model_str = str(self.model) if self.model is not None else ""
        if model_str.startswith("ollama"):
            priority = "ollama"

        # ─── انتخاب مسیر: ollama یا deepseek/openai/claude ───
        if priority == "deepseek":
            if not has_key:
                self.client = None
                self._pending_client_config = None
                log_warning("⚠️ [BRAIN:client] deepseek بدون api_key")
                return
            use_ollama = False
        elif priority == "ollama":
            use_ollama = True
        else:
            use_ollama = not has_key

        # ─── ساخت config برای lazy init ───
        try:
            if use_ollama:
                base_url = (
                    AI.get("url") or DEFAULT_BASE_URL).rstrip("/") + "/v1"
                self.model = AI.get(
                    "model") or DEFAULT_MODEL or "deepseek-flash"
                timeout = int(AI.get("timeout") or _API_TIMEOUT_S)
                api_key_for_client = "ollama"
                config = {
                    "api_key": api_key_for_client,
                    "base_url": base_url,
                    "timeout": timeout,
                    "model": self.model,
                    "kind": "ollama",
                }
                log_info(
                    f"⏳ [BRAIN:client] Ollama پیکربندی شد (lazy) | "
                    f"model={self.model} | url={base_url}"
                )
            else:
                base_url = _DEEPSEEK_URL
                if model_str.startswith("claude"):
                    base_url = _LOCAL_CLAUDE_URL
                elif model_str.startswith("gpt"):
                    base_url = _OPENAI_URL
                elif not (model_str.startswith(("deepseek", "claude", "gpt"))):
                    old_model = self.model
                    self.model = _env_str("DEEPSEEK_MODEL", "deepseek-chat")
                    log_warning(
                        f"⚠️ [BRAIN:client] مدل «{old_model}» → «{self.model}»"
                    )
                timeout = int(AI.get("timeout") or _API_TIMEOUT_S)
                config = {
                    "api_key": effective_api_key,
                    "base_url": base_url,
                    "timeout": timeout,
                    "model": self.model,
                    "kind": "api",
                }
                log_info(
                    f"⏳ [BRAIN:client] {base_url} پیکربندی شد (lazy) | "
                    f"model={self.model} | key_source={key_source}"
                )

            # 🆕 ذخیره config و lazy flag
            self._pending_client_config = config
            self.client = None
        except Exception as e:
            log_error(f"❌ [BRAIN:client] {type(e).__name__}: {e}")
            self._pending_client_config = None
            self.client = None
            
    def _lazy_init_client(self) -> None:
        """🆕 ساخت client فقط وقتی اولین agent() صدا زده شد."""
        cfg = getattr(self, "_pending_client_config", None)
        if not cfg:
            return
        try:
            from openai import AsyncOpenAI
            import httpx
            t0 = time.monotonic()
            http_client = httpx.AsyncClient(
                timeout=cfg["timeout"],
                http2=False,
                trust_env=True,
            )
            self.client = AsyncOpenAI(
                api_key=cfg["api_key"],
                base_url=cfg["base_url"],
                timeout=cfg["timeout"],
                max_retries=3,
                http_client=http_client,
            )
            self._pending_client_config = None
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_success(
                f"✅ [BRAIN:client:lazy] client ساخته شد | {elapsed_ms:.0f}ms | "
                f"kind={cfg.get('kind', '?')}"
            )
        except Exception as e:
            log_error(f"❌ [BRAIN:client:lazy] {type(e).__name__}: {e}")
            self.client = None
            
    def _background_index(self):
        try:
            log_info("📚 [BRAIN:bg:index] شروع")
            self.semantic_search.index_files()
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:bg:index] {type(e).__name__}: {e}")
            self._index_started = False

    def _build_tools(self):
        return build_tool_definitions()

    async def _execute_tool(self, name, args):
        if not name:
            return "❌ نام ابزار خالی است."
        t0 = time.monotonic()
        # 🆕 FIX: args خالی (JSON نامعتبر) — پیام راهنما بده
        if not args and name in _WRITE_TOOLS:
            log_warning(f"⚠️ [BRAIN:tool] {name} با args خالی — JSON نامعتبر")
            return (
                "⚠️ پارامترها خالی‌اند (JSON نامعتبر بود). "
                "برای فایل بزرگ از edit_file استفاده کن، نه write_file. "
                "یا محتوا را به قطعات کوچک‌تر تقسیم کن."
            )
        tool_info = self.ALLOWED_TOOLS.get(name)
        if not tool_info:
            return f"⛔ ابزار '{name}' مجاز نیست."
        if self.read_only and tool_info.get("risk") != "low":
            return "⛔ حالت فقط‌خواندنی."
        if name in _WRITE_TOOLS:
            path = args.get("path") or args.get("source") or args.get("dest")
            if not path:
                return "❌ مسیر مشخص نشده."
            if not self.full_access and not self._is_safe_write_path(path):
                return f"⛔ دسترسی نوشتن در '{path}' مجاز نیست."
        tool_map = getattr(self, "TOOL_MAP", None)
        if not tool_map:
            return "❌ TOOL_MAP بارگذاری نشده."
        func = tool_map.get(name)
        if not func:
            return f"⛔ ابزار '{name}' پیاده‌نشده."
        try:
            result = await func(self, **args)
            elapsed_ms = (time.monotonic() - t0) * 1000
            result_str = str(result)
            if elapsed_ms > SLOW_TOOL_MS:
                log_warning(f"🐌 [BRAIN:tool] {name} کند | {elapsed_ms:.0f}ms")
            else:
                log_info(
                    f"🔧 [BRAIN:tool] {name} | "
                    f"{elapsed_ms:.0f}ms | "
                    f"out={len(result_str)}ch"
                )
            return result_str
        except TypeError as e:
            return f"❌ پارامتر اشتباه: {e}"
        except Exception as e:
            log_error(f"❌ [BRAIN:tool] {name} | {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    # 🆕 FIX-BRAIN #1 / #2 / #4 — تصمیم‌گیری تست‌پذیر
    _COMPLEX_KEYWORDS = (
        "چرا",
        "چطور",
        "چگونه",
        "مشکل",
        "خطا",
        "باگ",
        "کار نمی",
        "وصل نمی",
        "تحلیل",
        "برنامه",
        "چندمرحله",
        "علت",
        "analyze",
        "debug",
        "refactor",
        "plan",
        "multi-step",
        "why",
        "root cause",
    )

    @staticmethod
    def _is_complex_task(task) -> bool:
        """آیا تسک واقعاً به فکر/برنامه‌ریزی نیاز دارد؟"""
        t = str(task or "").strip()
        if not t:
            return False
        low = t.lower()
        if any(kw in low for kw in AgentMixin._COMPLEX_KEYWORDS):
            return True
        # طول تنها کافی نیست — باید هم بلند باشد هم چندجمله‌ای
        return len(t) > 50 and len(t.split()) >= 20

    @classmethod
    def _should_think(cls, task) -> bool:
        """_think فقط برای تسک‌های پیچیده که plan ندارند."""
        t = str(task or "").strip()
        if not t or len(t) >= 500:
            return False
        # اگر plan هم فعال است، think نکن
        if cls._should_plan(t):
            return False
        return cls._is_complex_task(t)

    @classmethod
    def _should_plan(cls, task) -> bool:
        """plan فقط برای تسک‌های پیچیده که think ندارند."""
        t = str(task or "").strip()
        if not t or len(t) < 30 or len(t) >= 500:
            return False
        return cls._is_complex_task(t)

    @staticmethod
    def _build_retry_prompt(task, answer) -> str:
        """FIX-BRAIN #4 — retry_prompt باید تسک اصلی را حفظ کند."""
        return (
            "پاسخ قبلی ناقص بود. لطفاً دوباره تلاش کن:\n"
            f"TASK اصلی: {str(task or '')[:500]}\n\n"
            f"پاسخ قبلی: {str(answer or '')[:500]}\n\n"
            "این بار کامل و دقیق پاسخ بده."
        )

    async def _think(self, task, context=""):
        # 🆕 FIX: اگر client lazy است، حالا بساز
        if not self.client and getattr(self, "_pending_client_config", None):
            self._lazy_init_client()
        if not self.client:
            return ""
        prompt = (
            "You are a world-class software engineer. "
            "Think about this:\n\n"
            f"CONTEXT: {context}\n\n"
            f"TASK: {task}\n\n"
            "Analyze: 1) REAL goal? 2) Edge cases? "
            "3) Dependencies? 4) Optimal solution? "
            "5) Testing strategy?\n"
            "Provide concise analysis then action plan."
        )
        try:
            t0 = time.monotonic()
            r = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=_THINK_TEMPERATURE,
                    max_tokens=_THINK_MAX_TOKENS,
                ),
                timeout=_THINK_TIMEOUT_S,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            content = ""
            try:
                content = (r.choices[0].message.content or "").strip()
            except Exception:
                content = ""
            if elapsed_ms > SLOW_THINK_MS:
                log_warning(f"🐌 [BRAIN:think] کند | {elapsed_ms:.0f}ms")
            return content
        except TimeoutError:
            log_warning(f"⏰ [BRAIN:think] timeout {_THINK_TIMEOUT_S}s")
            return ""
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:think] {type(e).__name__}: {e}")
            return ""

    def _select_model(self, task):
        """انتخاب مدل — همیشه self.model (از .env)."""
        log_info(f"🎯 [BRAIN:model] {self.model} انتخاب شد")
        return self.model

    def _build_system_prompt(self, mode):
        base = _cached_base_prompt(mode)

        lessons = getattr(self, "_lessons", [])
        if lessons:
            base += "\n\n# درس‌های قبلی:\n"
            for lesson in lessons[:3]:
                base += f"- {lesson[:200]}\n"

        last_task = getattr(self, "_last_task", "")
        if last_task:
            try:
                from core.brain.tool_router import ToolRouter
                hint = ToolRouter.build_hint(last_task)
                if hint:
                    base += hint
            except Exception as e:
                log_warning(
                    f"⚠️ [core.brain.mixin_agent] {type(e).__name__}: {e}")

        try:
            _plats = {
                "بله": "bale", "ایتا": "eitaa", "روبیکا": "rubika",
                "تلگرام": "telegram", "واتساپ": "whatsapp",
                "لینکدین": "linkedin", "یوتیوب": "youtube",
            }
            _found = [p for k, p in _plats.items() if k in (last_task or "")]
            if _found:
                base += f"\n\n# پلتفرم هدف: {', '.join(_found)}"
        except Exception as e:
            log_warning(f"⚠️ [core.brain.mixin_agent] {type(e).__name__}: {e}")

        return base
        
    async def agent(self, task, mode="precise", bypass_busy=False):
        # 🆕 FIX: مقدار اولیه — جلوگیری از UnboundLocalError در مسیرهای زودهنگام
        result_str = ""

        # ⚡ پاسخ سریع برای سوالات ساده (بدون AI)
        _simple = task.strip().lower()
        if _simple in (
            "سلام",
            "درود",
            "hi",
            "hello",
            "hey",
            "چطوری",
            "خوبی",
            "چه خبر",
            "خوبی؟",
            "ممنون",
            "مرسی",
            "thanks",
            "thank you",
        ):
            import random

            _greetings = [
                "سلام! 👋 چطور می‌تونم کمکت کنم؟",
                "درود! 😊 آماده‌ام.",
                "سلام! چه کاری برات انجام بدم؟",
            ]
            log_info(f"⚡ [BRAIN:agent] پاسخ سریع | task={task[:40]!r}")
            return random.choice(_greetings)
        # 🆕 FIX: busy رو غیرفعال کن — سؤال‌ها موازی اجرا بشن
        # (قبلاً busy باعث می‌شد ۱۶ سؤال تایم‌اوت بشن)
        # 🆕 busy هوشمند — فقط برای سؤال‌های طولانی یا کلیدواژه‌دار
        _need_busy = True  # 🆕 همه تسک‌ها سریالی — جلوگیری از پاسخ گم
        if not bypass_busy and _need_busy and self.busy:
            log_info(f"⏳ [BRAIN:agent] صف انتظار | task={task[:60]!r}")
            for _ in range(10):
                await asyncio.sleep(1)
                if not self.busy:
                    break
            else:
                log_warning(
                    f"⚠️ [BRAIN:agent] busy — bypass | task={task[:60]!r}")
                bypass_busy = True
        if not bypass_busy and _need_busy:
            self.busy = True
            self._busy_started_at = time.monotonic()
        else:
            log_info(f"🔓 [BRAIN:agent] bypass_busy | task={task[:60]!r}")

        self.stats["agents_run"] += 1
        t_start = time.monotonic()
        log_info(
            f"🚀 [BRAIN:agent] شروع | mode={mode} | task={task[:LOG_PREVIEW_LEN]!r}"
        )
        self._last_task = str(task) if task is not None else ""

        # 🆕 FIX PERF: client را lazy بساز — اولین agent() call
        if not self.client and getattr(self, "_pending_client_config", None):
            self._lazy_init_client()

        # 🆕 قفل agent — جلوگیری از موازی
        if not hasattr(self, "_agent_lock") or self._agent_lock is None:
            self._agent_lock = asyncio.Lock()
            
        step = 0
        try:
            try:
                cached = self.agent_cache.get(task, self.model)
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:agent] cache.get | {
                        type(e).__name__}: {e}")
                cached = None
            if cached:
                return cached
            try:
                self.memory.add("user", task)
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:agent] memory.add | {
                        type(e).__name__}: {e}")

            # 🆕 کشف محصولات جدید از پیام کاربر
            try:
                if hasattr(self, "product_learner") and self.product_learner:
                    self.product_learner.track_mention(task)
            except Exception as e:
                log_warning(
                    f"⚠️ [core.brain.mixin_agent] {
                        type(e).__name__}: {e}")

            await self._emit("command", {"task": task, "mode": mode})
            # 🆕 FIX-BRAIN #1: فقط تسک پیچیده (نه صرفاً len > 50)
            # 🆕 FIX: تسک‌های بزرگ (> ۵۰۰ کاراکتر) خودشان plan دارند — think اضافی است
            _need_think = self._should_think(task) and len(str(task)) < 500
            thinking = await self._think(task, context="") if _need_think else ""
            selected_model = self._select_model(task)
            system_prompt = self._build_system_prompt(mode)
            knowledge = self._inject_knowledge(task)
            if knowledge:
                system_prompt += f"\n\n{knowledge}"
            # 🆕 FIX-BRAIN #2: plan فقط برای تسک پیچیده
            plan_steps = await self._plan_steps(task) if self._should_plan(task) else []
            if plan_steps:
                system_prompt += "\n\n# برنامه پیشنهادی:\n" + "\n".join(
                    f"  {i + 1}. {s}" for i, s in enumerate(plan_steps)
                )
            try:
                context_history = await self.memory.get_context(task)
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:agent] memory.get_context | {
                        type(e).__name__}: {e}")
                context_history = []
            messages = (
                [{"role": "system", "content": system_prompt}]
                + context_history
                + [{"role": "user", "content": task}]
            )
            result_str = ""
            if thinking:
                messages.insert(
                    1,
                    {
                        "role": "system",
                        "content": f"تحلیل اولیه:\n{thinking}",
                    },
                )
            final_answer = ""
            tools_used = []
            fallback_count = 0
            while step < MAX_AGENT_STEPS:
                step += 1
                step_t0 = time.monotonic()
                if not self.client:
                    final_answer = (
                        self._offline_agent(task)
                        if OFFLINE_MODE_ENABLED
                        else "⚠️ مغز در دسترس نیست."
                    )
                    break
                try:
                    response = await asyncio.wait_for(
                        self.client.chat.completions.create(
                            model=selected_model,
                            messages=messages,
                            tools=self.tools_definition,
                            temperature=_AGENT_TEMPERATURE,
                            max_tokens=_AGENT_MAX_TOKENS,
                        ),
                        timeout=_API_TIMEOUT_S,
                    )
                except TimeoutError:
                    final_answer = f"⏰ API timeout ({_API_TIMEOUT_S}s) — گام {step}"
                    break
                except Exception as e:
                    log_error(
                        f"❌ [BRAIN:agent] API گام {step} | {
                            type(e).__name__}: {e}")
                    if (
                        (AI_PRIORITY or "").lower() != "ollama"
                        and selected_model != DEFAULT_MODEL
                        and fallback_count < _MAX_FALLBACK
                    ):
                        selected_model = DEFAULT_MODEL
                        fallback_count += 1
                        step -= 1
                        continue
                    final_answer = f"❌ خطای API: {e}"
                    break
                msg = response.choices[0].message
                if not msg.tool_calls:
                    final_answer = msg.content or "✅ عملیات تکمیل شد."
                    messages.append(
                        {
                            "role": "assistant",
                            "content": final_answer,
                        }
                    )
                    break
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": (tc.function.arguments),
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    }
                )
                for tc in msg.tool_calls:
                    name = tc.function.name
                    tools_used.append(name)
                    try:
                        args = (
                            json.loads(tc.function.arguments)
                            if tc.function.arguments
                            else {}
                        )
                    except json.JSONDecodeError as e:
                        log_warning(
                            f"⚠️ [BRAIN:agent] args JSON نامعتبر | {name} | {e}")
                        args = {}
                    log_info(
                        f"     🔧 [BRAIN:agent] {name} | "
                        f"args="
                        f"{str(args)[:LOG_PREVIEW_LEN]}"
                    )
                    test_res = ""
                    try:
                        result = await self._execute_tool(name, args)
                        result_str = str(result)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_str[:MAX_CONTENT_LENGTH],
                        })
                        if (
                            name in ("write_file", "edit_file")
                            and "✅" in result_str
                            and "خطا" not in result_str
                        ):
                            test_res = await self._auto_run_tests(args.get("path", ""))
                            if test_res:
                                messages.append({
                                    "role": "system",
                                    "content": f"🧪 تست خودکار:\n{test_res}",
                                })
                    except Exception as e:
                        result_str = f"❌ [tool-crash] {type(e).__name__}: {e}"
                        log_error(
                            f"❌ [BRAIN:tool-crash] {name} | "
                            f"{type(e).__name__}: {e}"
                        )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_str[:MAX_CONTENT_LENGTH],
                        })
                step_elapsed_ms = (time.monotonic() - step_t0) * 1000
                if step_elapsed_ms > SLOW_STEP_MS:
                    log_warning(
                        f"🐌 [BRAIN:agent] گام {step} کند | {
                            step_elapsed_ms:.0f}ms")
            final_answer = final_answer or f"⚠️ سقف گام ({MAX_AGENT_STEPS})."

            # 🆕 Self-Healing Loop (اگه task تست/audit/fix بود)
            # ولی اگه retry_prompt هست، self_heal نکن (جلوگیری از حلقه)
            _is_retry = "پاسخ قبلی ناقص بود" in task
            max_retry = 3
            # FIX: بدون تسک معتبر (خالی یا < ۱۰ کاراکتر) self_heal نکن
            _task_ok = bool(task) and len(str(task).strip()) >= 10
            if not _is_retry and _task_ok:
                task_lower = task.lower()
                if any(
                    kw in task_lower
                    for kw in (
                        "تست",
                        "test",
                        "audit",
                        "fix",
                        "بررسی",
                        "باگ",
                        "خطا",
                    )
                ):
                    iteration = 0
                    last_error = ""
                    while iteration < max_retry:
                        iteration += 1
                        # 🆕 FIX: مقایسهٔ هم‌اندازه (۲۰۰ کاراکتر)
                        current_key = final_answer[:200]
                        if iteration > 1 and current_key == last_error:
                            log_warning("⚠️ [BRAIN:self_heal] خطا تکرار شد — توقف")
                            break
                        last_error = current_key
                        if not task or len(str(task).strip()) < 10:
                            break
                        has_error = (
                            "❌" in final_answer[:200]
                            or "خطا" in final_answer[:200]
                            or "Error" in final_answer[:200]
                            or "Traceback" in final_answer[:200]
                        )
                        if not has_error:
                            break
                        log_info(
                            f"🔄 [BRAIN:self_heal] "
                            f"iteration {iteration}/{max_retry}"
                        )
                        # FIX-BRAIN #4: تسک اصلی باید در retry_prompt باشد
                        retry_prompt = self._build_retry_prompt(
                            task, final_answer)
                        try:
                            final_answer = await self.agent(
                                retry_prompt,
                                mode="code",
                                bypass_busy=True,
                            )
                        except Exception as e:
                            log_warning(f"⚠️ [BRAIN:self_heal] {e}")
                            break
            # _reflect غیرفعال شد — پاسخ مستقیم حفظ می‌شه
            # if (
            #     final_answer
            #     and len(final_answer) > 50
            #     and "❌" not in final_answer[:50]
            #     and "⏰" not in final_answer[:50]
            # ):
            #     final_answer = await self._reflect(task, final_answer)
            try:
                self.agent_cache.set(task, final_answer, self.model)
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:agent] cache.set | {
                        type(e).__name__}: {e}")
            try:
                self.memory.add("assistant", final_answer)
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:agent] memory.add | {
                        type(e).__name__}: {e}")

            # 🆕 کشف محصولات جدید از پاسخ
            try:
                if hasattr(self, "product_learner") and self.product_learner:
                    self.product_learner.track_mention(final_answer)
            except Exception as e:
                log_warning(
                    f"⚠️ [core.brain.mixin_agent] {
                        type(e).__name__}: {e}")

            try:
                _head = final_answer[:150]
                success = "❌" not in _head and "⏰" not in _head
                self.memory.remember(
                    question=task,
                    answer=final_answer[:500],
                    importance=0.8 if success else 0.4,
                )
                if not success:
                    self.memory.add_lesson(
                        wrong=task[:200],
                        correct=(f"پاسخ: {final_answer[:200]}"),
                    )
                    log_info("📚 [BRAIN:feedback] درس جدید ذخیره شد")
            except Exception as e:
                log_warning(f"⚠️ [BRAIN:feedback] {type(e).__name__}: {e}")
            _head = final_answer[:200]
            success = "❌" not in _head and not _head.startswith("خطا")
            try:
                self.learning_engine.record_interaction(
                    user_input=task,
                    response=final_answer,
                    success=success,
                    details={
                        "tools_used": tools_used,
                        "steps": step,
                    },
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:agent] learning | {
                        type(e).__name__}: {e}")
            total_s = time.monotonic() - t_start
            log_success(
                f"✅ [BRAIN:agent] کامل | "
                f"steps={step} | "
                f"tools={len(tools_used)} | "
                f"{total_s:.1f}s | success={success}"
            )
            return final_answer
        except Exception as e:
            self.stats["errors"] += 1
            log_error(f"❌ [BRAIN:agent] {type(e).__name__}: {e} | step={step}")
            try:
                from core.adapters.memory_store import (
                    MemoryStore,
                )

                store = MemoryStore()
                store.add(
                    category="reflexion",
                    key=f"error:{type(e).__name__}",
                    value=(f"تسک: {task[:200]} | خطا: {e}"),
                    importance=0.8,
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [core.brain.mixin_agent] {
                        type(e).__name__}: {e}")
            return f"❌ خطای غیرمنتظره: {e}"
        finally:
            if not bypass_busy:
                self.busy = False

    def _offline_agent(self, task):
        if not task:
            return "❓ چطور می‌تونم کمکت کنم؟"
        t = task.strip().lower()
        for cmd, action in self.ALLOWED_DIRECT_COMMANDS.items():
            cmd_low = cmd.lower()
            if len(cmd_low) <= 3:
                if not (t == cmd_low or t.startswith(cmd_low + " ")):
                    continue
            else:
                if cmd_low not in t:
                    continue
            if action == "greeting":
                return "سلام! مغز آماده است."
            elif action == "status":
                try:
                    return self.get_status()
                except Exception:
                    return "📊 وضعیت در دسترس نیست."
            elif action == "help":
                try:
                    return self.get_help()
                except Exception:
                    return "📚 راهنما در دسترس نیست."
        return "🔍 حالت آفلاین.\n• /status — وضعیت\n• /help — راهنما"

    async def _auto_run_tests(self, file_path):
        if not file_path:
            return ""
        try:
            p = Path(file_path)
        except Exception:
            return ""
        candidates = [
            p.parent / f"test_{p.name}",
            Path("tests") / f"test_{p.name}",
            Path("test") / f"test_{p.name}",
        ]
        for tf in candidates:
            try:
                if tf.exists():
                    log_info(f"🧪 [BRAIN:auto-test] {tf}")
                    run_tests = getattr(self, "_run_tests", None)
                    if run_tests:
                        return await run_tests(str(tf))
                    return ""
            except Exception:
                continue
        return ""

    async def process_admin_command(self, text):
        if not text:
            return ""
        self.stats["commands_processed"] += 1
        log_info(
            f"👤 [BRAIN:cmd] "
            f"#{self.stats['commands_processed']} | "
            f"{text[:LOG_PREVIEW_LEN]!r}"
        )
        with contextlib.suppress(Exception):
            text = PersianBrainHandler.fix(text)
        t = text.strip().lower()
        quick = _QUICK_COMMANDS.get(t)
        if quick == "status":
            return self.get_status()
        if quick == "help":
            return self.get_help()
        if quick == "clear":
            self.agent_cache.clear()
            self.memory.clear()
            return "🗑️ پاک شد."
        if quick == "stats":
            return self.get_stats()
        if quick == "restart":
            return "🔄 برای ری‌استارت، از ChatOps استفاده کن."
        if quick == "logs":
            try:
                return self._get_recent_logs()
            except Exception as e:
                return f"❌ خطا: {e}"
        if quick == "health":
            try:
                return await self._get_health_report()
            except Exception as e:
                return f"❌ خطا: {e}"
        if t.startswith(("/mode ", "حالت ")):
            try:
                mode = t.split(maxsplit=1)[1]
                self.set_mode(mode)
                return f"✅ حالت '{mode}' فعال شد."
            except Exception as e:
                return f"❌ خطا: {e}"
        if AI_ENABLED:
            try:
                from core.services.ai_assistant import (
                    get_ai_assistant,
                )

                ai = get_ai_assistant()
                if await ai.available():
                    intent = await ai.parse_intent(text)
                    action = intent.get("action", "unknown")
                    args = intent.get("args", {})
                    result = await self._handle_action(action, args, text)
                    if result:
                        return result
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:cmd] AI assistant | {
                        type(e).__name__}: {e}")
        # 🆕 FIX PERF: اگر client lazy است، حالا بساز
        if not self.client and getattr(self, "_pending_client_config", None):
            self._lazy_init_client()

        if self.client:
            return await self.agent(text)
        if not OFFLINE_MODE_ENABLED:
            return "⚠️ مغز در دسترس نیست."
        return self._offline_agent(text)

    async def _handle_action(self, action, args, original_text):
        log_info(f"🎬 [BRAIN:action] {action} | args={list(args.keys())}")
        t0 = time.monotonic()
        try:
            if action == "status":
                result = self.get_status()
            elif action == "health":
                result = await self._get_health_report()
            elif action == "help":
                result = self.get_help()
            elif action == "keywords":
                result = await self._get_keywords_report()
            elif action == "platform_status":
                result = await self._get_platform_status(args.get("value", ""))
            elif action == "report":
                result = await self._get_report()
            elif action == "crm":
                result = "📊 خروجی CRM توسط admin_handler."
            elif action == "logs":
                result = self._get_recent_logs()
            elif action == "lead_details":
                result = await self._get_lead_details(args.get("value", "last"))
            elif action == "add_keyword":
                result = await self._delegate_keyword("add", args.get("value", ""))
            elif action == "remove_keyword":
                result = await self._delegate_keyword("remove", args.get("value", ""))
            elif action == "has_keyword":
                result = await self._check_keyword(args.get("value", ""))
            elif action in ("ask", "feature_request"):
                result = await self.agent(original_text)
            else:
                result = None
        except Exception as e:
            log_error(f"❌ [BRAIN:action] {action} | {type(e).__name__}: {e}")
            result = None
        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms > SLOW_ACTION_MS:
            log_warning(f"🐌 [BRAIN:action] {action} کند | {elapsed_ms:.0f}ms")
        return result

    async def _delegate_keyword(self, op, kw):
        if not kw:
            return "⚠️ کلیدواژه خالی است."
        try:
            from data import paths as data_paths
            from handlers.keyword_admin import KeywordAdmin

            custom_kw_path = str(data_paths.KEYWORDS_CUSTOM)
            ka = KeywordAdmin(custom_kw_path)

            if op == "add":
                result = ka.add_keyword(kw)
                if result:
                    return f"✅ «{kw}» اضافه شد."
                return f"⚠️ «{kw}» از قبل هست."
            elif op == "remove":
                result = ka.remove_keyword(kw)
                if result:
                    return f"🗑️ «{kw}» حذف شد."
                return f"⚠️ «{kw}» پیدا نشد."
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:delegate] {op} | {type(e).__name__}: {e}")
            return f"❌ خطا در {op} کلیدواژه «{kw}»: {e}"
        return f"⛔ op نامعتبر: {op}"

    async def build_from_map(self, task, max_retries=3):  # noqa: ARG002
        if not self.planner:
            return "❌ برنامه‌ریز نیست."
        if not self.client:
            return "❌ حالت آفلاین."
        try:
            result = await self.planner.execute_plan(task)
            return f"🏗️ ساخت از نقشه:\n{result}"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def self_upgrade(self, upgrade_request="بهبود کد"):
        if not self.client:
            return "❌ حالت آفلاین."
        try:
            prompt = f"خودت را بازبینی کن: {upgrade_request}"
            result = await self.agent(prompt, mode="analyze")
            return f"🔄 خودارتقا:\n{result}"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _plan_steps(self, task):
        # 🆕 FIX: اگر client lazy است، حالا بساز
        if not self.client and getattr(self, "_pending_client_config", None):
            self._lazy_init_client()
        if not self.client or not task:
            return []

        # 🆕 context از recall
        context = ""
        try:
            if self.memory:
                recall = self.memory.recall(task)
                if recall:
                    context = f"\n**تاریخچه مشابه:**\n{recall[:500]}\n"
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:plan:recall] {type(e).__name__}: {e}")

        prompt = (
            "برای این سؤال، یه برنامه ۱-۵ مرحله‌ای "
            "بنویس:\n\n"
            f"**سؤال:** {task}\n\n"
            f"{context}"
            "**فرمت خروجی:**\n"
            "1. گام اول\n"
            "2. گام دوم\n"
            "...\n\n"
            "اگه سؤال ساده‌ست، فقط بنویس: **ساده**\n"
        )
        try:
            r = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "user",
                            "content": prompt,
                        }
                    ],
                    temperature=0.1,
                    max_tokens=500,
                ),
                timeout=20.0,
            )
            result = (r.choices[0].message.content or "").strip()
            if "ساده" in result[:20]:
                return []
            steps = []
            for line in result.split("\n"):
                line = line.strip()
                if not line:
                    continue
                m = re.match(r"^[\d۰-۹]+[\.\-]\s*(.+)", line)
                if m:
                    steps.append(m.group(1)[:200])
            if steps:
                log_info(f"📋 [BRAIN:plan] {len(steps)} گام")
            return steps[:5]
        except Exception:
            return []


    def _inject_knowledge(self, task):
        if not task:
            return ""
        parts = []

        # ۱) hybrid (FTS + Semantic — یک کوئری)
        try:
            if self.memory and hasattr(self.memory, "hybrid_recall"):
                hybrid = self.memory.hybrid_recall(task, n=5)
                if hybrid:
                    parts.append("# دانش مرتبط:")
                    for r in hybrid[:5]:
                        parts.append(
                            f"- [{r.get('source', '?')}] "
                            f"{r.get('content', '')[:200]}"
                        )
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:inject:hybrid] {e}")

        # ۲) lessons (SQL — سریع، با fallback کلمه‌به‌کلمه)
        try:
            if self.memory:
                lessons: list[dict] = []
                lessons = self.memory.long_term.search_lessons(task)
                if not lessons:
                    seen: set[str] = set()
                    for word in str(task).split():
                        w = word.strip("،.:؛!?؟«»\"'()[]{}")
                        if len(w) < 3 or w in seen:
                            continue
                        seen.add(w)
                        partial = self.memory.long_term.search_lessons(w)
                        for l in partial:
                            if l not in lessons:
                                lessons.append(l)
                        if len(lessons) >= 5:
                            break
                if lessons:
                    parts.append("\n# درس‌های قبلی:")
                    for lesson in lessons[:5]:
                        parts.append(
                            f"- قبلاً: {lesson['wrong'][:80]} "
                            f"→ درست: {lesson['correct'][:80]}"
                        )
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:inject:lessons] {e}")

        # ۳) تجربه‌های پلتفرم
        try:
            from core.brain.experience_learner import get_experience
            exp = get_experience()
            exp_text = exp.build_context(limit=3)
            if exp_text:
                parts.append(f"\n{exp_text}")
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:inject:exp] {e}")

        # ۴) دستورهای شرطی
        task_lower = task.lower()

        if any(kw in task_lower for kw in ("تست", "test", "pytest", "test_")):
            parts.append("""
# قانون مسیر تست (اجباری):
- تست دائمی → `tests/permanent/test_<name>.py`
- تست موقت → `tests/temp/_tmp_<name>.py`
- هرگز در `tests/` یا روت پروژه تست نساز.
- اگه تست موقت ساختی، آخرش پاکش کن.

# دستور تست‌نویسی (اجباری):
۱. اول فایل مربوطه رو بخون: `read_file(path)` یا `read_all(path)`
۲. با `generate_tests(path)` تست بساز (یا خودت `write_file` بنویس)
۳. با `run_tests('tests/test_X.py')` اجرا کن
۴. اگه fail شد، با `fix_bug(path, error)` fix کن
۵. دوباره `run_tests` بزن

⛔ NEVER فقط بگو «تست نوشتم» — باید واقعاً فایل ساخته و اجرا شود.
""")

        if any(kw in task_lower for kw in ("audit", "بررسی", "چک", "خطا", "باگ", "fix")):
            parts.append("""
# دستور audit (اجباری):
۱. `audit_code(paths)` یا `audit_file(file_path)` صدا بزن
۲. خطاها رو دسته‌بندی کن
۳. با `fix_fstrings(dry_run=False)` یا `fix_bug` رفع کن
۴. دوباره `audit_code` بزن تا خطا صفر شه

⛔ NEVER فقط لیست خطا بده — باید fix کنی.
""")

        if any(kw in task_lower for kw in ("آپدیت", "به‌روز", "update", "upgrade")):
            parts.append("""
# دستور آپدیت (اجباری):
۱. `git_status` صدا بزن
۲. اگه تغییر هست، `git_commit(message)` بزن
۳. `run_tests` بزن
۴. اگه fail شد، `fix_bug`
۵. `git_commit` دوباره

⛔ NEVER بگو «انجام شد» بدون commit واقعی.
""")

        return "\n".join(parts) if parts else ""

    async def _reflect(self, task, answer):
        # _reflect غیرفعال شد — پاسخ اصلی حفظ می‌شه
        return answer


    async def ask_multi(self, question):
        if not question or not question.strip():
            return "⚠️ سؤال خالی است."
        try:
            from core.brain.multi_agent import (
                get_multi_agent,
            )

            ma = get_multi_agent()
            return await ma.ask(question)
        except Exception as e:
            log_error(f"❌ [BRAIN:ask_multi] {type(e).__name__}: {e}")
            return f"❌ خطا در Multi-Agent: {e}"

    def get_client_stats(self):
        return {
            "client_ready": self.client is not None,
            "model": self.model,
            "api_key_set": bool(self.api_key),
            "api_key_prefix": (self.api_key[:8] + "..." if self.api_key else "—"),
            "priority": (AI_PRIORITY or "auto").lower(),
            "api_timeout_s": _API_TIMEOUT_S,
            "think_timeout_s": _THINK_TIMEOUT_S,
            "agent_max_tokens": _AGENT_MAX_TOKENS,
            "think_max_tokens": _THINK_MAX_TOKENS,
            "agent_temperature": _AGENT_TEMPERATURE,
            "max_agent_steps": MAX_AGENT_STEPS,
            "max_fallback": _MAX_FALLBACK,
            "tools_count": len(self.ALLOWED_TOOLS),
        }