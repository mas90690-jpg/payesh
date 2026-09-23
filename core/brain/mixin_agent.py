"""Agent loop — init_client, agent, tool execution, admin commands."""
from __future__ import annotations

import json
import re
import time
from typing import Any

from core.brain.constants import (
    AI,
    AI_ENABLED,
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


class AgentMixin:
    """Agent loop و tool execution — به Brain اضافه می‌شود."""

    ALLOWED_TOOLS = ALLOWED_TOOLS
    ALLOWED_DIRECT_COMMANDS = ALLOWED_DIRECT_COMMANDS

    # ═══════════════════════════════════════════
    # EventBus / DB
    # ═══════════════════════════════════════════
    def set_event_bus(self, event_bus: EventBus):
        self.event_bus = event_bus

    def set_database(self, db: Any):
        self.db = db

    async def _emit(self, event_name: str, data: Any):
        if self.event_bus:
            await self.event_bus.emit(
                f"brain.{event_name}", data, source="brain"
            )

    # ═══════════════════════════════════════════
    # Client
    # ═══════════════════════════════════════════
    def _init_client(self):
        """ساخت کلاینت بر اساس AI_PRIORITY."""
        priority = self._ai_priority
        has_key = bool(self.api_key)

        if self.model.startswith("ollama"):
            priority = "ollama"

        if priority == "deepseek":
            if not has_key:
                self.client = None
                return
            use_ollama = False
        elif priority == "ollama":
            use_ollama = True
        else:
            use_ollama = not has_key

        try:
            try:
                from openai import AsyncOpenAI
            except Exception as e:
                log_error(f"❌ [BRAIN:client] {e}")
                self.client = None
                return

            if use_ollama:
                base_url = (
                    AI.get("url", "http://localhost:11434")
                    .rstrip("/")
                    + "/v1"
                )
                self.model = AI.get("model", "qwen2.5:3b")
                self.client = AsyncOpenAI(
                    api_key="ollama",
                    base_url=base_url,
                    timeout=AI.get("timeout", 120),
                    max_retries=3,
                )
                return

            base_url = "https://api.deepseek.com/v1"
            if self.model.startswith("claude"):
                base_url = "http://localhost:4000"
            elif self.model.startswith("gpt"):
                base_url = "https://api.openai.com/v1"

            self.client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=base_url,
                timeout=AI.get("timeout", 120),
                max_retries=3,
            )
        except Exception as e:
            log_error(f"❌ [BRAIN:client] {e}")
            self.client = None

    def set_api_key(self, key: str):
        self.api_key = key
        self._init_client()

    # ═══════════════════════════════════════════
    # Background index
    # ═══════════════════════════════════════════
    def _background_index(self):
        try:
            log_info("📚 [BRAIN:bg] ایندکس پس‌زمینه شروع")
            self.semantic_search.index_files()
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:bg] {type(e).__name__}: {e}")

    def _build_tools(self) -> list[dict]:
        """ساخت لیست تعریف ابزارها."""
        return build_tool_definitions()

    # ═══════════════════════════════════════════
    # Execute tool
    # ═══════════════════════════════════════════
    async def _execute_tool(self, name: str, args: dict) -> str:
        t0 = time.monotonic()
        tool_info = self.ALLOWED_TOOLS.get(name)
        if not tool_info:
            log_warning(f"⛔ [BRAIN:tool] ناشناخته | {name}")
            return f"⛔ ابزار '{name}' مجاز نیست."
        if self.read_only and tool_info["risk"] != "low":
            log_warning(f"⛔ [BRAIN:tool] read_only | {name}")
            return "⛔ حالت فقط‌خواندنی."

        write_tools = {
            "write_file", "edit_file", "delete_file", "move_file",
        }
        if name in write_tools:
            path = (
                args.get("path")
                or args.get("source")
                or args.get("dest")
            )
            if not path:
                return "❌ مسیر مشخص نشده."
            if not self.full_access and not self._is_safe_write_path(
                path
            ):
                return f"⛔ دسترسی نوشتن در '{path}' مجاز نیست."

        func = self.TOOL_MAP.get(name)
        if not func:
            log_warning(f"⛔ [BRAIN:tool] پیاده‌نشده | {name}")
            return f"⛔ ابزار '{name}' پیاده‌نشده."

        try:
            result = await func(self, **args)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > SLOW_TOOL_MS:
                log_warning(
                    f"🐌 [BRAIN:tool] {name} کند | "
                    f"{elapsed_ms:.0f}ms"
                )
            else:
                log_info(
                    f"🔧 [BRAIN:tool] {name} | {elapsed_ms:.0f}ms | "
                    f"out={len(str(result))}ch"
                )
            return str(result)
        except TypeError as e:
            log_error(f"❌ [BRAIN:tool] {name} پارامتر | {e}")
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
    async def _think(self, task: str, context: str = "") -> str:
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
            r = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2048,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            content = r.choices[0].message.content.strip()
            if elapsed_ms > SLOW_THINK_MS:
                log_warning(
                    f"🐌 [BRAIN:think] کند | {elapsed_ms:.0f}ms"
                )
            return content
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:think] {type(e).__name__}: {e}")
            return ""

    def _select_model(self, task: str) -> str:
        if self._ai_priority == "ollama" or not self.api_key:
            return self.model
        coding_pats = [
            r"\b(کد|بنویس|develop|write|create|build|python"
            r"|تابع|کلاس)\b",
            r"\b(refactor|debug|fix|test|api|database|sql|query)\b",
            r"\b(رفع|اشکال|خطا|error|bug|issue)\b",
        ]
        if (
            any(re.search(p, task, re.I) for p in coding_pats)
            or len(task) > 200
        ):
            log_info("🎯 [BRAIN:model] deepseek-reasoner انتخاب شد")
            return "deepseek-reasoner"
        return self.model

    def _build_system_prompt(self, mode: str) -> str:
        prompts = {
            "code": (
                "You are a SENIOR SOFTWARE ENGINEER. Write "
                "PRODUCTION-READY code."
            ),
            "chat": "You are a FRIENDLY AI ASSISTANT.",
            "analyze": "You are a CODE ANALYST EXPERT.",
            "precise": "You are a world-class software engineer.",
        }
        return prompts.get(mode, prompts["precise"])

    # ═══════════════════════════════════════════
    # Agent loop
    # ═══════════════════════════════════════════
    async def agent(self, task: str, mode: str = "precise") -> str:
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

        try:
            cached = self.agent_cache.get(task, self.model)
            if cached:
                log_success(
                    f"💡 [BRAIN:agent] از کش | "
                    f"{time.monotonic() - t_start:.2f}s"
                )
                return cached

            self.memory.add("user", task)
            await self._emit(
                "command", {"task": task, "mode": mode}
            )

            thinking = await self._think(task, context="")
            selected_model = self._select_model(task)
            system_prompt = self._build_system_prompt(mode)
            context_history = await self.memory.get_context(task)
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

            step = 0
            final_answer = ""
            tools_used: list[str] = []
            fallback_count = 0
            max_fallback = 1

            while step < MAX_AGENT_STEPS:
                step += 1
                step_t0 = time.monotonic()

                if not self.client:
                    final_answer = self._offline_agent(task)
                    break

                try:
                    response = (
                        await self.client.chat.completions.create(
                            model=selected_model,
                            messages=messages,
                            tools=self.tools_definition,
                            temperature=0.1,
                            max_tokens=4096,
                        )
                    )
                except Exception as e:
                    log_error(
                        f"❌ [BRAIN:agent] API گام {step} | "
                        f"{type(e).__name__}: {e}"
                    )
                    if (
                        self._ai_priority != "ollama"
                        and selected_model != "deepseek-chat"
                        and fallback_count < max_fallback
                    ):
                        fallback_count += 1
                        log_warning(
                            f"⚠️ [BRAIN:agent] fallback به "
                            f"deepseek-chat "
                            f"({fallback_count}/{max_fallback})"
                        )
                        selected_model = "deepseek-chat"
                        continue
                    final_answer = f"❌ خطای API: {e}"
                    break

                msg = response.choices[0].message
                if not msg.tool_calls:
                    final_answer = (
                        msg.content or "✅ عملیات تکمیل شد."
                    )
                    messages.append(
                        {"role": "assistant", "content": final_answer}
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
                                    "arguments": tc.function.arguments,
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
                            if tc.function.arguments else {}
                        )
                    except json.JSONDecodeError as e:
                        log_warning(
                            f"⚠️ [BRAIN:agent] args JSON نامعتبر | "
                            f"{name} | {e}"
                        )
                        args = {}
                    log_info(
                        f"     🔧 [BRAIN:agent] {name} | "
                        f"args={str(args)[:LOG_PREVIEW_LEN]}"
                    )
                    result = await self._execute_tool(name, args)
                    log_info(
                        f"     📤 [BRAIN:agent] {name} → "
                        f"{result[:LOG_PREVIEW_LEN]}"
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result[:MAX_CONTENT_LENGTH],
                        }
                    )
                    if (
                        name in ("write_file", "edit_file")
                        and "✅" in result
                        and "خطا" not in result
                    ):
                        test_res = await self._auto_run_tests(
                            args.get("path", "")
                        )
                        if test_res:
                            messages.append(
                                {
                                    "role": "system",
                                    "content": (
                                        f"🧪 تست خودکار:\n{test_res}"
                                    ),
                                }
                            )

                step_elapsed_ms = (time.monotonic() - step_t0) * 1000
                if step_elapsed_ms > SLOW_STEP_MS:
                    log_warning(
                        f"🐌 [BRAIN:agent] گام {step} کند | "
                        f"{step_elapsed_ms:.0f}ms"
                    )

            final_answer = (
                final_answer
                or f"⚠️ سقف گام ({MAX_AGENT_STEPS})."
            )
            self.agent_cache.set(task, final_answer, self.model)
            self.memory.add("assistant", final_answer)

            success = (
                "❌" not in final_answer
                and "خطا" not in final_answer
            )
            self.learning_engine.record_interaction(
                user_input=task,
                response=final_answer,
                success=success,
                details={
                    "tools_used": tools_used,
                    "steps": step,
                },
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
                f"❌ [BRAIN:agent] {type(e).__name__}: {e}"
            )
            return f"❌ خطای غیرمنتظره: {e}"
        finally:
            self.busy = False

    def _offline_agent(self, task: str) -> str:
        t = task.strip().lower()
        for cmd, action in self.ALLOWED_DIRECT_COMMANDS.items():
            if cmd in t:
                if action == "greeting":
                    return "سلام! مغز آماده است."
                elif action == "status":
                    return self.get_status()
                elif action == "help":
                    return self.get_help()
        return (
            "🔍 حالت آفلاین.\n"
            "• /status — وضعیت\n"
            "• /help — راهنما"
        )

    async def _auto_run_tests(self, file_path: str) -> str:
        if not file_path:
            return ""
        from pathlib import Path

        p = Path(file_path)
        candidates = [
            p.parent / f"test_{p.name}",
            Path("tests") / f"test_{p.name}",
            Path("test") / f"test_{p.name}",
        ]
        for tf in candidates:
            if tf.exists():
                log_info(f"🧪 [BRAIN:auto-test] {tf}")
                return await self._run_tests(str(tf))
        return ""

    # ═══════════════════════════════════════════
    # Admin commands
    # ═══════════════════════════════════════════
    async def process_admin_command(self, text: str) -> str:
        self.stats["commands_processed"] += 1
        t0 = time.monotonic()
        log_info(
            f"👤 [BRAIN:cmd] #{self.stats['commands_processed']} | "
            f"{text[:LOG_PREVIEW_LEN]!r}"
        )

        text = PersianBrainHandler.fix(text)
        t = text.strip().lower()

        if t in ["/status", "وضعیت", "status"]:
            return self.get_status()
        if t in ["/help", "راهنما", "help", "/راهنما"]:
            return self.get_help()
        if t in ["/clear", "پاک", "clear"]:
            self.agent_cache.clear()
            self.memory.clear()
            return "🗑️ پاک شد."
        if t in ["/stats", "آمار", "stats"]:
            return self.get_stats()
        if t.startswith("/mode ") or t.startswith("حالت "):
            mode = t.split(maxsplit=1)[1]
            self.set_mode(mode)
            return f"✅ حالت '{mode}' فعال شد."

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
                        f"🎯 [BRAIN:cmd] intent | action={action} | "
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

        if self.client:
            return await self.agent(text)
        return self._offline_agent(text)

    async def _handle_action(
        self, action: str, args: dict, original_text: str
    ) -> str | None:
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
                result = await self._get_platform_status(platform)
            elif action == "report":
                result = await self._get_report()
            elif action == "crm":
                result = (
                    "📊 خروجی CRM توسط admin_handler ساخته می‌شود.\n"
                    "دستور «crm» یا «crm <platform>» "
                    "(مثلاً «crm ایتا») را در بله بفرست."
                )
            elif action == "logs":
                result = self._get_recent_logs()
            elif action == "lead_details":
                which = args.get("value", "last")
                result = await self._get_lead_details(which)
            elif action == "add_keyword":
                kw = args.get("value", "")
                result = (
                    f"🔧 افزودن کلیدواژه '{kw}' - "
                    f"از پنل admin/keywords استفاده کن."
                )
            elif action == "remove_keyword":
                kw = args.get("value", "")
                result = (
                    f"🔧 حذف کلیدواژه '{kw}' - "
                    f"از پنل admin/keywords استفاده کن."
                )
            elif action == "has_keyword":
                kw = args.get("value", "")
                result = await self._check_keyword(kw)
            elif action in ("ask", "feature_request"):
                result = await self.agent(original_text)
            else:
                log_warning(
                    f"⚠️ [BRAIN:action] ناشناخته | {action}"
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
    # Build from map / self upgrade
    # ═══════════════════════════════════════════
    async def build_from_map(
        self, task: str, max_retries: int = 3
    ) -> str:
        if not self.planner:
            return "❌ برنامه‌ریز نیست."
        if not self.client:
            return "❌ حالت آفلاین."
        try:
            result = await self.planner.execute_plan(task)
            return f"🏗️ **ساخت از نقشه:**\n{result}"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def self_upgrade(
        self, upgrade_request: str = "بهبود کد"
    ) -> str:
        if not self.client:
            return "❌ حالت آفلاین."
        try:
            prompt = f"خودت را بازبینی کن: {upgrade_request}"
            result = await self.agent(prompt, mode="analyze")
            return f"🔄 **خودارتقا:**\n{result}"
        except Exception as e:
            return f"❌ خطا: {e}"