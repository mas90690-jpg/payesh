"""Agent loop — init_client, agent, tool execution, admin commands.

نسخهٔ ۶:
  - 🐛 FIX: _build_system_prompt — platform_hint حالا قبل از return.
  - 🐛 FIX: _reflect — بدنهٔ خالی پر شد.
  - 🐛 FIX: agent — بلوک تکراری حذف شد.
  - 🐛 FIX: except تکراری memory.add حذف شد.
  - 🐛 FIX: import re تکراری داخل _plan_steps حذف شد.
  - 🐛 FIX: get_client_stats — docstring سالم بدون emoji.
"""
from __future__ import annotations

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


_WRITE_TOOLS = frozenset({
    "write_file", "edit_file", "delete_file", "move_file",
})

_DEEPSEEK_URL = _env_str("DEEPSEEK_API_URL", "https://api.deepseek.com/v1")
_OPENAI_URL = _env_str("OPENAI_API_URL", "https://api.openai.com/v1")
_LOCAL_CLAUDE_URL = _env_str("LOCAL_CLAUDE_URL", "http://localhost:4000")

_AGENT_MAX_TOKENS = _env_int("BRAIN_AGENT_MAX_TOKENS", 4096, 256, 32000)
_AGENT_TEMPERATURE = _env_float("BRAIN_AGENT_TEMPERATURE", 0.1, 0.0, 2.0)
_THINK_MAX_TOKENS = _env_int("BRAIN_THINK_MAX_TOKENS", 2048, 256, 16000)
_THINK_TEMPERATURE = _env_float("BRAIN_THINK_TEMPERATURE", 0.1, 0.0, 2.0)
_API_TIMEOUT_S = _env_float("BRAIN_AGENT_API_TIMEOUT_S", 120.0, 5.0, 600.0)
_THINK_TIMEOUT_S = _env_float("BRAIN_THINK_TIMEOUT_S", 60.0, 5.0, 300.0)
_MAX_FALLBACK = _env_int("BRAIN_MAX_FALLBACK", 1, 0, 10)
_LONG_TASK_LEN = _env_int("BRAIN_LONG_TASK_LEN", 200, 50, 10000)


_QUICK_COMMANDS = {
    "/status": "status", "وضعیت": "status", "status": "status",
    "آمار": "status", "/stats": "stats", "stats": "stats",
    "/help": "help", "راهنما": "help", "help": "help",
    "/راهنما": "help", "کمک": "help",
    "/clear": "clear", "پاک": "clear", "clear": "clear",
    "/restart": "restart", "ری‌استارت": "restart", "restart": "restart",
    "/logs": "logs", "لاگ": "logs", "logs": "logs",
    "/health": "health", "سلامت": "health", "health": "health",
}


