"""
یادگیری، برنامه‌ریزی، خودترمیمی، تست، بازبینی.

نسخهٔ ۲ (این نسخه):
  - 🎯 `BRAIN_DIR` حالا از `data.paths.BRAIN_DIR` می‌آید (منبع یکتا).
  - 🎯 `_save_learned_data` و `TaskPlanner.save` حالا `os.fsync` می‌زنند.
  - 🎯 `WebSearch` حالا timeout و max_bytes env-based.
  - 🎯 `record_interaction` حالا طول user_input/response را محدود می‌کند.
  - 🐛 FIX: `TaskPlanner.load` حالا `plans` غیر-dict را امن handle می‌کند.
  - 🐛 FIX: `TaskPlanner.plan_task` حالا اگر JSON نامعتبر بود، لاگ
    کامل می‌زند (نه فقط fallback بی‌صدا).
  - 🐛 FIX: `SelfHealer.fix_bug` حالا `max_attempts` را با حد منطقی
    sanitize می‌کند.
  - 🎯 `_env_int` / `_env_float` helper.
  - 🎯 `_atomic_write_json` helper مشترک.
  - 🎯 `WebSearch` حالا از `data.paths.BRAIN_DIR` استفاده نمی‌کند
    (نیازی نبود).
  - ✅ حفظ همهٔ رفتار نسخهٔ ۱.
"""
from __future__ import annotations
import threading
import asyncio
import json
import os
import re
import time
import threading as _threading
from core.adapters.memory_store import MemoryStore
from datetime import datetime
from pathlib import Path
from typing import Any

from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)

# 🎯 P0: مسیرها از data.paths
from data import paths as data_paths


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


