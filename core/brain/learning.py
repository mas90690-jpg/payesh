"""یادگیری، برنامه‌ریزی، خودترمیمی، تست، بازبینی."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

from core.brain.constants import (
    BRAIN_DIR,
)
from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)


# ═══════════════════════════════════════════
# LearningEngine
# ═══════════════════════════════════════════
class LearningEngine:
    """یادگیری از تعاملات: patterns + suggest tool."""

    def __init__(self, agent):
        self.agent = agent
        self.learned_patterns: dict = {}
        self.successful_interactions: list[dict] = []
        self.failed_interactions: list[dict] = []
        self._load_learned_data()

    def _load_learned_data(self):
        learn_file = BRAIN_DIR / "learned_patterns.json"
        if not learn_file.exists():
            return
        try:
            data = json.loads(
                learn_file.read_text(encoding="utf-8")
            )
            self.learned_patterns = data.get("patterns", {})
            self.successful_interactions = data.get(
                "successful", []
            )
            self.failed_interactions = data.get("failed", [])
            log_info(
                f"📚 [BRAIN:learn] بارگذاری | "
                f"patterns={len(self.learned_patterns)} | "
                f"history={len(self.successful_interactions)}/{len(self.failed_interactions)}"
            )
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [BRAIN:learn] JSON نامعتبر | "
                f"line={e.lineno} — ریست"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:learn] بارگذاری ناموفق | "
                f"{type(e).__name__}: {e}"
            )

    def _save_learned_data(self):
        learn_file = BRAIN_DIR / "learned_patterns.json"
        try:
            tmp = learn_file.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "patterns": self.learned_patterns,
                        "successful": self.successful_interactions[-200:],
                        "failed": self.failed_interactions[-200:],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp.replace(learn_file)
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:learn] ذخیره ناموفق | "
                f"{type(e).__name__}: {e}"
            )

    def record_interaction(
        self,
        user_input: str,
        response: str,
        success: bool,
        details: dict = None,
    ):
        interaction = {
            "timestamp": datetime.now().isoformat(),
            "user_input": user_input,
            "response": response[:500],
            "success": success,
            "details": details or {},
            "tools_used": (
                details.get("tools_used", []) if details else []
            ),
        }
        if success:
            self.successful_interactions.append(interaction)
            self._extract_patterns(interaction)
        else:
            self.failed_interactions.append(interaction)
        self._save_learned_data()
        log_info(
            f"📝 [BRAIN:learn] ثبت | success={success}"
        )

    def _extract_patterns(self, interaction: dict):
        user_input = interaction.get("user_input", "").lower()
        tools = interaction.get("tools_used", [])
        for tool in tools:
            key = f"tool_{tool}"
            if key not in self.learned_patterns:
                self.learned_patterns[key] = {
                    "count": 0,
                    "keywords": [],
                }
            self.learned_patterns[key]["count"] += 1
            words = [w for w in user_input.split() if len(w) > 3]
            for word in words[:5]:
                if (
                    word
                    not in self.learned_patterns[key]["keywords"]
                ):
                    self.learned_patterns[key]["keywords"].append(
                        word
                    )

    def suggest_tool_for_task(self, task: str) -> str | None:
        task_lower = task.lower()
        best_tool = None
        best_score = 0
        for pattern_key, data in self.learned_patterns.items():
            if not pattern_key.startswith("tool_"):
                continue
            tool_name = pattern_key.replace("tool_", "")
            keywords = data.get("keywords", [])
            score = sum(
                1 for kw in keywords if kw in task_lower
            )
            if score > best_score:
                best_score = score
                best_tool = tool_name
        if best_score >= 2:
            return best_tool
        return None


# ═══════════════════════════════════════════
# TaskPlanner
# ═══════════════════════════════════════════
class TaskPlanner:
    """برنامه‌ریزی چندمرحله‌ای با AI."""

    def __init__(self, agent):
        self.agent = agent
        self.plans: dict[str, list[dict]] = {}
        self.filepath = BRAIN_DIR / "plans.json"
        self.load()

    def load(self):
        if not self.filepath.exists():
            return
        try:
            self.plans = json.loads(
                self.filepath.read_text(encoding="utf-8")
            )
            log_info(
                f"📋 [BRAIN:plan] {len(self.plans)} پلن"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:plan] load | "
                f"{type(e).__name__}: {e}"
            )
            self.plans = {}

    def save(self):
        try:
            tmp = self.filepath.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(
                    self.plans, ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
            tmp.replace(self.filepath)
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:plan] save | "
                f"{type(e).__name__}: {e}"
            )

    async def plan_task(self, task: str) -> list[dict]:
        if not self.agent.client:
            return [
                {
                    "step": 1,
                    "action": "execute",
                    "description": task,
                }
            ]
        prompt = f"""