class AgentMixin:
    ALLOWED_TOOLS = ALLOWED_TOOLS
    ALLOWED_DIRECT_COMMANDS = ALLOWED_DIRECT_COMMANDS

    def set_event_bus(self, event_bus):
        self.event_bus = event_bus

    def set_database(self, db):
        self.db = db

    async def _emit(self, event_name, data):
        if self.event_bus:
            try:
                await self.event_bus.emit(
                    f"brain.{event_name}", data, source="brain"
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:emit] {event_name} | "
                    f"{type(e).__name__}: {e}"
                )

    def _init_client(self):
        priority = (AI_PRIORITY or "auto").strip().lower()
        try:
            _cfg_api_key = (
                (AI or {}).get("deepseek_api_key") or ""
            ).strip()
        except Exception:
            _cfg_api_key = ""
        _env_api_key = (
            os.environ.get("DEEPSEEK_API_KEY", "") or ""
        ).strip()

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

        if priority == "deepseek":
            if not has_key:
                self.client = None
                log_warning(
                    "⚠️ [BRAIN:client] deepseek بدون api_key"
                )
                return
            use_ollama = False
        elif priority == "ollama":
            use_ollama = True
        else:
            use_ollama = not has_key

        try:
            from openai import AsyncOpenAI
        except Exception as e:
            log_error(
                f"❌ [BRAIN:client] openai import | "
                f"{type(e).__name__}: {e}"
            )
            self.client = None
            return

        try:
            if use_ollama:
                base_url = (
                    (AI.get("url") or DEFAULT_BASE_URL).rstrip("/")
                    + "/v1"
                )
                self.model = (
                    AI.get("model") or DEFAULT_MODEL or "qwen2.5:3b"
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
                    f"model={self.model} | url={base_url}"
                )
                return

            base_url = _DEEPSEEK_URL
            if model_str.startswith("claude"):
                base_url = _LOCAL_CLAUDE_URL
            elif model_str.startswith("gpt"):
                base_url = _OPENAI_URL
            elif not (
                model_str.startswith(("deepseek", "claude", "gpt"))
            ):
                old_model = self.model
                self.model = _env_str(
                    "DEEPSEEK_MODEL", "deepseek-chat"
                )
                log_warning(
                    f"⚠️ [BRAIN:client] مدل «{old_model}» → "
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
                f"model={self.model} | key_source={key_source}"
            )
        except Exception as e:
            log_error(
                f"❌ [BRAIN:client] {type(e).__name__}: {e}"
            )
            self.client = None

    def _background_index(self):
        try:
            log_info("📚 [BRAIN:bg:index] شروع")
            self.semantic_search.index_files()
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:bg:index] "
                f"{type(e).__name__}: {e}"
            )
            self._index_started = False

    def _build_tools(self):
        return build_tool_definitions()

    async def _execute_tool(self, name, args):
        if not name:
            return "❌ نام ابزار خالی است."
        t0 = time.monotonic()
        tool_info = self.ALLOWED_TOOLS.get(name)
        if not tool_info:
            return f"⛔ ابزار '{name}' مجاز نیست."
        if self.read_only and tool_info.get("risk") != "low":
            return "⛔ حالت فقط‌خواندنی."
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
                log_warning(
                    f"🐌 [BRAIN:tool] {name} کند | "
                    f"{elapsed_ms:.0f}ms"
                )
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
            log_error(
                f"❌ [BRAIN:tool] {name} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    async def _think(self, task, context=""):
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
                timeout=_THINK_TIMEOUT_S,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
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
                f"⏰ [BRAIN:think] timeout "
                f"{_THINK_TIMEOUT_S}s"
            )
            return ""
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:think] "
                f"{type(e).__name__}: {e}"
            )
            return ""

    def _select_model(self, task):
        priority = (AI_PRIORITY or "auto").strip().lower()
        if priority == "ollama" or not self.api_key:
            return self.model
        if self.read_only:
            return self.model
        coding_pats = [
            (r"\b(کد|بنویس|develop|write|create|build|python"
            r"|تابع|کلاس)\b"),
            (r"\b(refactor|debug|fix|test|api|database|sql"
            r"|query)\b"),
            r"\b(رفع|اشکال|خطا|error|bug|issue)\b",
        ]
        if (
            any(re.search(p, task, re.IGNORECASE) for p in coding_pats)
            or len(task) > _LONG_TASK_LEN
        ):
            log_info(
                "🎯 [BRAIN:model] deepseek-chat انتخاب شد"
            )
            return "deepseek-chat"
        return self.model

    def _build_system_prompt(self, mode):
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
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:prompt] config | "
                f"{type(e).__name__}: {e}"
            )
        if not base:
            base = (
                "You are a world-class software engineer. "
                "Always respond in Persian. Be concise, "
                "precise, and helpful."
            )
        base = cot_prefix + base
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
            except Exception:
                pass
        platform_hint = ""
        try:
            _last = getattr(self, "_last_task", "") or ""
            _plats = {
                "بله": "bale",
                "ایتا": "eitaa",
                "روبیکا": "rubika",
                "تلگرام": "telegram",
                "واتساپ": "whatsapp",
                "لینکدین": "linkedin",
                "یوتیوب": "youtube",
            }
            _found = [
                p for k, p in _plats.items() if k in _last
            ]
            if _found:
                platform_hint = (
                    f"\n\n# پلتفرم هدف: {', '.join(_found)}"
                )
        except Exception:
            pass
        if platform_hint:
            base += platform_hint
        return base

    async def agent(
        self, task, mode="precise", bypass_busy=False
    ):

        # ⚡ پاسخ سریع برای سوالات ساده (بدون AI)
        _simple = task.strip().lower()
        if _simple in (
            "سلام", "درود", "hi", "hello", "hey",
            "چطوری", "خوبی", "چه خبر", "خوبی؟",
            "ممنون", "مرسی", "thanks", "thank you",
        ) or len(_simple) <= 3:
            import random
            _greetings = [
                "سلام! 👋 چطور می‌تونم کمکت کنم؟",
                "درود! 😊 آماده‌ام.",
                "سلام! چه کاری برات انجام بدم؟",
            ]
            log_info(f"⚡ [BRAIN:agent] پاسخ سریع | task={task[:40]!r}")
            return random.choice(_greetings)
        if not bypass_busy and self.busy:
            log_info(
                f"⏳ [BRAIN:agent] صف انتظار | "
                f"task={task[:60]!r}"
            )
            for _ in range(60):
                await asyncio.sleep(1)
                if not self.busy:
                    break
            else:
                return "⏰ مغز ۶۰ ثانیه مشغول بود."
        if not bypass_busy:
            self.busy = True
            self._busy_started_at = time.monotonic()
        else:
            log_info(
                f"🔓 [BRAIN:agent] bypass_busy | "
                f"task={task[:60]!r}"
            )
        self.stats["agents_run"] += 1
        t_start = time.monotonic()
        log_info(
            f"🚀 [BRAIN:agent] شروع | mode={mode} | "
            f"task={task[:LOG_PREVIEW_LEN]!r}"
        )
        step = 0
        try:
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
                return cached
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
            _need_think = (
                len(task) > 50
                or any(
                    kw in task.lower()
                    for kw in (
                        "چرا",
                        "چطور",
                        "چگونه",
                        "مشکل",
                        "خطا",
                        "باگ",
                        "نمی",
                        "وصل",
                        "کار نمی",
                        "درست کار",
                    )
                )
            )
            thinking = (
                await self._think(task, context="")
                if _need_think
                else ""
            )
            selected_model = self._select_model(task)
            system_prompt = self._build_system_prompt(mode)
            knowledge = self._inject_knowledge(task)
            if knowledge:
                system_prompt += f"\n\n{knowledge}"
            plan_steps = (
                await self._plan_steps(task)
                if len(task) > 30
                else []
            )
            if plan_steps:
                system_prompt += (
                    "\n\n# برنامه پیشنهادی:\n"
                    + "\n".join(
                        f"  {i + 1}. {s}"
                        for i, s in enumerate(plan_steps)
                    )
                )
            try:
                context_history = await self.memory.get_context(
                    task
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:agent] memory.get_context | "
                    f"{type(e).__name__}: {e}"
                )
                context_history = []
            messages = (
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
                    final_answer = (
                        f"⏰ API timeout ({_API_TIMEOUT_S}s)"
                        f" — گام {step}"
                    )
                    break
                except Exception as e:
                    log_error(
                        f"❌ [BRAIN:agent] API گام {step} | "
                        f"{type(e).__name__}: {e}"
                    )
                    if (
                        (AI_PRIORITY or "").lower()
                        != "ollama"
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
                    final_answer = (
                        msg.content or "✅ عملیات تکمیل شد."
                    )
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
                                    "arguments": (
                                        tc.function.arguments
                                    ),
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
                            f"⚠️ [BRAIN:agent] args JSON "
                            f"نامعتبر | {name} | {e}"
                        )
                        args = {}
                    log_info(
                        f"     🔧 [BRAIN:agent] {name} | "
                        f"args="
                        f"{str(args)[:LOG_PREVIEW_LEN]}"
                    )
                    result = await self._execute_tool(name, args)
                    result_str = str(result)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_str[
                                :MAX_CONTENT_LENGTH
                            ],
                        }
                    )
                    if (
                        name in ("write_file", "edit_file")
                        and "✅" in result_str
                        and "خطا" not in result_str
                    ):
                        test_res = await self._auto_run_tests(
                            args.get("path", "")
                        )
                        if test_res:
                            messages.append(
                                {
                                    "role": "system",
                                    "content": (
                                        f"🧪 تست خودکار:\n"
                                        f"{test_res}"
                                    ),
                                }
                            )
                step_elapsed_ms = (
                    time.monotonic() - step_t0
                ) * 1000
                if step_elapsed_ms > SLOW_STEP_MS:
                    log_warning(
                        f"🐌 [BRAIN:agent] گام {step} کند"
                        f" | {step_elapsed_ms:.0f}ms"
                    )
            final_answer = (
                final_answer
                or f"⚠️ سقف گام ({MAX_AGENT_STEPS})."
            )
            if (
                final_answer
                and len(final_answer) > 50
                and "❌" not in final_answer[:50]
                and "⏰" not in final_answer[:50]
            ):
                final_answer = await self._reflect(
                    task, final_answer
                )
            try:
                self.agent_cache.set(
                    task, final_answer, self.model
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:agent] cache.set | "
                    f"{type(e).__name__}: {e}"
                )
            try:
                self.memory.add("assistant", final_answer)
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:agent] memory.add | "
                    f"{type(e).__name__}: {e}"
                )
            try:
                _head = final_answer[:150]
                success = (
                    "❌" not in _head
                    and "⏰" not in _head
                )
                self.memory.remember(
                    question=task,
                    answer=final_answer[:500],
                    importance=0.8 if success else 0.4,
                )
                if not success:
                    self.memory.add_lesson(
                        wrong=task[:200],
                        correct=(
                            f"پاسخ: {final_answer[:200]}"
                        ),
                    )
                    log_info(
                        "📚 [BRAIN:feedback] "
                        "درس جدید ذخیره شد"
                    )
            except Exception as e:
                log_warning(
                    f"⚠️ [BRAIN:feedback] "
                    f"{type(e).__name__}: {e}"
                )
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
                f"✅ [BRAIN:agent] کامل | "
                f"steps={step} | "
                f"tools={len(tools_used)} | "
                f"{total_s:.1f}s | success={success}"
            )
            return final_answer
        except Exception as e:
            self.stats["errors"] += 1
            log_error(
                f"❌ [BRAIN:agent] {type(e).__name__}: "
                f"{e} | step={step}"
            )
            try:
                from core.adapters.memory_store import (
                    MemoryStore,
                )
                store = MemoryStore()
                store.add(
                    category="reflexion",
                    key=f"error:{type(e).__name__}",
                    value=(
                        f"تسک: {task[:200]} | خطا: {e}"
                    ),
                    importance=0.8,
                )
            except Exception:
                pass
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
                if not (
                    t == cmd_low
                    or t.startswith(cmd_low + " ")
                ):
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
        return (
            "🔍 حالت آفلاین.\n"
            "• /status — وضعیت\n"
            "• /help — راهنما"
        )

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
                    return await self._run_tests(str(tf))
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
            return (
                "🔄 برای ری‌استارت، از ChatOps استفاده کن."
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
        if self.client:
            return await self.agent(text)
        if not OFFLINE_MODE_ENABLED:
            return "⚠️ مغز در دسترس نیست."
        return self._offline_agent(text)

    async def _handle_action(self, action, args, original_text):
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
                result = await self._get_platform_status(
                    args.get("value", "")
                )
            elif action == "report":
                result = await self._get_report()
            elif action == "crm":
                result = (
                    "📊 خروجی CRM توسط admin_handler."
                )
            elif action == "logs":
                result = self._get_recent_logs()
            elif action == "lead_details":
                result = await self._get_lead_details(
                    args.get("value", "last")
                )
            elif action == "add_keyword":
                result = await self._delegate_keyword(
                    "add", args.get("value", "")
                )
            elif action == "remove_keyword":
                result = await self._delegate_keyword(
                    "remove", args.get("value", "")
                )
            elif action == "has_keyword":
                result = await self._check_keyword(
                    args.get("value", "")
                )
            elif action in ("ask", "feature_request"):
                result = await self.agent(original_text)
            else:
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

    async def _delegate_keyword(self, op, kw):
        if not kw:
            return "⚠️ کلیدواژه خالی است."
        db_path = ""
        with contextlib.suppress(Exception):
            db_path = getattr(self.db, "db_path", "") or ""
        if not db_path:
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
                f"⚠️ [BRAIN:delegate] {op} | "
                f"db={db_path} | "
                f"{type(e).__name__}: {e}"
            )
        return (
            f"🔧 برای {op} کلیدواژه «{kw}» "
            f"از ChatOps استفاده کن."
        )

    async def build_from_map(self, task, max_retries=3):
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
            prompt = (
                f"خودت را بازبینی کن: {upgrade_request}"
            )
            result = await self.agent(
                prompt, mode="analyze"
            )
            return f"🔄 خودارتقا:\n{result}"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _plan_steps(self, task):
        if not self.client or not task:
            return []
        if len(task) < 30:
            return []
        prompt = (
            "برای این سؤال، یه برنامه ۱-۵ مرحله‌ای "
            "بنویس:\n\n"
            f"**سؤال:** {task}\n\n"
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
            result = (
                r.choices[0].message.content or ""
            ).strip()
            if "ساده" in result[:20]:
                return []
            steps = []
            for line in result.split("\n"):
                line = line.strip()
                if not line:
                    continue
                m = re.match(
                    r"^[\d۰-۹]+[\.\-]\s*(.+)", line
                )
                if m:
                    steps.append(m.group(1)[:200])
            if steps:
                log_info(
                    f"📋 [BRAIN:plan] {len(steps)} گام"
                )
            return steps[:5]
        except Exception:
            return []

    def _inject_knowledge(self, task):
        if not task:
            return ""
        parts = []
        try:
            if (
                self.memory
                and self.memory.semantic.enabled
            ):
                docs = self.memory.semantic.search(
                    task, n=5
                )
                if docs:
                    parts.append("# دانش پروژه:")
                    for doc in docs[:5]:
                        parts.append(f"- {doc[:300]}")
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:inject:sem] {e}"
            )
        try:
            if self.memory and hasattr(
                self.memory, "hybrid_recall"
            ):
                hybrid = self.memory.hybrid_recall(
                    task, n=3
                )
                if hybrid:
                    parts.append(
                        "\n# جستجوی ترکیبی:"
                    )
                    for r in hybrid[:3]:
                        parts.append(
                            f"- "
                            f"[{r.get('source', '?')}] "
                            f"{r.get('content', '')[:200]}"
                        )
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:inject:hybrid] {e}"
            )
        try:
            if self.memory:
                lessons = (
                    self.memory.long_term.search_lessons(
                        task
                    )
                )
                if lessons:
                    parts.append("\n# درس‌های قبلی:")
                    for lesson in lessons[:3]:
                        parts.append(
                            f"- قبلاً: "
                            f"{lesson['wrong'][:80]} "
                            f"→ درست: "
                            f"{lesson['correct'][:80]}"
                        )
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:inject:lessons] {e}"
            )
        try:
            from core.brain.experience_learner import (
                get_experience,
            )
            exp = get_experience()
            exp_text = exp.build_context(limit=3)
            if exp_text:
                parts.append(f"\n{exp_text}")
        except Exception:
            pass
        return "\n".join(parts) if parts else ""

    async def _reflect(self, task, answer):
        if not self.client or not answer:
            return answer
        prompt = (
            "تو یه بازبین دقیق هستی. "
            "این پاسخ رو بازبینی کن:\n\n"
            f"**سؤال:** {task[:300]}\n\n"
            f"**پاسخ تو:** {answer[:2000]}\n\n"
            "**بازبینی دقیق:**\n"
            "1. کاملی\n"
            "2. دقت\n"
            "3. عملی بودن\n"
            "4. روشنی\n"
            "5. پیش‌بینی\n\n"
            "اگه پاسخ کامل و دقیق بود، "
            "فقط بنویس: **خوب**\n\n"
            "اگه نه، پاسخ بهتر و کامل‌تر رو بنویس.\n"
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
                    max_tokens=1000,
                ),
                timeout=30.0,
            )
            reflection = (
                r.choices[0].message.content or ""
            ).strip()
            if (
                reflection == "خوب"
                or "خوب" in reflection[:10]
            ):
                log_info(
                    "✅ [BRAIN:reflect] پاسخ کامل"
                )
                return answer
            log_info(
                f"🔍 [BRAIN:reflect] بهبود یافت | "
                f"+{len(reflection) - len(answer)}ch"
            )
            return reflection
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:reflect] "
                f"{type(e).__name__}: {e}"
            )
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
            log_error(
                f"❌ [BRAIN:ask_multi] "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا در Multi-Agent: {e}"

    def get_client_stats(self):
        return {
            "client_ready": self.client is not None,
            "model": self.model,
            "api_key_set": bool(self.api_key),
            "api_key_prefix": (
                self.api_key[:8] + "..."
                if self.api_key
                else "—"
            ),
            "priority": (
                AI_PRIORITY or "auto"
            ).lower(),
            "api_timeout_s": _API_TIMEOUT_S,
            "think_timeout_s": _THINK_TIMEOUT_S,
            "agent_max_tokens": _AGENT_MAX_TOKENS,
            "think_max_tokens": _THINK_MAX_TOKENS,
            "agent_temperature": _AGENT_TEMPERATURE,
            "max_agent_steps": MAX_AGENT_STEPS,
            "max_fallback": _MAX_FALLBACK,
            "tools_count": len(self.ALLOWED_TOOLS),
        }