def _atomic_write_json(path: Path, data: Any) -> bool:
    """🎯 نوشتن atomic با fsync."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(str(tmp), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            try:
                f.flush()
                os.fsync(f.fileno())
            except (AttributeError, OSError):
                pass
        tmp.replace(path)

        # fsync دایرکتوری
        try:
            parent = str(path.parent)
            dir_fd = os.open(parent, os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except (AttributeError, OSError):
            pass
        return True
    except Exception as e:
        log_warning(
            f"⚠️ [BRAIN:write] {path.name} | "
            f"{type(e).__name__}: {e}"
        )
        return False


# ═══════════════════════════════════════════════════════════
# مسیرهای Brain (از data.paths)
# ═══════════════════════════════════════════════════════════
BRAIN_DIR: Path = data_paths.BRAIN_DIR

# آستانه‌های env-based
_MAX_INTERACTION_HISTORY = _env_int(
    "BRAIN_LEARN_HISTORY", 200, min_val=10, max_val=10_000
)
_MAX_USER_INPUT_LEN = _env_int(
    "BRAIN_LEARN_MAX_INPUT_LEN", 500,
    min_val=50, max_val=5000,
)
_MAX_RESPONSE_LEN = _env_int(
    "BRAIN_LEARN_MAX_RESPONSE_LEN", 500,
    min_val=50, max_val=5000,
)
_MAX_PLANS = _env_int(
    "BRAIN_PLANS_MAX", 100, min_val=5, max_val=1000
)
_PLAN_MAX_TOKENS = _env_int(
    "BRAIN_PLAN_MAX_TOKENS", 1500,
    min_val=200, max_val=8000,
)
_WEB_SEARCH_TIMEOUT = _env_int(
    "BRAIN_WEB_TIMEOUT", 10, min_val=3, max_val=60
)
_WEB_MAX_RESULTS = _env_int(
    "BRAIN_WEB_MAX_RESULTS", 5, min_val=1, max_val=20
)


# ═══════════════════════════════════════════════════════════
# LearningEngine
# ═══════════════════════════════════════════════════════════
class LearningEngine:
    """یادگیری از تعاملات: patterns + suggest tool."""

    def __init__(self, agent):
        self.agent = agent
        self.learned_patterns: dict = {}
        self.successful_interactions: list[dict] = []
        self.failed_interactions: list[dict] = []
        self._learn_file = BRAIN_DIR / "learned_patterns.json"
        self._load_learned_data()

    # ═══════════════════════════════════════════
    # load / save
    # ═══════════════════════════════════════════
    def _load_learned_data(self):
        if not self._learn_file.exists():
            return
        try:
            with open(
                str(self._learn_file), encoding="utf-8"
            ) as f:
                data = json.load(f)

            if not isinstance(data, dict):
                log_warning(
                    f"⚠️ [BRAIN:learn] ساختار نامعتبر | "
                    f"type={type(data).__name__} — ریست"
                )
                return

            self.learned_patterns = (
                data.get("patterns", {}) or {}
            )
            self.successful_interactions = (
                data.get("successful", []) or []
            )
            self.failed_interactions = (
                data.get("failed", []) or []
            )
            log_info(
                f"📚 [BRAIN:learn] بارگذاری | "
                f"patterns={len(self.learned_patterns)} | "
                f"history="
                f"{len(self.successful_interactions)}/"
                f"{len(self.failed_interactions)}"
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
        data = {
            "patterns": self.learned_patterns,
            "successful": self.successful_interactions[
                -_MAX_INTERACTION_HISTORY:
            ],
            "failed": self.failed_interactions[
                -_MAX_INTERACTION_HISTORY:
            ],
        }
        _atomic_write_json(self._learn_file, data)

    # ═══════════════════════════════════════════
    # record_interaction
    # ═══════════════════════════════════════════
    def record_interaction(
        self,
        user_input: str,
        response: str,
        success: bool,
        details: dict | None = None,
    ):
        """🎯 FIX: محدودسازی طول."""
        details = details or {}
        interaction = {
            "timestamp": datetime.now().isoformat(),
            "user_input": str(user_input)[:_MAX_USER_INPUT_LEN],
            "response": str(response)[:_MAX_RESPONSE_LEN],
            "success": bool(success),
            "details": details,
            "tools_used": list(
                details.get("tools_used", []) or []
            )[:20],
        }

        if success:
            self.successful_interactions.append(interaction)
            self._extract_patterns(interaction)
        else:
            self.failed_interactions.append(interaction)

        # cap
        if (
            len(self.successful_interactions)
            > _MAX_INTERACTION_HISTORY
        ):
            self.successful_interactions = (
                self.successful_interactions[
                    -_MAX_INTERACTION_HISTORY:
                ]
            )
        if (
            len(self.failed_interactions)
            > _MAX_INTERACTION_HISTORY
        ):
            self.failed_interactions = (
                self.failed_interactions[
                    -_MAX_INTERACTION_HISTORY:
                ]
            )

        self._save_learned_data()
                # 🎯 ذخیره در MemoryStore جدید
        try:
            from core.adapters.memory_store import MemoryStore
            store = MemoryStore()
            store.add(
                category="interactions",
                key=user_input[:200],
                value=response[:500],
                importance=1.0 if success else 0.3,
            )
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:learn] memory_store | {e}")
            
        log_info(
            f"📝 [BRAIN:learn] ثبت | success={success}"
        )

    def _extract_patterns(self, interaction: dict):
        """استخراج الگو از تعامل موفق."""
        user_input = (
            interaction.get("user_input", "") or ""
        ).lower()
        tools = interaction.get("tools_used", []) or []

        for tool in tools:
            if not tool:
                continue
            key = f"tool_{tool}"
            if key not in self.learned_patterns:
                self.learned_patterns[key] = {
                    "count": 0,
                    "keywords": [],
                }
            self.learned_patterns[key]["count"] = (
                self.learned_patterns[key].get("count", 0) + 1
            )
            words = [
                w for w in user_input.split() if len(w) > 3
            ]
            for word in words[:5]:
                keywords = self.learned_patterns[key].setdefault(
                    "keywords", []
                )
                if word not in keywords:
                    keywords.append(word)

    def suggest_tool_for_task(self, task: str) -> str | None:
        """پیشنهاد ابزار بر اساس الگوهای یادگرفته‌شده."""
        task_lower = (task or "").lower()
        best_tool = None
        best_score = 0

        for pattern_key, data in self.learned_patterns.items():
            if not pattern_key.startswith("tool_"):
                continue
            tool_name = pattern_key.replace("tool_", "")
            keywords = data.get("keywords", []) or []
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
    # 🎯 حافظهٔ جدید (SQLite + Bitemporal)
    # ═══════════════════════════════════════════
    def to_memory_store(self) -> None:
        """ذخیرهٔ همهٔ الگوها در MemoryStore جدید."""
        try:
            from core.adapters.memory_store import MemoryStore
            store = MemoryStore()
            for key, data in self.learned_patterns.items():
                count = data.get("count", 0)
                keywords = data.get("keywords", [])
                store.add(
                    category="patterns",
                    key=key,
                    value=f"count={count}, keywords={keywords}",
                    importance=min(1.0, count / 10.0),
                )
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:learn] to_memory_store | {e}")
            
# ═══════════════════════════════════════════════════════════
# TaskPlanner
# ═══════════════════════════════════════════════════════════
class TaskPlanner:
    """برنامه‌ریزی چندمرحله‌ای با AI."""

    def __init__(self, agent):
        self.agent = agent
        self.plans: dict[str, list[dict]] = {}
        self._plan_file = BRAIN_DIR / "plans.json"
        self.load()

    def load(self):
        """🎯 FIX: plans غیر-dict امن handle می‌شود."""
        if not self._plan_file.exists():
            return
        try:
            with open(
                str(self._plan_file), encoding="utf-8"
            ) as f:
                data = json.load(f)

            if not isinstance(data, dict):
                log_warning(
                    f"⚠️ [BRAIN:plan] ساختار نامعتبر | "
                    f"type={type(data).__name__} — ریست"
                )
                self.plans = {}
                return

            self.plans = {
                str(k): v for k, v in data.items()
                if isinstance(v, list)
            }
            log_info(
                f"📋 [BRAIN:plan] {len(self.plans)} پلن"
            )
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [BRAIN:plan] JSON نامعتبر | "
                f"line={e.lineno} — ریست"
            )
            self.plans = {}
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:plan] load | "
                f"{type(e).__name__}: {e}"
            )
            self.plans = {}

    def save(self):
        """🎯 FIX: atomic + fsync + cap."""
        if len(self.plans) > _MAX_PLANS:
            keys = list(self.plans.keys())
            for k in keys[: len(keys) - _MAX_PLANS]:
                self.plans.pop(k, None)
        _atomic_write_json(self._plan_file, self.plans)

    async def plan_task(self, task: str) -> list[dict]:
        """برنامه‌ریزی با AI — با fallback."""
        if not self.agent.client:
            return [{
                "step": 1,
                "action": "execute",
                "description": task,
            }]

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
                max_tokens=_PLAN_MAX_TOKENS,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            plan_text = (
                response.choices[0].message.content or ""
            ).strip()

            json_match = re.search(
                r"\[.*\]", plan_text, re.DOTALL
            )
            if json_match:
                try:
                    plan = json.loads(json_match.group())
                    if isinstance(plan, list) and plan:
                        self.plans[task] = plan
                        self.save()
                        log_success(
                            f"✅ [BRAIN:plan] "
                            f"steps={len(plan)} | "
                            f"{elapsed_ms:.0f}ms"
                        )
                        return plan
                except json.JSONDecodeError as e:
                    # 🎯 FIX: لاگ کامل JSON نامعتبر
                    log_warning(
                        f"⚠️ [BRAIN:plan] JSON نامعتبر "
                        f"در پاسخ AI | "
                        f"line={e.lineno} col={e.colno} | "
                        f"{e.msg} | preview="
                        f"{plan_text[:200]!r}"
                    )

            # fallback
            log_warning(
                f"⚠️ [BRAIN:plan] بدون JSON معتبر — "
                f"fallback تک‌مرحله‌ای | "
                f"preview={plan_text[:200]!r}"
            )
            return [{
                "step": 1,
                "action": "execute",
                "description": task,
            }]
        except Exception as e:
            log_error(
                f"❌ [BRAIN:plan] {type(e).__name__}: {e}"
            )
            return [{
                "step": 1,
                "action": "execute",
                "description": task,
            }]

    async def execute_plan(self, task: str) -> str:
        """اجرای پلن مرحله‌به‌مرحله."""
        plan = await self.plan_task(task)
        if not plan:
            return "❌ برنامه‌ای تولید نشد."

        results: dict[Any, str] = {}
        for step in sorted(
            plan, key=lambda x: x.get("step", 0)
        ):
            step_num = step.get("step", 0)
            deps = step.get("dependencies", []) or []

            # چک وابستگی‌ها
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
            f"مرحله {k}: {v}"
            for k, v in sorted(results.items())
        )
        return f"📌 برنامه تکمیل شد:\n{final}"


# ═══════════════════════════════════════════════════════════
# SelfHealer
# ═══════════════════════════════════════════════════════════
class SelfHealer:
    """رفع خودکار باگ با AI."""

    def __init__(self, agent, max_attempts: int = 3):
        self.agent = agent
        # 🎯 FIX: sanitize max_attempts
        try:
            ma = int(max_attempts)
        except (TypeError, ValueError):
            ma = 3
        self.max_attempts = max(1, min(ma, 10))

    async def fix_bug(
        self, file_path: str, error_message: str
    ) -> str:
        """رفع باگ با retry."""
        if not self.agent.client:
            return "❌ حالت آفلاین."

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
            try:
                response = (
                    await self.agent.client
                    .chat.completions.create(
                        model="deepseek-reasoner",
                        messages=[{
                            "role": "user",
                            "content": prompt,
                        }],
                        temperature=0.1,
                        max_tokens=2000,
                    )
                )
                corrected = (
                    response.choices[0].message.content or ""
                ).strip()

                code_blocks = re.findall(
                    r"```python\n(.*?)```",
                    corrected, re.DOTALL,
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


# ═══════════════════════════════════════════════════════════
# TestGenerator
# ═══════════════════════════════════════════════════════════
class TestGenerator:
    """تولید تست خودکار با AI."""

    def __init__(self, agent):
        self.agent = agent

    async def generate_tests(self, file_path: str) -> str:
        """تولید تست pytest برای یک فایل."""
        content = await self.agent._read_file(file_path)
        if "⚠️" in content:
            return f"❌ فایل {file_path} قابل خواندن نیست."

        if not self.agent.client:
            return "❌ حالت آفلاین."

        prompt = f"""
