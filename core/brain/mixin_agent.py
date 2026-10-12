"""Agent loop — init_client, agent, tool execution, admin commands.

نسخهٔ ۵ (این نسخه):
  - 🎯 NEW: `_build_system_prompt` حالا از `config.get_ai_system_prompt`
    استفاده می‌کند (شخصیت متخصص پایش کد).
  - 🎯 FIX: `_build_system_prompt` حالا mode های audit/fix را می‌شناسد.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۴.

نسخهٔ ۴ (قبلی):
  - 🐛 FIX مهم: `_init_client` حالا در حالت `auto` اول از
    `config.AI["deepseek_api_key"]` می‌خواند (نه env مستقیم).
  - 🐛 FIX: `_init_client` حالا `config.AI["model"]` را هم
    می‌خواند (قبلاً فقط از env).
  - 🐛 FIX: `_delegate_keyword` حالا از `data.paths.DB_FILE`
    استفاده می‌کند.
  - 🐛 FIX: `_THINK_TIMEOUT_S` جدا از `_API_TIMEOUT_S`.
  - 🐛 FIX: `_QUICK_COMMANDS` کامل‌تر.
  - 🎯 لاگ‌های دقیق‌تر در `_init_client`.
  - 🎯 `get_client_stats()` متد جدید.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۳.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

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
from core.kernel.event_bus import EventBus
from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════
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


def _env_str(name: str, default: str = "") -> str:
    """خواندن str از env — با strip."""
    return (os.getenv(name) or default).strip()


# ═══════════════════════════════════════════════════════════
# ثابت‌ها
# ═══════════════════════════════════════════════════════════
_WRITE_TOOLS: frozenset[str] = frozenset({
    "write_file", "edit_file", "delete_file", "move_file",
})

# 🎯 env-based provider URLs
_DEEPSEEK_URL = _env_str(
    "DEEPSEEK_API_URL", "https://api.deepseek.com/v1"
)
_OPENAI_URL = _env_str(
    "OPENAI_API_URL", "https://api.openai.com/v1"
)
_LOCAL_CLAUDE_URL = _env_str(
    "LOCAL_CLAUDE_URL", "http://localhost:4000"
)

# 🎯 env-based agent parameters
_AGENT_MAX_TOKENS = _env_int(
    "BRAIN_AGENT_MAX_TOKENS", 4096,
    min_val=256, max_val=32_000,
)
_AGENT_TEMPERATURE = _env_float(
    "BRAIN_AGENT_TEMPERATURE", 0.1,
    min_val=0.0, max_val=2.0,
)
_THINK_MAX_TOKENS = _env_int(
    "BRAIN_THINK_MAX_TOKENS", 2048,
    min_val=256, max_val=16_000,
)
_THINK_TEMPERATURE = _env_float(
    "BRAIN_THINK_TEMPERATURE", 0.1,
    min_val=0.0, max_val=2.0,
)

# 🎯 FIX: timeout جدا برای think (معمولاً سریع‌تر از agent)
_API_TIMEOUT_S = _env_float(
    "BRAIN_AGENT_API_TIMEOUT_S", 120.0,
    min_val=5.0, max_val=600.0,
)
_THINK_TIMEOUT_S = _env_float(
    "BRAIN_THINK_TIMEOUT_S", 60.0,
    min_val=5.0, max_val=300.0,
)

_MAX_FALLBACK = _env_int(
    "BRAIN_MAX_FALLBACK", 1,
    min_val=0, max_val=10,
)
_LONG_TASK_LEN = _env_int(
    "BRAIN_LONG_TASK_LEN", 200,
    min_val=50, max_val=10_000,
)


# ═══════════════════════════════════════════════════════════
# 🎯 FIX: دستورات سریع — کامل‌تر
# ═══════════════════════════════════════════════════════════
_QUICK_COMMANDS: dict[str, str] = {
    # ─── status ───
    "/status": "status",
    "وضعیت": "status",
    "status": "status",
    "آمار": "status",
    "/stats": "stats",
    "stats": "stats",
    # ─── help ───
    "/help": "help",
    "راهنما": "help",
    "help": "help",
    "/راهنما": "help",
    "کمک": "help",
    # ─── clear ───
    "/clear": "clear",
    "پاک": "clear",
    "clear": "clear",
    # ─── restart ───
    "/restart": "restart",
    "ری‌استارت": "restart",
    "restart": "restart",
    # ─── logs ───
    "/logs": "logs",
    "لاگ": "logs",
    "logs": "logs",
    # ─── health ───
    "/health": "health",
    "سلامت": "health",
    "health": "health",
}


class AgentMixin:
    """Agent loop و tool execution — به Brain اضافه می‌شود."""

    ALLOWED_TOOLS = ALLOWED_TOOLS
    ALLOWED_DIRECT_COMMANDS = ALLOWED_DIRECT_COMMANDS

    # ═══════════════════════════════════════════
    # EventBus / DB
    # ═══════════════════════════════════════════
    def set_event_bus(self, event_bus: EventBus) -> None:
        self.event_bus = event_bus

    def set_database(self, db: Any) -> None:
        self.db = db

    async def _emit(self, event_name: str, data: Any) -> None:
        if self.event_bus:
            try:
                await self.event_bus.emit(
                    f"brain.{event_name}",
                    data,
                    source="brain",
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:emit] {event_name} | "
                    f"{type(e).__name__}: {e}"
                )

    # ═══════════════════════════════════════════
    # Client
    # ═══════════════════════════════════════════
    def _init_client(self) -> None:
        """ساخت کلاینت بر اساس `AI_PRIORITY` (از config).

        ═══════════════════════════════════════════
        🎯 FIX نسخهٔ ۴:
        ═══════════════════════════════════════════
        در حالت `auto`، اگر `self.api_key` خالی بود، حالا از
        `config.AI["deepseek_api_key"]` می‌خوانیم (منبع یکتا).
        این تضمین می‌کند که حتی اگر `Brain.__init__` api_key
        پاس ندهد، client با کلید config ساخته شود.
        """
        # ─── priority ───
        priority = (AI_PRIORITY or "auto").strip().lower()

        # ═══════════════════════════════════════════
        # 🎯 FIX: deepseek_api_key از config (اگر self.api_key خالی)
        # ═══════════════════════════════════════════
        _cfg_api_key = ""
        try:
            _cfg_api_key = (
                (AI or {}).get("deepseek_api_key") or ""
            ).strip()
        except Exception:
            _cfg_api_key = ""

        _env_api_key = (
            os.environ.get("DEEPSEEK_API_KEY", "") or ""
        ).strip()

        # ترتیب: self.api_key > config > env
        if self.api_key:
            effective_api_key = self.api_key
            key_source = "brain"
        elif _cfg_api_key:
            effective_api_key = _cfg_api_key
            key_source = "config"
            # به self.api_key هم ست کن تا بقیه جاها استفاده کنند
            self.api_key = _cfg_api_key
        elif _env_api_key:
            effective_api_key = _env_api_key
            key_source = "env"
            self.api_key = _env_api_key
        else:
            effective_api_key = ""
            key_source = "none"

        has_key = bool(effective_api_key)

        # 🐛 FIX: مدل None/غیر-str امن
        model_str = (
            str(self.model) if self.model is not None else ""
        )

        # اگر مدل با "ollama" شروع شود → اجباراً ollama
        if model_str.startswith("ollama"):
            priority = "ollama"

        # ═══════════════════════════════════════════
        # 🎯 FIX: تعیین use_ollama با در نظر گرفتن کلید effective
        # ═══════════════════════════════════════════
        if priority == "deepseek":
            if not has_key:
                self.client = None
                log_warning(
                    "⚠️ [BRAIN:client] deepseek انتخاب شد ولی "
                    "api_key نیست (نه در Brain، نه config، نه env)"
                )
                return
            use_ollama = False

        elif priority == "ollama":
            use_ollama = True

        else:  # auto
            # auto: اگر کلید داریم، DeepSeek اول
            use_ollama = not has_key

        # ─── import ───
        try:
            from openai import AsyncOpenAI
        except Exception as e:
            log_error(
                f"❌ [BRAIN:client] openai import | "
                f"{type(e).__name__}: {e}"
            )
            self.client = None
            return

        # ═══════════════════════════════════════════
        # Ollama
        # ═══════════════════════════════════════════
        try:
            if use_ollama:
                base_url = (
                    (AI.get("url") or DEFAULT_BASE_URL)
                    .rstrip("/") + "/v1"
                )
                self.model = (
                    AI.get("model")
                    or DEFAULT_MODEL
                    or "qwen2.5:3b"
                )
                timeout = int(
                    AI.get("timeout") or _API_TIMEOUT_S
                )
                self.client = AsyncOpenAI(
                    api_key="ollama",
                    base_url=base_url,
                    timeout=timeout,
                    max_retries=3,
                )
                log_success(
                    f"✅ [BRAIN:client] Ollama | "
                    f"model={self.model} | url={base_url} | "
                    f"timeout={timeout}s | "
                    f"api_key_source={key_source}"
                )
                return

            # ═══════════════════════════════════════════
            # DeepSeek / OpenAI / Local
            # ═══════════════════════════════════════════
            base_url = _DEEPSEEK_URL

            # 🎯 FIX: اگر مدل با deepseek/claude/gpt شروع نمی‌شود،
            # یعنی مدل ollama است ولی provider=deepseek انتخاب شده
            # → مدل را به deepseek-chat تغییر بده تا 404 نگیریم.
            if model_str.startswith("claude"):
                base_url = _LOCAL_CLAUDE_URL
            elif model_str.startswith("gpt"):
                base_url = _OPENAI_URL
            elif not (
                model_str.startswith("deepseek")
                or model_str.startswith("claude")
                or model_str.startswith("gpt")
            ):
                # 🎯 FIX مهم: مدل ollama با provider=deepseek
                old_model = self.model
                self.model = _env_str(
                    "DEEPSEEK_MODEL", "deepseek-chat"
                )
                log_warning(
                    f"⚠️ [BRAIN:client] مدل «{old_model}» "
                    f"با DeepSeek ناسازگار بود → "
                    f"«{self.model}»"
                )

            timeout = int(AI.get("timeout") or _API_TIMEOUT_S)
            self.client = AsyncOpenAI(
                api_key=effective_api_key,
                base_url=base_url,
                timeout=timeout,
                max_retries=3,
            )
            log_success(
                f"✅ [BRAIN:client] {base_url} | "
                f"model={self.model} | timeout={timeout}s | "
                f"api_key_source={key_source} | "
                f"key_prefix="
                f"{effective_api_key[:8] if effective_api_key else '—'}..."
            )

        except Exception as e:
            log_error(
                f"❌ [BRAIN:client] {type(e).__name__}: {e}"
            )
            self.client = None
            
    # ═══════════════════════════════════════════
    # Background index
    # ═══════════════════════════════════════════
    def _background_index(self) -> None:
        try:
            log_info("📚 [BRAIN:bg:index] شروع")
            self.semantic_search.index_files()
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:bg:index] "
                f"{type(e).__name__}: {e}"
            )
            self._index_started = False  # 🎯 اجازهٔ retry
            
    def _build_tools(self) -> list[dict]:
        """ساخت لیست تعریف ابزارها."""
        return build_tool_definitions()

    # ═══════════════════════════════════════════
    # Execute tool
    # ═══════════════════════════════════════════
    async def _execute_tool(
        self, name: str, args: dict
    ) -> str:
        if not name:
            return "❌ نام ابزار خالی است."

        t0 = time.monotonic()
        tool_info = self.ALLOWED_TOOLS.get(name)
        if not tool_info:
            log_warning(
                f"⛔ [BRAIN:tool] ناشناخته | name={name}"
            )
            return f"⛔ ابزار '{name}' مجاز نیست."

        # ─── read_only check ───
        if self.read_only and tool_info.get("risk") != "low":
            log_warning(
                f"⛔ [BRAIN:tool] read_only | name={name} | "
                f"risk={tool_info.get('risk')}"
            )
            return "⛔ حالت فقط‌خواندنی."

        # ─── write-tools path check ───
        if name in _WRITE_TOOLS:
            path = (
                args.get("path")
                or args.get("source")
                or args.get("dest")
            )
            if not path:
                return "❌ مسیر مشخص نشده."
            if (
                not self.full_access
                and not self._is_safe_write_path(path)
            ):
                return (
                    f"⛔ دسترسی نوشتن در '{path}' مجاز نیست."
                )

        # 🐛 FIX: چک TOOL_MAP
        tool_map = getattr(self, "TOOL_MAP", None)
        if not tool_map:
            log_error(
                f"❌ [BRAIN:tool] TOOL_MAP خالی | name={name}"
            )
            return (
                "❌ TOOL_MAP بارگذاری نشده — "
                "بازسازی کلاس ناموفق بوده."
            )

        func = tool_map.get(name)
        if not func:
            log_warning(
                f"⛔ [BRAIN:tool] پیاده‌نشده | name={name} | "
                f"available={len(tool_map)}"
            )
            return f"⛔ ابزار '{name}' پیاده‌نشده."

        # ─── execute ───
        try:
            result = await func(self, **args)
            elapsed_ms = (time.monotonic() - t0) * 1000
            result_str = str(result)

            if elapsed_ms > SLOW_TOOL_MS:
                log_warning(
                    f"🐌 [BRAIN:tool] {name} کند | "
                    f"{elapsed_ms:.0f}ms | "
                    f"out={len(result_str)}ch"
                )
            else:
                log_info(
                    f"🔧 [BRAIN:tool] {name} | "
                    f"{elapsed_ms:.0f}ms | "
                    f"out={len(result_str)}ch"
                )
            return result_str
        except TypeError as e:
            log_error(
                f"❌ [BRAIN:tool] {name} پارامتر | {e}"
            )
            return f"❌ پارامتر اشتباه: {e}"
        except Exception as e:
            log_error(
                f"❌ [BRAIN:tool] {name} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════════
    # Think / Model / Prompt
    # ═══════════════════════════════════════════
    async def _think(
        self, task: str, context: str = ""
    ) -> str:
        """تحلیل اولیه قبل از agent loop.

        🐛 FIX: timeout جدا از API (معمولاً سریع‌تر).
        """
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
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    temperature=_THINK_TEMPERATURE,
                    max_tokens=_THINK_MAX_TOKENS,
                ),
                timeout=_THINK_TIMEOUT_S,  # 🎯 FIX
            )
            elapsed_ms = (time.monotonic() - t0) * 1000

            # 🐛 FIX: content None امن
            content = ""
            try:
                content = (
                    r.choices[0].message.content or ""
                ).strip()
            except Exception:
                content = ""

            if elapsed_ms > SLOW_THINK_MS:
                log_warning(
                    f"🐌 [BRAIN:think] کند | "
                    f"{elapsed_ms:.0f}ms"
                )
            return content
        except TimeoutError:
            log_warning(
                f"⏰ [BRAIN:think] timeout {_THINK_TIMEOUT_S}s"
            )
            return ""
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:think] "
                f"{type(e).__name__}: {e}"
            )
            return ""

    def _select_model(self, task: str) -> str:
        """انتخاب مدل بر اساس نوع task.

        - در حالت `read_only`، `deepseek-reasoner` نمی‌سازد.
        """
        # 🐛 FIX: normalize AI_PRIORITY
        priority = (AI_PRIORITY or "auto").strip().lower()
        if priority == "ollama" or not self.api_key:
            return self.model
        if self.read_only:
            return self.model

        coding_pats = [
            r"\b(کد|بنویس|develop|write|create|build|python"
            r"|تابع|کلاس)\b",
            r"\b(refactor|debug|fix|test|api|database|sql|query)\b",
            r"\b(رفع|اشکال|خطا|error|bug|issue)\b",
        ]
        if (
            any(re.search(p, task, re.I) for p in coding_pats)
            or len(task) > _LONG_TASK_LEN
        ):
            log_info(
                "🎯 [BRAIN:model] deepseek-flash انتخاب شد"
            )
            return "deepseek-flash"
        return self.model

    def _build_system_prompt(self, mode: str) -> str:
        """🎯 ساخت system prompt بر اساس mode — از config.

        این متد از `config.get_ai_system_prompt(mode)` استفاده می‌کند
        که شخصیت متخصص پایش کد را برمی‌گرداند.

        Args:
            mode: حالت پردازش (audit, fix, code, chat, analyze, precise, ...).

        Returns:
            system prompt کامل.
        """
        try:
            from config import get_ai_system_prompt
            prompt = get_ai_system_prompt(mode)
            if prompt:
                # 🎯 تزریق درس‌های قبلی (Reflexion)
                lessons = getattr(self, "_lessons", [])
                if lessons:
                    prompt += "\n\n# درس‌های قبلی (از اشتباهات گذشته):\n"
                    for lesson in lessons[:3]:
                        prompt += f"- {lesson[:200]}\n"
                return prompt
        except ImportError as e:
            log_warning(
                f"⚠️ [BRAIN:prompt] config import | "
                f"{type(e).__name__}: {e} — fallback"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:prompt] get_ai_system_prompt | "
                f"{type(e).__name__}: {e} — fallback"
            )

        # ─── fallback امن ───
        fallback = (
            "You are a world-class software engineer. "
            "Always respond in Persian (فارسی). "
            "Be concise, precise, and helpful."
        )
        lessons = getattr(self, "_lessons", [])
        if lessons:
            fallback += "\n\n# درس‌های قبلی:\n"
            for lesson in lessons[:3]:
                fallback += f"- {lesson[:200]}\n"
        return fallback

    # ═══════════════════════════════════════════
    # Agent loop
    # ═══════════════════════════════════════════
    async def agent(
        self, task: str, mode: str = "precise"
    ) -> str:
        """حلقهٔ اصلی agent — با tool calls و memory."""
        if not task:
            return "⚠️ تسک خالی است."

        if self.busy:
            log_warning("⚠️ [BRAIN:agent] busy — رد شد")
            return "⏳ مغز مشغول است..."

        self.busy = True
        self.stats["agents_run"] += 1
        t_start = time.monotonic()

        log_info(
            f"🚀 [BRAIN:agent] شروع | mode={mode} | "
            f"task={task[:LOG_PREVIEW_LEN]!r} | "
            f"run#{self.stats['agents_run']}"
        )

        step = 0
        try:
            # ─── cache ───
            try:
                cached = self.agent_cache.get(
                    task, self.model
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:agent] cache.get | "
                    f"{type(e).__name__}: {e}"
                )
                cached = None

            if cached:
                log_success(
                    f"💡 [BRAIN:agent] از کش | "
                    f"{time.monotonic() - t_start:.2f}s"
                )
                return cached

            # ─── memory ───
            try:
                self.memory.add("user", task)
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:agent] memory.add | "
                    f"{type(e).__name__}: {e}"
                )
            await self._emit(
                "command", {"task": task, "mode": mode}
            )

            # ─── think ───
            thinking = await self._think(task, context="")
            selected_model = self._select_model(task)
            system_prompt = self._build_system_prompt(mode)

            try:
                context_history = (
                    await self.memory.get_context(task)
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:agent] memory.get_context | "
                    f"{type(e).__name__}: {e}"
                )
                context_history = []

            messages: list[dict] = (
                [{"role": "system", "content": system_prompt}]
                + context_history
                + [{"role": "user", "content": task}]
            )
            if thinking:
                messages.insert(
                    1,
                    {
                        "role": "system",
                        "content": f"تحلیل اولیه:\n{thinking}",
                    },
                )

            final_answer = ""
            tools_used: list[str] = []
            fallback_count = 0

            while step < MAX_AGENT_STEPS:
                step += 1
                step_t0 = time.monotonic()

                if not self.client:
                    if OFFLINE_MODE_ENABLED:
                        final_answer = self._offline_agent(task)
                    else:
                        final_answer = (
                            "⚠️ مغز در دسترس نیست "
                            "(DeepSeek متصل نشد)."
                        )
                    break

                # ─── API call (با timeout) ───
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
                    log_error(
                        f"⏰ [BRAIN:agent] API timeout "
                        f"گام {step} | {_API_TIMEOUT_S}s"
                    )
                    final_answer = (
                        f"⏰ API timeout ({_API_TIMEOUT_S}s) — "
                        f"گام {step}"
                    )
                    break
                except Exception as e:
                    log_error(
                        f"❌ [BRAIN:agent] API گام {step} | "
                        f"{type(e).__name__}: {e}"
                    )
                    # fallback به مدل پیش‌فرض
                    if (
                        (AI_PRIORITY or "").lower() != "ollama"
                        and selected_model != DEFAULT_MODEL
                        and fallback_count < _MAX_FALLBACK
                    ):
                        log_warning(
                            f"⚠️ [BRAIN:agent] fallback به "
                            f"{DEFAULT_MODEL} "
                            f"({fallback_count + 1}/{_MAX_FALLBACK})"
                        )
                        selected_model = DEFAULT_MODEL
                        fallback_count += 1
                        step -= 1  # این iteration را دوباره
                        continue
                    final_answer = f"❌ خطای API: {e}"
                    break

                msg = response.choices[0].message

                # ─── بدون tool call → پاسخ نهایی ───
                if not msg.tool_calls:
                    final_answer = (
                        msg.content or "✅ عملیات تکمیل شد."
                    )
                    messages.append({
                        "role": "assistant",
                        "content": final_answer,
                    })
                    break

                # ─── tool calls ───
                messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                })

                for tc in msg.tool_calls:
                    name = tc.function.name
                    tools_used.append(name)
                    try:
                        args = (
                            json.loads(tc.function.arguments)
                            if tc.function.arguments else {}
                        )
                    except json.JSONDecodeError as e:
                        log_warning(
                            f"⚠️ [BRAIN:agent] args JSON "
                            f"نامعتبر | {name} | {e} | "
                            f"raw={tc.function.arguments[:80]!r}"
                        )
                        args = {}

                    log_info(
                        f"     🔧 [BRAIN:agent] {name} | "
                        f"args={str(args)[:LOG_PREVIEW_LEN]}"
                    )
                    result = await self._execute_tool(name, args)
                    result_str = str(result)
                    log_info(
                        f"     📤 [BRAIN:agent] {name} → "
                        f"{result_str[:LOG_PREVIEW_LEN]}"
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_str[:MAX_CONTENT_LENGTH],
                    })

                    # ─── auto-run tests ───
                    if (
                        name in ("write_file", "edit_file")
                        and "✅" in result_str
                        and "خطا" not in result_str
                    ):
                        test_res = await self._auto_run_tests(
                            args.get("path", "")
                        )
                        if test_res:
                            messages.append({
                                "role": "system",
                                "content": (
                                    f"🧪 تست خودکار:\n{test_res}"
                                ),
                            })

                step_elapsed_ms = (
                    time.monotonic() - step_t0
                ) * 1000
                if step_elapsed_ms > SLOW_STEP_MS:
                    log_warning(
                        f"🐌 [BRAIN:agent] گام {step} کند | "
                        f"{step_elapsed_ms:.0f}ms"
                    )

            final_answer = (
                final_answer
                or f"⚠️ سقف گام ({MAX_AGENT_STEPS})."
            )

            # ─── cache ───
            try:
                self.agent_cache.set(
                    task, final_answer, self.model
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:agent] cache.set | "
                    f"{type(e).__name__}: {e}"
                )

            # ─── memory ───
            try:
                self.memory.add("assistant", final_answer)
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:agent] memory.add | "
                    f"{type(e).__name__}: {e}"
                )

            # 🎯 FIX: فقط در ۲۰۰ کاراکتر اول چک کن (نه کل جواب)
            _head = final_answer[:200]
            success = (
                "❌" not in _head
                and not _head.startswith("خطا")
            )
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
                    f"⚠️ [BRAIN:agent] learning | "
                    f"{type(e).__name__}: {e}"
                )

            total_s = time.monotonic() - t_start
            log_success(
                f"✅ [BRAIN:agent] کامل | steps={step} | "
                f"tools={len(tools_used)} | {total_s:.1f}s | "
                f"success={success}"
            )
            return final_answer

        except Exception as e:
            self.stats["errors"] += 1
            log_error(
                f"❌ [BRAIN:agent] {type(e).__name__}: {e} | "
                f"step={step}"
            )
            # 🎯 ذخیره در Reflexion (MemoryStore)
            try:
                from core.adapters.memory_store import MemoryStore
                store = MemoryStore()
                store.add(
                    category="reflexion",
                    key=f"error:{type(e).__name__}",
                    value=f"تسک: {task[:200]} | خطا: {e}",
                    importance=0.8,
                )
            except Exception:
                pass
            return f"❌ خطای غیرمنتظره: {e}"
        finally:
            self.busy = False

    def _offline_agent(self, task: str) -> str:
        """پاسخ آفلاین — بدون AI.

        🐛 FIX: از word-boundary استفاده می‌کنیم (قبلاً substring بود).
        """
        if not task:
            return "❓ چطور می‌تونم کمکت کنم؟"

        t = task.strip().lower()

        for cmd, action in self.ALLOWED_DIRECT_COMMANDS.items():
            # 🐛 FIX: word-boundary برای triggerهای کوتاه
            cmd_low = cmd.lower()
            if len(cmd_low) <= 3:
                # چک دقیق برای trigger کوتاه
                if t == cmd_low or t.startswith(
                    cmd_low + " "
                ):
                    pass
                else:
                    continue
            else:
                # substring برای trigger بلند
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

        return (
            "🔍 حالت آفلاین.\n"
            "• /status — وضعیت\n"
            "• /help — راهنما"
        )

    async def _auto_run_tests(self, file_path: str) -> str:
        """اجرای خودکار تست برای فایل."""
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
                    return await self._run_tests(str(tf))
            except Exception:
                continue
        return ""

    # ═══════════════════════════════════════════
    # Admin commands
    # ═══════════════════════════════════════════
    async def process_admin_command(self, text: str) -> str:
        """پردازش دستور مدیر — با AI intent + agent fallback."""
        if not text:
            return ""

        self.stats["commands_processed"] += 1
        time.monotonic()
        log_info(
            f"👤 [BRAIN:cmd] "
            f"#{self.stats['commands_processed']} | "
            f"{text[:LOG_PREVIEW_LEN]!r}"
        )

        try:
            text = PersianBrainHandler.fix(text)
        except Exception:
            pass

        t = text.strip().lower()

        # ─── دستورات سریع ───
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
            return (
                "🔄 برای ری‌استارت، از ChatOps استفاده کن: "
                "«ری‌استارت» در بله."
            )
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

        # ─── حالت ───
        if t.startswith("/mode ") or t.startswith("حالت "):
            try:
                mode = t.split(maxsplit=1)[1]
                self.set_mode(mode)
                return f"✅ حالت '{mode}' فعال شد."
            except Exception as e:
                return f"❌ خطا: {e}"

        # ─── AI intent ───
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
                    log_info(
                        f"🎯 [BRAIN:cmd] intent | "
                        f"action={action} | "
                        f"args={list(args.keys())}"
                    )
                    result = await self._handle_action(
                        action, args, text
                    )
                    if result:
                        return result
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:cmd] AI assistant | "
                    f"{type(e).__name__}: {e}"
                )

        # ─── agent fallback ───
        if self.client:
            return await self.agent(text)

        # 🎯 اگه آفلاین خاموشه، پیام خطا بده
        if not OFFLINE_MODE_ENABLED:
            return "⚠️ مغز در دسترس نیست (DeepSeek متصل نشد)."

        return self._offline_agent(text)
        
    async def _handle_action(
        self, action: str, args: dict, original_text: str
    ) -> str | None:
        """اجرای action استخراج‌شده توسط AI."""
        log_info(
            f"🎬 [BRAIN:action] {action} | "
            f"args={list(args.keys())}"
        )
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
                platform = args.get("value", "")
                result = await self._get_platform_status(
                    platform
                )
            elif action == "report":
                result = await self._get_report()
            elif action == "crm":
                result = (
                    "📊 خروجی CRM توسط admin_handler ساخته "
                    "می‌شود.\n"
                    "دستور «crm» یا «crm <platform>» "
                    "(مثلاً «crm ایتا») را در بله بفرست."
                )
            elif action == "logs":
                result = self._get_recent_logs()
            elif action == "lead_details":
                which = args.get("value", "last")
                result = await self._get_lead_details(which)

            # 🐛 FIX: delegate به manual_brain
            elif action == "add_keyword":
                result = await self._delegate_keyword(
                    "add", args.get("value", "")
                )
            elif action == "remove_keyword":
                result = await self._delegate_keyword(
                    "remove", args.get("value", "")
                )

            elif action == "has_keyword":
                kw = args.get("value", "")
                result = await self._check_keyword(kw)

            elif action in ("ask", "feature_request"):
                result = await self.agent(original_text)

            else:
                log_warning(
                    f"⚠️ [BRAIN:action] ناشناخته | "
                    f"action={action}"
                )
                result = None
        except Exception as e:
            log_error(
                f"❌ [BRAIN:action] {action} | "
                f"{type(e).__name__}: {e}"
            )
            result = None

        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms > SLOW_ACTION_MS:
            log_warning(
                f"🐌 [BRAIN:action] {action} کند | "
                f"{elapsed_ms:.0f}ms"
            )
        return result

    # ═══════════════════════════════════════════
    # 🎯 FIX: _delegate_keyword با data.paths
    # ═══════════════════════════════════════════
    async def _delegate_keyword(
        self, op: str, kw: str
    ) -> str:
        """🎯 delegate کردن add/remove keyword به manual_brain.

        🐛 FIX: مسیر DB از `data.paths.DB_FILE` می‌آید
        (قبلاً hardcode بود و در PostgreSQL شکست می‌خورد).
        """
        if not kw:
            return "⚠️ کلیدواژه خالی است."

        # ─── مسیر DB ───
        db_path = ""
        try:
            # اول از db instance
            db_path = getattr(self.db, "db_path", "") or ""
        except Exception:
            pass

        if not db_path:
            # fallback: data.paths.DB_FILE
            try:
                from data import paths as data_paths
                db_path = str(data_paths.DB_FILE)
            except Exception:
                db_path = "storage/database/messages.db"

        try:
            from core.manual.brain import get_manual_brain
            mbrain = get_manual_brain(db_path)
            if op == "add":
                return mbrain._add_keyword(kw)
            elif op == "remove":
                return mbrain._remove_keyword(kw)
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:delegate] {op} | db={db_path} | "
                f"{type(e).__name__}: {e}"
            )
        return (
            f"🔧 برای {op} کلیدواژه «{kw}» از ChatOps استفاده کن: "
            f"«کلیدواژه «{kw}» را اضافه کن»"
        )

    # ═══════════════════════════════════════════
    # Build from map / self upgrade
    # ═══════════════════════════════════════════
    async def build_from_map(
        self, task: str, max_retries: int = 3
    ) -> str:
        """ساخت از نقشه با planner."""
        if not self.planner:
            return "❌ برنامه‌ریز نیست."
        if not self.client:
            return "❌ حالت آفلاین."
        try:
            result = await self.planner.execute_plan(task)
            return f"🏗️ **ساخت از نقشه:**\n{result}"
        except Exception as e:
            log_error(
                f"❌ [BRAIN:build_map] "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    async def self_upgrade(
        self, upgrade_request: str = "بهبود کد"
    ) -> str:
        """خودارتقا — بازبینی کد خود."""
        if not self.client:
            return "❌ حالت آفلاین."
        try:
            prompt = f"خودت را بازبینی کن: {upgrade_request}"
            result = await self.agent(prompt, mode="analyze")
            return f"🔄 **خودارتقا:**\n{result}"
        except Exception as e:
            log_error(
                f"❌ [BRAIN:self_upgrade] "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════════
    # 🎯 NEW: get_client_stats برای debug
    # ═══════════════════════════════════════════
    def get_client_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت client — برای debug."""
        return {
            "client_ready": self.client is not None,
            "model": self.model,
            "api_key_set": bool(self.api_key),
            "api_key_prefix": (
                self.api_key[:8] + "..."
                if self.api_key else "—"
            ),
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