You are a world-class project manager. Given the task: "{task}",
create a detailed step-by-step plan.
Each step must have: step, action, description, dependencies, time.
Return as JSON array.
"""
        try:
            t0 = time.monotonic()
            response = await self.agent.client.chat.completions.create(
                model=self.agent.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1500,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            plan_text = (
                response.choices[0].message.content.strip()
            )
            json_match = re.search(
                r"\[.*\]", plan_text, re.DOTALL
            )
            if json_match:
                plan = json.loads(json_match.group())
                self.plans[task] = plan
                self.save()
                log_success(
                    f"✅ [BRAIN:plan] steps={len(plan)} | "
                    f"{elapsed_ms:.0f}ms"
                )
                return plan
            return [
                {
                    "step": 1,
                    "action": "execute",
                    "description": task,
                }
            ]
        except Exception as e:
            log_error(
                f"❌ [BRAIN:plan] | {type(e).__name__}: {e}"
            )
            return [
                {
                    "step": 1,
                    "action": "execute",
                    "description": task,
                }
            ]

    async def execute_plan(self, task: str) -> str:
        plan = await self.plan_task(task)
        if not plan:
            return "❌ برنامه‌ای تولید نشد."
        results = {}
        for step in sorted(plan, key=lambda x: x.get("step", 0)):
            step_num = step.get("step", 0)
            deps = step.get("dependencies", [])
            for dep in deps:
                if dep not in results:
                    return (
                        f"❌ وابستگی {dep} برای مرحله "
                        f"{step_num} برآورده نشد."
                    )
            subtask = step.get("description", "")
            result = await self.agent.agent(
                subtask, mode="precise"
            )
            results[step_num] = result
        final = "\n\n".join(
            [f"مرحله {k}: {v}" for k, v in sorted(results.items())]
        )
        return f"📌 برنامه تکمیل شد:\n{final}"


# ═══════════════════════════════════════════
# SelfHealer
# ═══════════════════════════════════════════
class SelfHealer:
    """رفع خودکار باگ با AI."""

    def __init__(self, agent, max_attempts: int = 3):
        self.agent = agent
        self.max_attempts = max_attempts

    async def fix_bug(
        self, file_path: str, error_message: str
    ) -> str:
        for attempt in range(1, self.max_attempts + 1):
            content = await self.agent._read_file(file_path)
            if "⚠️" in content:
                return f"❌ فایل {file_path} قابل خواندن نیست."
            prompt = f"""
Fix the bug in this code:
{content}
Error: {error_message}
Provide only corrected code.
"""
            if not self.agent.client:
                return "❌ حالت آفلاین."
            try:
                response = (
                    await self.agent.client.chat.completions.create(
                        model="deepseek-reasoner",
                        messages=[
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.1,
                        max_tokens=2000,
                    )
                )
                corrected = (
                    response.choices[0].message.content.strip()
                )
                code_blocks = re.findall(
                    r"```python\n(.*?)```", corrected, re.DOTALL
                )
                new_code = (
                    code_blocks[0] if code_blocks else corrected
                )
                result = await self.agent._write_file(
                    file_path, new_code
                )
                if "✅" in result:
                    test_result = await self.agent._run_tests(
                        file_path
                    )
                    if (
                        "✅" in test_result
                        or "passed" in test_result.lower()
                    ):
                        return (
                            f"✅ باگ رفع شد در تلاش "
                            f"{attempt}.\n{test_result}"
                        )
                    error_message = test_result
                else:
                    return f"❌ خطا در نوشتن: {result}"
            except Exception as e:
                log_error(
                    f"❌ [BRAIN:heal] تلاش {attempt} | "
                    f"{type(e).__name__}: {e}"
                )
        return f"❌ پس از {self.max_attempts} تلاش نشد."


# ═══════════════════════════════════════════
# TestGenerator
# ═══════════════════════════════════════════
class TestGenerator:
    """تولید تست خودکار با AI."""

    def __init__(self, agent):
        self.agent = agent

    async def generate_tests(self, file_path: str) -> str:
        content = await self.agent._read_file(file_path)
        if "⚠️" in content:
            return f"❌ فایل {file_path} قابل خواندن نیست."
        prompt = f"""