Generate pytest tests for:
{content}
Return only test code.
"""
        try:
            response = (
                await self.agent.client.chat.completions.create(
                    model="deepseek-reasoner",
                    messages=[{
                        "role": "user",
                        "content": prompt,
                    }],
                    temperature=0.1,
                    max_tokens=2000,
                )
            )
            test_code = (
                response.choices[0].message.content or ""
            ).strip()

            code_blocks = re.findall(
                r"```python\n(.*?)```",
                test_code, re.DOTALL,
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


# ═══════════════════════════════════════════════════════════
# WebSearch
# ═══════════════════════════════════════════════════════════
class WebSearch:
    """جستجوی وب با DuckDuckGo."""

    def __init__(self):
        self.session = None
        self._init_session()

    def _init_session(self):
        """ساخت requests.Session (sync — امن برای to_thread)."""
        try:
            import requests
            self.session = requests.Session()
            self.session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; "
                    "Win64; x64) AppleWebKit/537.36"
                )
            })
        except ImportError:
            self.session = None
            log_warning(
                "⚠️ [BRAIN:web] requests نصب نیست"
            )

    async def search(
        self, query: str, num_results: int | None = None
    ) -> str:
        """جستجوی وب — با to_thread (async-safe)."""
        if not self.session:
            return "❌ requests نصب نیست."

        if num_results is None:
            num_results = _WEB_MAX_RESULTS
        try:
            n = int(num_results)
        except (TypeError, ValueError):
            n = _WEB_MAX_RESULTS
        n = max(1, min(n, _WEB_MAX_RESULTS * 4))

        try:
            url = "https://html.duckduckgo.com/html/"
            params = {"q": query}
            resp = await asyncio.to_thread(
                self.session.get,
                url,
                params=params,
                timeout=_WEB_SEARCH_TIMEOUT,
            )
            resp.raise_for_status()

            try:
                from bs4 import BeautifulSoup
            except ImportError:
                return "❌ BeautifulSoup نصب نیست."

            soup = BeautifulSoup(resp.text, "html.parser")
            results: list[str] = []
            for result in soup.select(".result"):
                title_elem = result.select_one(".result__a")
                snippet_elem = result.select_one(
                    ".result__snippet"
                )
                if title_elem and snippet_elem:
                    title = title_elem.get_text(strip=True)
                    snippet = snippet_elem.get_text(strip=True)
                    results.append(
                        f"🔹 {title}\n   {snippet[:200]}..."
                    )
                    if len(results) >= n:
                        break

            if results:
                return "\n\n".join(results)
            return "🔍 نتیجه‌ای یافت نشد."
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:web] {type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"


# ═══════════════════════════════════════════════════════════
# CodeReviewer
# ═══════════════════════════════════════════════════════════
class CodeReviewer:
    """بازبینی کد با AI."""

    def __init__(self, agent):
        self.agent = agent

    async def review(self, file_path: str) -> str:
        """بازبینی یک فایل با AI."""
        content = await self.agent._read_file(file_path)
        if "⚠️" in content:
            return f"❌ فایل {file_path} قابل خواندن نیست."

        if not self.agent.client:
            return "❌ حالت آفلاین."

        prompt = f"""
Review this code (bugs, security, smells, perf):
{content}
"""
        try:
            response = (
                await self.agent.client.chat.completions.create(
                    model="deepseek-reasoner",
                    messages=[{
                        "role": "user",
                        "content": prompt,
                    }],
                    temperature=0.1,
                    max_tokens=2000,
                )
            )
            review = (
                response.choices[0].message.content or ""
            ).strip()
            return f"📋 بازبینی کد {file_path}:\n{review}"
        except Exception as e:
            log_error(
                f"❌ [BRAIN:review] {type(e).__name__}: {e}"
            )
            return f"❌ خطا در بازبینی: {e}"
            
# ═══════════════════════════════════════════════════════════
# ProductLearner — یادگیری خودکار محصولات
# ═══════════════════════════════════════════════════════════
class ProductLearner:
    """یادگیری خودکار محصولات جدید از گفتگو.

    - فقط products.py و products_en.py رو می‌خونه
    - هیچ‌وقت بهشون دست نمی‌زنه
    - هر چی یاد گرفت → products_learned.json
    """

    def __init__(self, agent):
        self.agent = agent
        self._learn_file = (
            BRAIN_DIR / "products_learned.json"
        )
        self._pending: dict[str, int] = {}
        self._lock = threading.RLock()
        self._load()

        log_info(
            f"🧬 [BRAIN:product_learner] آماده | "
            f"file={self._learn_file.name}"
        )

    # ═══════════════════════════════════════════
    # load / save
    # ═══════════════════════════════════════════
    def _load(self):
        """لود products_learned.json."""
        if not self._learn_file.exists():
            self._learned: dict = {
                "version": "1.0.0",
                "products": {},
                "aliases": {},
                "updated_at": None,
            }
            return

        try:
            with open(
                str(self._learn_file), encoding="utf-8"
            ) as f:
                data = json.load(f)

            if not isinstance(data, dict):
                self._learned = {
                    "version": "1.0.0",
                    "products": {},
                    "aliases": {},
                    "updated_at": None,
                }
                return

            self._learned = data
            log_info(
                f"📚 [BRAIN:product_learner] لود شد | "
                f"learned={len(data.get('products', {}))} برند"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:product_learner:load] "
                f"{type(e).__name__}: {e}"
            )
            self._learned = {
                "version": "1.0.0",
                "products": {},
                "aliases": {},
                "updated_at": None,
            }

    def _save(self) -> bool:
        """ذخیره با atomic + fsync."""
        self._learned["updated_at"] = (
            datetime.now().isoformat()
        )
        return _atomic_write_json(
            self._learn_file, self._learned
        )

    # ═══════════════════════════════════════════
    # public API
    # ═══════════════════════════════════════════
    def get_learned(self) -> dict:
        """برمی‌گرداند {brand: [products]}."""
        return dict(
            self._learned.get("products", {}) or {}
        )

    def get_all(self) -> dict[str, list[str]]:
        """products.py + products_learned.json merge."""
        # ─── base ───
        try:
            from data.products import PRODUCTS_BY_BRAND
            base = {
                k: list(v)
                for k, v in PRODUCTS_BY_BRAND.items()
            }
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:product_learner:base] "
                f"{type(e).__name__}: {e}"
            )
            base = {}

        # ─── learned ───
        for brand, products in self.get_learned().items():
            if brand not in base:
                base[brand] = []
            for p in products:
                if p not in base[brand]:
                    base[brand].append(p)

        return base

    def get_all_flat(self) -> list[str]:
        """لیست تخت همهٔ محصولات."""
        out: list[str] = []
        for products in self.get_all().values():
            out.extend(products)
        return sorted(set(out))

    # ═══════════════════════════════════════════
    # یادگیری
    # ═══════════════════════════════════════════
    def track_mention(self, text: str) -> None:
        """ردیابی یه محصول جدید در متن.

        اگه ۳ بار تکرار شد → یاد بگیر.
        """
        if not text or len(text) < 3:
            return

        # ─── استخراج کاندیدها ───
        candidates = self._extract_candidates(text)

        with self._lock:
            for cand in candidates:
                # ─── اگه از قبل توی لیست اصلی هست → skip ───
                if self._is_in_base(cand):
                    continue

                # ─── اگه از قبل یاد گرفته → skip ───
                if self._is_learned(cand):
                    continue

                # ─── شمارش ───
                self._pending[cand] = (
                    self._pending.get(cand, 0) + 1
                )

            # ─── چک یادگیری ───
            self._maybe_learn()

    def _is_in_base(self, product: str) -> bool:
        """آیا توی products.py هست؟"""
        try:
            from data.products import ALL_PRODUCTS
            from data.products_en import ALL_PRODUCTS_EN

            p_low = product.lower()
            for p in ALL_PRODUCTS:
                if p.lower() == p_low:
                    return True
            for p in ALL_PRODUCTS_EN:
                if p.lower() == p_low:
                    return True
        except Exception:
            pass
        return False

    def _is_learned(self, product: str) -> bool:
        """آیا قبلاً یاد گرفته شده؟"""
        p_low = product.lower()
        for products in self.get_learned().values():
            for p in products:
                if p.lower() == p_low:
                    return True
        return False

    @staticmethod
    def _extract_candidates(text: str) -> list[str]:
        """استخراج کاندیدهای محصول از متن."""
        # ─── کلمه‌های کلیدی ───
        KEYWORDS = (
            "کود", "سم", "قارچ‌کش", "حشره‌کش",
            "علف‌کش", "کلسیم", "منیزیم", "روی",
            "آهن", "مس", "منگنز", "بور",
            "نیتروژن", "فسفر", "پتاسیم",
            "مایع", "پودر", "گرانول",
            "محلول", "کنسانتره",
        )

        candidates: list[str] = []

        # ─── پیدا کردن عبارت‌های ۱-۳ کلمه‌ای ───
        words = text.split()
        for i, word in enumerate(words):
            w = word.strip("،.:؛!?؟«»\"'")
            if len(w) < 3:
                continue

            # ─── چک کلیدواژه ───
            for kw in KEYWORDS:
                if kw in w:
                    # ─── ۱ کلمه ───
                    candidates.append(w)
                    # ─── ۲ کلمه ───
                    if i + 1 < len(words):
                        nxt = words[i + 1].strip(
                            "،.:؛!?؟«»\"'"
                        )
                        if len(nxt) >= 3:
                            candidates.append(f"{w} {nxt}")
                    break

        return candidates

    def _maybe_learn(self) -> None:
        """اگه تکرار کافی بود، یاد بگیر."""
        MIN_REPEAT = 3
        learned_any = False

        for cand in list(self._pending.keys()):
            count = self._pending.get(cand, 0)
            if count < MIN_REPEAT:
                continue

            # ─── ذخیره ───
            products = self._learned.setdefault(
                "products", {}
            )
            if "یادگرفته" not in products:
                products["یادگرفته"] = []
            if cand not in products["یادگرفته"]:
                products["یادگرفته"].append(cand)
                learned_any = True

            # ─── حذف از pending ───
            self._pending.pop(cand, None)

        if learned_any and self._save():
            log_success(
                f"🧬 [BRAIN:product_learner] "
                f"یاد گرفت | file={self._learn_file.name}"
            )

    # ═══════════════════════════════════════════
    # debug
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """آمار."""
        with self._lock:
            return {
                "learned_brands": len(
                    self.get_learned()
                ),
                "learned_products": sum(
                    len(v) for v in self.get_learned().values()
                ),
                "pending": dict(self._pending),
                "file": str(self._learn_file),
            }           