Generate pytest tests for:
{content}
Return only test code.
"""
        if not self.agent.client:
            return "❌ حالت آفلاین."
        try:
            response = (
                await self.agent.client.chat.completions.create(
                    model="deepseek-reasoner",
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=2000,
                )
            )
            test_code = (
                response.choices[0].message.content.strip()
            )
            code_blocks = re.findall(
                r"```python\n(.*?)```", test_code, re.DOTALL
            )
            if code_blocks:
                test_code = code_blocks[0]
            test_file = (
                Path(file_path).parent
                / f"test_{Path(file_path).name}"
            )
            result = await self.agent._write_file(
                str(test_file), test_code
            )
            if "✅" in result:
                test_result = await self.agent._run_tests(
                    str(test_file)
                )
                return (
                    f"✅ تست‌ها در {test_file} ذخیره شدند.\n"
                    f"نتیجه: {test_result}"
                )
            return f"❌ خطا در ذخیره: {result}"
        except Exception as e:
            log_error(
                f"❌ [BRAIN:test] {type(e).__name__}: {e}"
            )
            return f"❌ خطا در تولید تست: {e}"


# ═══════════════════════════════════════════
# WebSearch
# ═══════════════════════════════════════════
class WebSearch:
    """جستجوی وب با DuckDuckGo."""

    def __init__(self):
        self.session = None
        self._init_session()

    def _init_session(self):
        try:
            import requests
            self.session = requests.Session()
            self.session.headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; "
                        "Win64; x64) AppleWebKit/537.36"
                    )
                }
            )
        except ImportError:
            self.session = None

    async def search(
        self, query: str, num_results: int = 5
    ) -> str:
        if not self.session:
            return "❌ requests نصب نیست."
        try:
            import asyncio as _a
            url = "https://html.duckduckgo.com/html/"
            params = {"q": query}
            resp = await _a.to_thread(
                self.session.get, url, params=params, timeout=10
            )
            resp.raise_for_status()
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(
                    resp.text, "html.parser"
                )
                results = []
                for result in soup.select(".result"):
                    title_elem = result.select_one(
                        ".result__a"
                    )
                    snippet_elem = result.select_one(
                        ".result__snippet"
                    )
                    if title_elem and snippet_elem:
                        title = title_elem.get_text(strip=True)
                        snippet = snippet_elem.get_text(
                            strip=True
                        )
                        results.append(
                            f"🔹 {title}\n   {snippet[:200]}..."
                        )
                        if len(results) >= num_results:
                            break
                if results:
                    return "\n\n".join(results)
                return "🔍 نتیجه‌ای یافت نشد."
            except ImportError:
                return "❌ BeautifulSoup نصب نیست."
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:web] {type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"


# ═══════════════════════════════════════════
# CodeReviewer
# ═══════════════════════════════════════════
class CodeReviewer:
    """بازبینی کد با AI."""

    def __init__(self, agent):
        self.agent = agent

    async def review(self, file_path: str) -> str:
        content = await self.agent._read_file(file_path)
        if "⚠️" in content:
            return f"❌ فایل {file_path} قابل خواندن نیست."
        prompt = f"""
Review this code (bugs, security, smells, perf):
{content}
"""
        if not self.agent.client:
            return "❌ حالت آفلاین."
        try:
            response = (
                await self.agent.client.chat.completions.create(
                    model="deepseek-reasoner",
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=2000,
                )
            )
            return (
                f"📋 بازبینی کد {file_path}:\n"
                f"{response.choices[0].message.content.strip()}"
            )
        except Exception as e:
            log_error(
                f"❌ [BRAIN:review] {type(e).__name__}: {e}"
            )
            return f"❌ خطا در بازبینی: {e}"
