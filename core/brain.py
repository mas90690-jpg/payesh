#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/brain.py — مغز هوشمند امن ربات پایش سرنخ
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
ترکیبی از:
- Agent پیشرفته از bot.py (۳۲ ابزار، Extended Thinking، Self‑Healing، ...)
- زیرساخت امن پروژه (event_bus, database, config, logger, ai_assistant)
- یکپارچگی کامل با admin_handler از طریق process_admin_command
- امنیت چندلایه: whitelist, sandbox, safe_path, محدودیت نوشتن
- پشتیبانی از حافظه API (با پرچم full_access برای دسترسی کامل)
"""

import os
import sys
import json
import time
import asyncio
import threading
import subprocess
import shutil
import hashlib
import re
import ast
import glob
import zipfile
import sqlite3
import tempfile
import difflib
import locale
import platform
import random
import shlex
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple, Callable, Union

# NOTE: `from openai import OpenAI` is deliberately NOT at module level.
# In this environment importing `openai` blocks (network resolution during import),
# which hung the entire bot (AI never answered on Bale). It is imported lazily
# inside _init_client(), only when an API key is actually configured.
# from openai import OpenAI

# ─── تنظیمات پروژه ───
try:
    from config import (
        BASE_DIR as PROJECT_BASE,
        STORAGE_DIR,
        DB_DIR,
        BACKUPS_DIR,
        LOGS_DIR,
        AI,
        AI_ENABLED,
        PLUGINS_CONFIG,
    )
except ImportError:
    # fallback برای زمانی که config در دسترس نیست
    PROJECT_BASE = Path(__file__).parent.parent.resolve()
    STORAGE_DIR = PROJECT_BASE / "storage"
    DB_DIR = STORAGE_DIR / "database"
    BACKUPS_DIR = STORAGE_DIR / "backups"
    LOGS_DIR = STORAGE_DIR / "logs"
    AI = {
        "enabled": True,
        "model": "deepseek-chat",
        "url": "https://api.deepseek.com/v1",
    }
    AI_ENABLED = True
    PLUGINS_CONFIG = {}

from core.logger import log_info, log_success, log_warning, log_error, get_logger
from core.event_bus import EventBus, Event, EventPriority

# ─── ثابت‌های مغز ───
BRAIN_DIR = Path(STORAGE_DIR) / "brain"
MEMORY_DIR = BRAIN_DIR / "memory"
CACHE_DIR = BRAIN_DIR / "cache"
BACKUP_DIR = BRAIN_DIR / "backups"
HOOKS_DIR = BRAIN_DIR / "hooks"
for d in [BRAIN_DIR, MEMORY_DIR, CACHE_DIR, BACKUP_DIR, HOOKS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_CONTENT_LENGTH = 100_000
MAX_COMMAND_TIMEOUT = 300
MAX_AGENT_STEPS = 30
MAX_CONTEXT_TOKENS = 16384
CACHE_TTL_DAYS = 14
DEFAULT_MODEL = "deepseek-chat"

# ─── کلاس‌های کمکی ───


class PersianBrainHandler:
    """مدیریت حرفه‌ای متن فارسی"""

    PERSIAN_RANGES = [
        (0x0600, 0x06FF),
        (0xFB50, 0xFDFF),
        (0xFE70, 0xFEFF),
        (0x0750, 0x077F),
        (0x08A0, 0x08FF),
    ]
    REVERSE_MAP = {
        "ي": "ی",
        "ك": "ک",
        "ة": "ه",
        "ۀ": "ه",
        "ھ": "ه",
        "ے": "ی",
        "ۓ": "ی",
    }

    @staticmethod
    def setup_environment():
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
                sys.stderr.reconfigure(encoding="utf-8")
            try:
                locale.setlocale(locale.LC_ALL, "fa_IR.UTF-8")
            except:
                try:
                    locale.setlocale(locale.LC_ALL, "Persian_Iran.1256")
                except:
                    locale.setlocale(locale.LC_ALL, "")
            if platform.system() == "Windows":
                os.system("color")
                os.system("chcp 65001 > nul 2>&1")
            os.environ["PYTHONIOENCODING"] = "utf-8"
            os.environ["LANG"] = "fa_IR.UTF-8"
        except Exception as e:
            log_warning(f"تنظیم محیط فارسی: {e}")

    @staticmethod
    def is_persian(text: str) -> bool:
        if not text:
            return False
        pc = tc = 0
        for ch in text:
            if ch.isspace() or ch.isdigit() or ch in "!?.,;:()[]{}":
                continue
            tc += 1
            code = ord(ch)
            if any(s <= code <= e for s, e in PersianBrainHandler.PERSIAN_RANGES):
                pc += 1
        return tc > 0 and (pc / tc) > 0.3

    @staticmethod
    def fix(text: str) -> str:
        if not text:
            return text
        for wrong, correct in PersianBrainHandler.REVERSE_MAP.items():
            text = text.replace(wrong, correct)
        text = (
            text.replace("\u200e", "")
            .replace("\u200f", "")
            .replace("\u202b", "")
            .replace("\u202e", "")
        )
        return text


def human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def safe_path(path_str: str, base: Optional[Path] = None) -> Path:
    base = base or Path(PROJECT_BASE)
    full = (base / path_str).resolve()
    if not str(full).startswith(str(base)):
        raise ValueError("⛔ دسترسی به خارج از پروژه ممنوع!")
    return full


# ─── کش هوشمند ───


class SmartCache:
    def __init__(self, name: str, ttl_days: int = CACHE_TTL_DAYS, max_size: int = 5000):
        self.filepath = CACHE_DIR / f"{name}.json"
        self.ttl = timedelta(days=ttl_days)
        self.max_size = max_size
        self.cache: Dict[str, Dict] = {}
        self._lock = threading.Lock()
        self.load()

    def _key(self, text: str, model: str = "") -> str:
        return hashlib.sha256(f"{text}|{model}".encode()).hexdigest()

    def get(self, text: str, model: str = "") -> Optional[str]:
        key = self._key(text, model)
        with self._lock:
            entry = self.cache.get(key)
        if (
            entry
            and (datetime.now() - datetime.fromtimestamp(entry["timestamp"])) < self.ttl
        ):
            return entry["answer"]
        return None

    def set(self, text: str, answer: str, model: str = ""):
        key = self._key(text, model)
        with self._lock:
            self.cache[key] = {"answer": answer, "timestamp": time.time()}
            if len(self.cache) > self.max_size:
                sorted_items = sorted(
                    self.cache.items(), key=lambda x: x[1]["timestamp"]
                )
                self.cache = dict(sorted_items[-int(self.max_size * 0.8) :])
        self.save()

    def load(self):
        if self.filepath.exists():
            try:
                with self._lock:
                    self.cache = json.loads(self.filepath.read_text(encoding="utf-8"))
            except:
                self.cache = {}

    def save(self):
        try:
            with self._lock:
                data = json.dumps(self.cache, ensure_ascii=False, indent=2)
            self.filepath.write_text(data, encoding="utf-8")
        except Exception as e:
            log_warning(f"Cache save failed: {e}")

    def clear(self):
        with self._lock:
            self.cache = {}
        self.save()

    def stats(self) -> Dict:
        with self._lock:
            return {"entries": len(self.cache), "file": str(self.filepath)}


# ─── حافظه هوشمند (با ChromaDB یا API) ───


class APIMemory:
    """
    پیاده‌سازی حافظه از طریق API (جایگزین ChromaDB)
    """

    def __init__(self, api_url: str, api_key: str = "", timeout: int = 30):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._session = None
        self._initialized = False

    async def _ensure_session(self):
        if self._session is None:
            import aiohttp

            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._session = aiohttp.ClientSession(
                headers=headers, timeout=aiohttp.ClientTimeout(total=self.timeout)
            )

    async def _post(self, endpoint: str, data: dict) -> dict:
        await self._ensure_session()
        try:
            async with self._session.post(
                f"{self.api_url}/{endpoint}", json=data
            ) as resp:
                if resp.status >= 400:
                    log_warning(
                        f"APIMemory API error {resp.status}: {await resp.text()}"
                    )
                    return {"error": f"HTTP {resp.status}"}
                return await resp.json()
        except Exception as e:
            log_warning(f"APIMemory request failed: {e}")
            return {"error": str(e)}

    async def add(self, role: str, content: str, metadata: Dict = None):
        """افزودن یک ورودی به حافظه API"""
        return await self._post(
            "add",
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
            },
        )

    async def search(self, query: str, n: int = 5) -> List[Dict]:
        """جستجوی معنایی در حافظه API"""
        result = await self._post("search", {"query": query, "n": n})
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "results" in result:
            return result["results"]
        return []

    async def get_context(self, query: str = "", n: int = 5) -> List[Dict]:
        """دریافت زمینه (مشابه search اما با فرمت context)"""
        result = await self._post("context", {"query": query, "n": n})
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "context" in result:
            return result["context"]
        return []

    async def clear(self):
        """پاک‌سازی حافظه API"""
        await self._post("clear", {})

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None


class BrainMemory:
    """
    حافظه هوشمند مغز: کوتاه‌مدت + خلاصه + ChromaDB یا API
    """

    def __init__(
        self,
        max_tokens: int = MAX_CONTEXT_TOKENS,
        use_api: bool = False,
        api_url: str = "",
        api_key: str = "",
    ):
        self.max_tokens = max_tokens
        self.short_term: List[Dict] = []
        self.summaries: List[str] = []
        self.use_api = use_api
        self.api_url = api_url
        self.api_key = api_key
        self.api_memory = None
        self.chroma_client = None
        self.collection = None

        if use_api and api_url:
            self.api_memory = APIMemory(api_url, api_key)
            log_success("🧠 حافظه API فعال شد")
        else:
            self._init_chromadb()

    def _init_chromadb(self):
        try:
            import chromadb

            self.chroma_client = chromadb.PersistentClient(path=str(MEMORY_DIR))
            self.collection = self.chroma_client.get_or_create_collection(
                name="brain_memory", metadata={"hnsw:space": "cosine"}
            )
            log_success("🧠 حافظه معنایی (ChromaDB) آماده است")
        except ImportError:
            log_warning("⚠️ ChromaDB نصب نیست - حافظه معنایی غیرفعال")
        except Exception as e:
            log_warning(f"⚠️ خطای ChromaDB: {e}")

    def add(self, role: str, content: str):
        self.short_term.append(
            {"role": role, "content": content, "timestamp": time.time()}
        )

        # ذخیره در API (اگر فعال باشد)
        if self.api_memory:
            try:
                asyncio.create_task(self.api_memory.add(role, content))
            except Exception as e:
                log_warning(f"APIMemory add failed: {e}")

        # ذخیره در ChromaDB (اگر فعال باشد)
        if self.collection:
            try:
                doc_id = hashlib.md5(
                    f"{role}{content}{time.time()}".encode()
                ).hexdigest()
                self.collection.add(
                    documents=[content],
                    metadatas=[{"role": role, "timestamp": time.time()}],
                    ids=[doc_id],
                )
            except Exception as e:
                log_warning(f"ChromaDB add failed: {e}")

        if len(self.short_term) > 20:
            self._compress()

    def _compress(self):
        if len(self.short_term) < 10:
            return
        to_summarize = self.short_term[:15]
        text = "\n".join([f"{m['role']}: {m['content'][:300]}" for m in to_summarize])
        summary = f"[خلاصه‌ی {len(to_summarize)} پیام قبلی] {text[:600]}..."
        self.summaries.append(summary)
        self.short_term = self.short_term[15:]
        log_info(f"📦 خلاصه‌سازی شد: {len(to_summarize)} پیام → ۱ خلاصه")

    def get_context(self, query: str = "") -> List[Dict]:
        context: List[Dict] = []
        tokens = 0

        # پیام‌های کوتاه‌مدت
        for msg in self.short_term[-5:]:
            context.append(msg)
            tokens += len(msg.get("content", "")) // 4

        # خلاصه‌ها
        for summary in reversed(self.summaries):
            if tokens + len(summary) // 4 > self.max_tokens:
                break
            context.append({"role": "system", "content": summary})
            tokens += len(summary) // 4

        # جستجو در API (اگر فعال باشد)
        if self.api_memory and query:
            try:
                # برای جلوگیری از بلاک کردن، از asyncio.run_coroutine_threadsafe استفاده می‌کنیم
                loop = asyncio.get_running_loop()
                results = loop.run_until_complete(
                    self.api_memory.get_context(query, n=3)
                )
                for item in results:
                    if isinstance(item, dict):
                        doc = item.get("content", str(item))
                        if tokens + len(doc) // 4 > self.max_tokens:
                            break
                        context.append(
                            {"role": "system", "content": f"[حافظه API] {doc}"}
                        )
                        tokens += len(doc) // 4
            except Exception as e:
                log_warning(f"APIMemory search failed: {e}")

        # جستجو در ChromaDB (اگر فعال باشد)
        if query and self.collection:
            try:
                results = self.collection.query(query_texts=[query], n_results=3)
                if results.get("documents"):
                    for doc in results["documents"][0]:
                        if tokens + len(doc) // 4 > self.max_tokens:
                            break
                        context.append(
                            {"role": "system", "content": f"[حافظه مرتبط] {doc}"}
                        )
                        tokens += len(doc) // 4
            except Exception as e:
                log_warning(f"جستجوی حافظه ناموفق: {e}")

        log_info(f"📊 Context: {len(context)} پیام, ~{tokens} توکن")
        return context

    def clear(self):
        self.short_term = []
        self.summaries = []
        if self.api_memory:
            try:
                asyncio.create_task(self.api_memory.clear())
            except:
                pass
        if self.collection:
            try:
                self.collection.delete(ids=self.collection.get()["ids"])
            except:
                pass

    def search(self, query: str, n: int = 5) -> List[Dict]:
        # جستجو در API
        if self.api_memory:
            try:
                loop = asyncio.get_running_loop()
                results = loop.run_until_complete(self.api_memory.search(query, n))
                if results:
                    return results
            except Exception as e:
                log_warning(f"APIMemory search failed: {e}")

        # جستجو در ChromaDB
        if not self.collection:
            return []
        try:
            results = self.collection.query(query_texts=[query], n_results=n)
            if not results or not results.get("documents"):
                return []
            return [
                {"role": m.get("role", "unknown"), "content": d[:300]}
                for d, m in zip(results["documents"][0], results["metadatas"][0])
            ]
        except Exception as e:
            log_warning(f"Search failed: {e}")
            return []

    async def close(self):
        if self.api_memory:
            await self.api_memory.close()


# ─── Semantic Search پروژه ───


class ProjectSemanticSearch:
    def __init__(self):
        self.client = None
        self.collection = None
        self._init()

    def _init(self):
        try:
            import chromadb
            from chromadb.utils import embedding_functions

            self.client = chromadb.PersistentClient(path=str(MEMORY_DIR / "codebase"))
            self.embedding_fn = (
                embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name="all-MiniLM-L6-v2"
                )
            )
            self.collection = self.client.get_or_create_collection(
                name="code_embeddings", embedding_function=self.embedding_fn
            )
            log_success("✅ Semantic Search پروژه فعال شد")
        except ImportError:
            log_warning("⚠️ Semantic Search غیرفعال (chromadb نصب نیست)")
        except Exception as e:
            log_warning(f"⚠️ Semantic Search غیرفعال: {e}")

    def index_files(self, patterns: Optional[List[str]] = None):
        if not self.collection:
            return 0
        if patterns is None:
            patterns = [
                "**/*.py",
                "**/*.txt",
                "**/*.md",
                "**/*.json",
                "**/*.yaml",
                "**/*.yml",
            ]
        total = 0
        project = Path(PROJECT_BASE)
        for pattern in patterns:
            for fpath in glob.glob(pattern, root_dir=str(project), recursive=True):
                try:
                    full = project / fpath
                    if full.is_file() and full.stat().st_size < MAX_FILE_SIZE_BYTES:
                        content = full.read_text(encoding="utf-8", errors="ignore")
                        self.collection.add(
                            documents=[content],
                            metadatas=[{"path": str(fpath)}],
                            ids=[f"file_{hashlib.md5(fpath.encode()).hexdigest()}"],
                        )
                        total += 1
                except Exception as e:
                    log_warning(f"Index failed {fpath}: {e}")
        log_success(f"✅ {total} فایل ایندکس شدند")
        return total

    def search(self, query: str, n: int = 5) -> List[Dict]:
        if not self.collection:
            return []
        try:
            results = self.collection.query(query_texts=[query], n_results=n)
            if not results or not results.get("documents"):
                return []
            return [
                {
                    "path": m.get("path", "?"),
                    "content": d[:500] + ("..." if len(d) > 500 else ""),
                }
                for d, m in zip(results["documents"][0], results["metadatas"][0])
            ]
        except Exception as e:
            log_warning(f"Search failed: {e}")
            return []


# ─── موتور یادگیری ───


class LearningEngine:
    def __init__(self, agent):
        self.agent = agent
        self.learned_patterns: Dict = {}
        self.successful_interactions: List[Dict] = []
        self.failed_interactions: List[Dict] = []
        self._load_learned_data()

    def _load_learned_data(self):
        learn_file = BRAIN_DIR / "learned_patterns.json"
        if learn_file.exists():
            try:
                data = json.loads(learn_file.read_text(encoding="utf-8"))
                self.learned_patterns = data.get("patterns", {})
                self.successful_interactions = data.get("successful", [])
                self.failed_interactions = data.get("failed", [])
            except:
                pass

    def _save_learned_data(self):
        learn_file = BRAIN_DIR / "learned_patterns.json"
        try:
            learn_file.write_text(
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
        except Exception as e:
            log_warning(f"Save learned data failed: {e}")

    def record_interaction(
        self, user_input: str, response: str, success: bool, details: Dict = None
    ):
        interaction = {
            "timestamp": datetime.now().isoformat(),
            "user_input": user_input,
            "response": response[:500],
            "success": success,
            "details": details or {},
            "tools_used": details.get("tools_used", []) if details else [],
        }
        if success:
            self.successful_interactions.append(interaction)
            self._extract_patterns(interaction)
        else:
            self.failed_interactions.append(interaction)
        self._save_learned_data()

    def _extract_patterns(self, interaction: Dict):
        user_input = interaction.get("user_input", "").lower()
        tools = interaction.get("tools_used", [])
        for tool in tools:
            key = f"tool_{tool}"
            if key not in self.learned_patterns:
                self.learned_patterns[key] = {"count": 0, "keywords": []}
            self.learned_patterns[key]["count"] += 1
            words = [w for w in user_input.split() if len(w) > 3]
            for word in words[:5]:
                if word not in self.learned_patterns[key]["keywords"]:
                    self.learned_patterns[key]["keywords"].append(word)
        if "ساخت" in user_input or "create" in user_input or "build" in user_input:
            self.learned_patterns["task_type_build"] = {
                "count": self.learned_patterns.get("task_type_build", {}).get(
                    "count", 0
                )
                + 1
            }
        elif "رفع" in user_input or "fix" in user_input or "bug" in user_input:
            self.learned_patterns["task_type_fix"] = {
                "count": self.learned_patterns.get("task_type_fix", {}).get("count", 0)
                + 1
            }
        elif "تست" in user_input or "test" in user_input:
            self.learned_patterns["task_type_test"] = {
                "count": self.learned_patterns.get("task_type_test", {}).get("count", 0)
                + 1
            }

    def suggest_tool_for_task(self, task: str) -> Optional[str]:
        task_lower = task.lower()
        best_tool = None
        best_score = 0
        for pattern_key, data in self.learned_patterns.items():
            if not pattern_key.startswith("tool_"):
                continue
            tool_name = pattern_key.replace("tool_", "")
            keywords = data.get("keywords", [])
            score = sum(1 for kw in keywords if kw in task_lower)
            if score > best_score:
                best_score = score
                best_tool = tool_name
        if best_score >= 2:
            return best_tool
        return None


# ─── برنامه‌ریز چندمرحله‌ای ───


class TaskPlanner:
    def __init__(self, agent):
        self.agent = agent
        self.plans: Dict[str, List[Dict]] = {}
        self.filepath = BRAIN_DIR / "plans.json"
        self.load()

    def load(self):
        if self.filepath.exists():
            try:
                self.plans = json.loads(self.filepath.read_text(encoding="utf-8"))
            except:
                self.plans = {}

    def save(self):
        try:
            self.filepath.write_text(
                json.dumps(self.plans, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except:
            pass

    def plan_task(self, task: str) -> List[Dict]:
        if not self.agent.client:
            return [{"step": 1, "action": "execute", "description": task}]
        prompt = f"""
You are a world‑class project manager. Given the task: "{task}", create a detailed step‑by‑step plan.
Each step must have:
- Step number
- Action (e.g., "analyze", "modify", "test", "verify", "search")
- Description
- Dependencies (list of step numbers that must be completed first)
- Estimated time (low/medium/high)

Return the plan as a JSON array of objects with keys: step, action, description, dependencies, time.
"""
        try:
            response = self.agent.client.chat.completions.create(
                model="deepseek-reasoner",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1500,
            )
            plan_text = response.choices[0].message.content.strip()
            json_match = re.search(r"\[.*\]", plan_text, re.DOTALL)
            if json_match:
                plan = json.loads(json_match.group())
                self.plans[task] = plan
                self.save()
                return plan
            return [{"step": 1, "action": "execute", "description": task}]
        except Exception as e:
            log_error(f"خطا در برنامه‌ریزی: {e}")
            return [{"step": 1, "action": "execute", "description": task}]

    def execute_plan(self, task: str) -> str:
        plan = self.plan_task(task)
        if not plan:
            return "❌ برنامه‌ای تولید نشد."
        results = {}
        for step in sorted(plan, key=lambda x: x["step"]):
            step_num = step["step"]
            log_info(f"📋 مرحله {step_num}: {step['action']} - {step['description']}")
            deps = step.get("dependencies", [])
            for dep in deps:
                if dep not in results:
                    return f"❌ وابستگی {dep} برای مرحله {step_num} برآورده نشد."
            subtask = step["description"]
            result = self.agent.agent(subtask, mode="precise")
            results[step_num] = result
            log_info(f"✅ مرحله {step_num} کامل شد.")
        final = "\n\n".join([f"مرحله {k}: {v}" for k, v in sorted(results.items())])
        return f"📌 برنامه تکمیل شد:\n{final}"


# ─── خودترمیمی ───


class SelfHealer:
    def __init__(self, agent, max_attempts: int = 3):
        self.agent = agent
        self.max_attempts = max_attempts

    def fix_bug(self, file_path: str, error_message: str) -> str:
        for attempt in range(1, self.max_attempts + 1):
            log_info(f"🔧 تلاش {attempt} برای رفع باگ در {file_path}")
            content = self.agent._read_file(file_path)
            if "⚠️" in content:
                return f"❌ فایل {file_path} قابل خواندن نیست."
            prompt = f"""
I have a bug in the following code:
{content}
Error message:
{error_message}
Please fix the bug. Provide only the corrected code without extra text.
"""
            if not self.agent.client:
                return "❌ حالت آفلاین - نمی‌توانم اصلاح کنم."
            try:
                response = self.agent.client.chat.completions.create(
                    model="deepseek-reasoner",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=2000,
                )
                corrected = response.choices[0].message.content.strip()
                code_blocks = re.findall(r"```python\n(.*?)```", corrected, re.DOTALL)
                if code_blocks:
                    new_code = code_blocks[0]
                else:
                    new_code = corrected
                result = self.agent._write_file(file_path, new_code)
                if "✅" in result:
                    test_result = self.agent._run_tests(file_path)
                    if "✅" in test_result or "passed" in test_result.lower():
                        return f"✅ باگ رفع شد در تلاش {attempt}.\n{test_result}"
                    else:
                        error_message = test_result
                        log_warning(f"تست پس از اصلاح ناموفق: {error_message}")
                else:
                    return f"❌ خطا در نوشتن فایل: {result}"
            except Exception as e:
                log_error(f"خطا در تلاش اصلاح: {e}")
        return f"❌ پس از {self.max_attempts} تلاش، باگ رفع نشد."


# ─── تولید تست ───


class TestGenerator:
    def __init__(self, agent):
        self.agent = agent

    def generate_tests(self, file_path: str) -> str:
        content = self.agent._read_file(file_path)
        if "⚠️" in content:
            return f"❌ فایل {file_path} قابل خواندن نیست."
        prompt = f"""
Given the following Python code:
{content}
Generate a comprehensive unit test suite using pytest or unittest.
Include: Edge cases, Normal cases, Error cases, Mocking if needed.
Return only the test code.
"""
        if not self.agent.client:
            return "❌ حالت آفلاین - نمی‌توانم تست تولید کنم."
        try:
            response = self.agent.client.chat.completions.create(
                model="deepseek-reasoner",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2000,
            )
            test_code = response.choices[0].message.content.strip()
            code_blocks = re.findall(r"```python\n(.*?)```", test_code, re.DOTALL)
            if code_blocks:
                test_code = code_blocks[0]
            test_file = Path(file_path).parent / f"test_{Path(file_path).name}"
            result = self.agent._write_file(str(test_file), test_code)
            if "✅" in result:
                test_result = self.agent._run_tests(str(test_file))
                return f"✅ تست‌ها در {test_file} ذخیره شدند.\nنتیجه: {test_result}"
            else:
                return f"❌ خطا در ذخیره‌ی تست: {result}"
        except Exception as e:
            return f"❌ خطا در تولید تست: {e}"


# ─── جستجوی وب ───


class WebSearch:
    def __init__(self):
        self.session = None
        self._init_session()

    def _init_session(self):
        try:
            import requests

            self.session = requests.Session()
            self.session.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
        except ImportError:
            self.session = None

    def search(self, query: str, num_results: int = 5) -> str:
        if not self.session:
            return "❌ کتابخانه requests نصب نیست."
        try:
            url = "https://html.duckduckgo.com/html/"
            params = {"q": query}
            resp = self.session.get(url, params=params, timeout=10)
            resp.raise_for_status()
            try:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(resp.text, "html.parser")
                results = []
                for result in soup.select(".result"):
                    title_elem = result.select_one(".result__a")
                    snippet_elem = result.select_one(".result__snippet")
                    if title_elem and snippet_elem:
                        title = title_elem.get_text(strip=True)
                        snippet = snippet_elem.get_text(strip=True)
                        results.append(f"🔹 {title}\n   {snippet[:200]}...")
                        if len(results) >= num_results:
                            break
                if results:
                    return "\n\n".join(results)
                else:
                    return "🔍 نتیجه‌ای یافت نشد."
            except ImportError:
                return "❌ BeautifulSoup نصب نیست."
        except Exception as e:
            return f"❌ خطا در جستجو: {e}"


# ─── بازبینی کد ───


class CodeReviewer:
    def __init__(self, agent):
        self.agent = agent

    def review(self, file_path: str) -> str:
        content = self.agent._read_file(file_path)
        if "⚠️" in content:
            return f"❌ فایل {file_path} قابل خواندن نیست."
        prompt = f"""
You are a senior code reviewer. Analyze the following code and provide:
- Potential bugs
- Security vulnerabilities
- Code smells (anti‑patterns)
- Performance issues
- Suggestions for improvement
Code:
{content}
Return the review in a structured format with sections.
"""
        if not self.agent.client:
            return "❌ حالت آفلاین - امکان بازبینی وجود ندارد."
        try:
            response = self.agent.client.chat.completions.create(
                model="deepseek-reasoner",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2000,
            )
            return f"📋 بازبینی کد {file_path}:\n{response.choices[0].message.content.strip()}"
        except Exception as e:
            return f"❌ خطا در بازبینی: {e}"


# ─── کلاس اصلی مغز ───


class Brain:
    """
    مغز هوشمند امن ربات پایش سرنخ.
    ترکیبی از قابلیت‌های پیشرفتهٔ Agent و زیرساخت پروژه.
    """

    ALLOWED_TOOLS = {
        "read_file": {"risk": "low", "require_confirm": False},
        "list_directory": {"risk": "low", "require_confirm": False},
        "git_status": {"risk": "low", "require_confirm": False},
        "git_diff": {"risk": "low", "require_confirm": False},
        "git_log": {"risk": "low", "require_confirm": False},
        "get_system_info": {"risk": "low", "require_confirm": False},
        "list_processes": {"risk": "low", "require_confirm": False},
        "ping_host": {"risk": "low", "require_confirm": False},
        "analyze_code": {"risk": "low", "require_confirm": False},
        "semantic_search": {"risk": "low", "require_confirm": False},
        "grep_search": {"risk": "low", "require_confirm": False},
        "query_database": {"risk": "low", "require_confirm": False},
        "run_tests": {"risk": "low", "require_confirm": False},
        "clipboard_get": {"risk": "low", "require_confirm": False},
        "browse_web": {"risk": "low", "require_confirm": False},
        "web_search": {"risk": "low", "require_confirm": False},
        "get_project_context": {"risk": "low", "require_confirm": False},
        "system_dashboard": {"risk": "low", "require_confirm": False},
        "zip_archive": {"risk": "low", "require_confirm": False},
        "write_file": {"risk": "medium", "require_confirm": True},
        "edit_file": {"risk": "medium", "require_confirm": True},
        "delete_file": {"risk": "high", "require_confirm": True},
        "move_file": {"risk": "medium", "require_confirm": True},
        "git_commit": {"risk": "medium", "require_confirm": True},
        "git_branch": {"risk": "medium", "require_confirm": True},
        "kill_process": {"risk": "high", "require_confirm": True},
        "run_tests_coverage": {"risk": "low", "require_confirm": False},
        "analyze_security": {"risk": "low", "require_confirm": False},
        "backup_project": {"risk": "low", "require_confirm": False},
        "execute_command": {"risk": "high", "require_confirm": True},
        "execute_python": {"risk": "high", "require_confirm": True},
        "fix_bug": {"risk": "medium", "require_confirm": True},
        "generate_tests": {"risk": "low", "require_confirm": False},
        "code_review": {"risk": "low", "require_confirm": False},
        "plan_task": {"risk": "low", "require_confirm": False},
    }

    ALLOWED_DIRECT_COMMANDS = {
        "سلام": "greeting",
        "خوبی": "greeting",
        "وضعیت": "status",
        "گزارش": "report",
        "help": "help",
        "راهنما": "help",
        "کمک": "help",
    }

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        db: Optional[Any] = None,
        read_only: bool = False,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        full_access: bool = False,  # جدید: دسترسی کامل (دور زدن محدودیت‌های امنیتی)
        use_api_memory: bool = False,  # جدید: استفاده از حافظه API به‌جای ChromaDB
        memory_api_url: str = "",
        memory_api_key: str = "",
    ):
        PersianBrainHandler.setup_environment()
        self.event_bus = event_bus
        self.db = db
        self.read_only = read_only
        self.full_access = full_access
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not self.api_key:
            log_warning("⚠️ کلید API یافت نشد. لطفاً در .env تنظیم کنید.")
        self.model = model
        self.client = None
        self._init_client()
        self.cache = SmartCache("brain_cache")
        self.agent_cache = SmartCache("agent_cache")

        # ساخت حافظه با پشتیبانی از API
        self.memory = BrainMemory(
            max_tokens=MAX_CONTEXT_TOKENS,
            use_api=use_api_memory,
            api_url=memory_api_url,
            api_key=memory_api_key,
        )

        self.semantic_search = ProjectSemanticSearch()
        self.learning_engine = LearningEngine(self)
        self.planner = TaskPlanner(self)
        self.healer = SelfHealer(self)
        self.test_gen = TestGenerator(self)
        self.web_search = WebSearch()
        self.reviewer = CodeReviewer(self)
        self.busy = False
        self.current_mode = "precise"
        self.last_read_file: Optional[str] = None
        self.tools_definition = self._build_tools()
        self.stats: Dict[str, Any] = {
            "commands_processed": 0,
            "agents_run": 0,
            "errors": 0,
            "started_at": datetime.now().isoformat(),
        }
        if self.semantic_search.collection:
            threading.Thread(target=self._background_index, daemon=True).start()
        log_success(
            f"🧠 مغز هوشمند آماده | مدل: {self.model} | read_only: {read_only} | full_access: {full_access} | memory_api: {use_api_memory}"
        )

    # ────────────────── یکپارچگی با رویدادها ───────────────────

    def set_event_bus(self, event_bus: EventBus):
        self.event_bus = event_bus

    def set_database(self, db: Any):
        self.db = db

    async def _emit(self, event_name: str, data: Any):
        if self.event_bus:
            await self.event_bus.emit(f"brain.{event_name}", data, source="brain")

    # ────────────────── مقداردهی API ───────────────────

    def _init_client(self):
        if not self.api_key:
            log_warning("⚠️ مغز بدون کلید API - حالت آفلاین فعال است")
            self.client = None
            return
        try:
            # importِ تنبل: خودِ `import openai` در این محیط بلاک می‌کند (شبکه/پروکسی)
            try:
                from openai import OpenAI
            except Exception as e:
                log_error(f"❌ نصب/import پکیج openai ناموفق: {e}")
                self.client = None
                return

            base_url = "https://api.deepseek.com/v1"
            if self.model.startswith("claude"):
                base_url = "http://localhost:4000"
            elif self.model.startswith("gpt"):
                base_url = "https://api.openai.com/v1"
            elif self.model.startswith("ollama"):
                base_url = AI.get("url", "http://localhost:11434") + "/v1"
                self.model = (
                    self.model.replace("ollama/", "", 1)
                    if "/" in self.model
                    else AI.get("model", "qwen2.5:3b")
                )

            # استفاده از `OpenAI` مستقیم (ورژن جدید)
            self.client = OpenAI(
                api_key=self.api_key, base_url=base_url, timeout=120, max_retries=3
            )
            self.client.models.list()
            log_success(f"✅ اتصال مغز به {base_url} برقرار شد")
        except Exception as e:
            log_error(f"❌ خطا در اتصال مغز: {e}")
            self.client = None

    def set_api_key(self, key: str):
        self.api_key = key
        self._init_client()

    # ────────────────── پس‌زمینه ───────────────────

    def _background_index(self):
        try:
            self.semantic_search.index_files()
        except Exception as e:
            log_warning(f"ایندکس‌سازی پس‌زمینه: {e}")

    # ─────────────────── ساخت ابزارها ────

    def _build_tools(self) -> List[Dict]:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "خواندن کامل یک فایل (تا ۵ مگ)",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "نوشتن یا بازنویسی یک فایل (نیاز به تأیید)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_file",
                    "description": "ویرایش جراحی در یک فایل (نیاز به تأیید)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "old_text": {"type": "string"},
                            "new_text": {"type": "string"},
                        },
                        "required": ["path", "old_text", "new_text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "description": "لیست محتویات یک پوشه",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string", "default": "."}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_file",
                    "description": "حذف امن یک فایل با پشتیبان خودکار (نیاز به تأیید)",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "move_file",
                    "description": "جابجایی یا تغییر نام فایل/پوشه (نیاز به تأیید)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "destination": {"type": "string"},
                        },
                        "required": ["source", "destination"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "git_status",
                    "description": "نمایش وضعیت مخزن Git",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "git_diff",
                    "description": "نمایش تغییرات فایل‌ها در Git",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "git_commit",
                    "description": "ثبت commit با پیام (نیاز به تأیید)",
                    "parameters": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "git_log",
                    "description": "نمایش تاریخچه کامیت‌ها",
                    "parameters": {
                        "type": "object",
                        "properties": {"n": {"type": "integer", "default": 10}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "git_branch",
                    "description": "ایجاد یا تغییر برنچ (نیاز به تأیید)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "action": {"type": "string", "enum": ["create", "switch"]},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "execute_command",
                    "description": "اجرای دستور سیستمی (نیاز به تأیید - whitelist شده)",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_system_info",
                    "description": "دریافت اطلاعات سیستم (CPU, RAM, Disk)",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_processes",
                    "description": "لیست پردازه‌های در حال اجرا",
                    "parameters": {
                        "type": "object",
                        "properties": {"limit": {"type": "integer", "default": 20}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "kill_process",
                    "description": "پایان دادن به یک پردازه (نیاز به تأیید)",
                    "parameters": {
                        "type": "object",
                        "properties": {"pid": {"type": "integer"}},
                        "required": ["pid"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "ping_host",
                    "description": "تست اتصال به یک هاست",
                    "parameters": {
                        "type": "object",
                        "properties": {"host": {"type": "string"}},
                        "required": ["host"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_tests",
                    "description": "اجرای تست‌ها (pytest/unittest)",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "execute_python",
                    "description": "اجرای کد پایتون در محیط شنی (ایمن - نیاز به تأیید)",
                    "parameters": {
                        "type": "object",
                        "properties": {"code": {"type": "string"}},
                        "required": ["code"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "analyze_code",
                    "description": "تحلیل استاتیک کد با AST",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "semantic_search",
                    "description": "جستجوی معنایی در کد پروژه",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "n_results": {"type": "integer", "default": 5},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "grep_search",
                    "description": "جستجوی ساده با regex در فایل‌ها",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string"},
                            "file_pattern": {"type": "string", "default": "*.py"},
                        },
                        "required": ["pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "query_database",
                    "description": "اجرای کوئری SQL روی دیتابیس پروژه (فقط خواندنی)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "limit": {"type": "integer", "default": 50},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "browse_web",
                    "description": "خواندن محتوای یک صفحه وب (متن)",
                    "parameters": {
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "clipboard_get",
                    "description": "خواندن کلیپ‌بورد سیستم",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "zip_archive",
                    "description": "فشرده‌سازی یا باز کردن یک آرشیو ZIP",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "dest": {"type": "string"},
                            "action": {
                                "type": "string",
                                "enum": ["compress", "extract"],
                                "default": "compress",
                            },
                        },
                        "required": ["source"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "جستجوی پیشرفته در وب با خلاصه‌سازی",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "num_results": {"type": "integer", "default": 5},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "generate_tests",
                    "description": "تولید تست خودکار برای یک فایل",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "code_review",
                    "description": "بازبینی کد و ارائه پیشنهادات",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fix_bug",
                    "description": "رفع خودکار باگ با استفاده از پیام خطا",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "error": {"type": "string"},
                        },
                        "required": ["path", "error"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "plan_task",
                    "description": "برنامه‌ریزی چندمرحله‌ای برای یک کار",
                    "parameters": {
                        "type": "object",
                        "properties": {"task": {"type": "string"}},
                        "required": ["task"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_project_context",
                    "description": "دریافت اطلاعات زمینه‌ی پروژه (ساختار، وابستگی‌ها)",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "system_dashboard",
                    "description": "نمایش داشبورد کامل سیستم",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_tests_coverage",
                    "description": "اجرای تست با گزارش پوشش کد",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "analyze_security",
                    "description": "بررسی امنیت کد با bandit",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "backup_project",
                    "description": "گرفتن بکاپ کامل و فشرده از کل پروژه",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "destination": {
                                "type": "string",
                                "description": "نام فایل خروجی (اختیاری)",
                            }
                        },
                    },
                },
            },
        ]
        return tools

    # ────────────────── پیاده‌سازی امن ابزارها ────

    def _safepath(self, p: str) -> Path:
        return safe_path(p, Path(PROJECT_BASE))

    def _is_safe_write_path(self, path_str: str) -> bool:
        # اگر full_access فعال باشد، همه‌ی مسیرها مجاز هستند
        if self.full_access:
            return True
        from pathlib import Path
        from config import BASE_DIR

        try:
            p = Path(path_str).resolve()
            base = Path(BASE_DIR).resolve()
        except Exception:
            return False
        if not str(p).startswith(str(base)):
            return False
        allowed_prefixes = [
            base / "data",
            base / "storage" / "reports",
            base / "storage" / "database",
        ]
        for allowed in allowed_prefixes:
            if str(p).startswith(str(allowed)):
                return True
        blocked_prefixes = [
            base / "core",
            base / "collectors",
            base / "handlers",
            base / "analyzers",
            base / ".env",
            base / "main.py",
            base / "config.py",
            base / "panel.py",
            base / "login.py",
        ]
        for blocked in blocked_prefixes:
            if str(p).startswith(str(blocked)):
                return False
        return False

    async def _read_file(self, path: str) -> str:
        p = self._safepath(path)
        if not p.exists():
            return f"⚠️ فایل '{path}' وجود ندارد."
        if p.stat().st_size > MAX_FILE_SIZE_BYTES:
            return f"⚠️ فایل بزرگتر از {human_bytes(MAX_FILE_SIZE_BYTES)} است."
        try:
            content = p.read_text(encoding="utf-8")
            self.last_read_file = path
            if len(content) > MAX_CONTENT_LENGTH:
                return f"📄 {path} ({len(content)} کاراکتر)\n{content[:MAX_CONTENT_LENGTH]}\n... [ادامه در فایل اصلی]"
            return f"📄 {path} ({len(content)} کاراکتر)\n{content}"
        except UnicodeDecodeError:
            return f"⚠️ فایل باینری: {path} ({human_bytes(p.stat().st_size)})"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _write_file(self, path: str, content: str) -> str:
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"
        if len(content) > MAX_CONTENT_LENGTH * 5:
            return "⚠️ محتوا خیلی بزرگ است."
        p = self._safepath(path)
        backup = None
        if p.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = BACKUP_DIR / f"{p.stem}_{ts}{p.suffix}"
            shutil.copy2(p, backup)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            msg = f"✅ فایل '{path}' نوشته شد ({len(content)} کاراکتر)"
            if backup:
                msg += f"\n📦 بکاپ: {backup.name}"
            return msg
        except Exception as e:
            if backup and backup.exists():
                shutil.copy2(backup, p)
            return f"❌ خطا (بازیابی شد): {e}"

    async def _edit_file(self, path: str, old_text: str, new_text: str) -> str:
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"
        p = self._safepath(path)
        if not p.exists():
            return f"⚠️ فایل '{path}' وجود ندارد."
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = BACKUP_DIR / f"{p.stem}_{ts}{p.suffix}"
        shutil.copy2(p, backup)
        try:
            content = p.read_text(encoding="utf-8")
            if old_text not in content:
                return "⚠️ متن قدیمی در فایل یافت نشد."
            new_content = content.replace(old_text, new_text, 1)
            p.write_text(new_content, encoding="utf-8")
            diff = "".join(
                list(
                    difflib.unified_diff(
                        content.splitlines(keepends=True),
                        new_content.splitlines(keepends=True),
                        fromfile=f"a/{path}",
                        tofile=f"b/{path}",
                        lineterm="",
                    )
                )[:30]
            )
            return f"✅ ویرایش شد.\n📦 بکاپ: {backup.name}\n📊 {diff[:2000]}"
        except Exception as e:
            if backup.exists():
                shutil.copy2(backup, p)
            return f"❌ خطا (بازیابی شد): {e}"

    async def _list_directory(self, path: str = ".") -> str:
        p = self._safepath(path)
        if not p.exists():
            return f"⚠️ مسیر '{path}' وجود ندارد."
        items = []
        for item in sorted(p.iterdir()):
            try:
                st = item.stat()
                icon = "📁" if item.is_dir() else "📄"
                size = "<DIR>" if item.is_dir() else human_bytes(st.st_size)
                mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                items.append(f"{icon} {item.name:<30} {size:>12}  {mtime}")
            except:
                continue
        return "\n".join(items) or "📭 پوشه خالی است."

    async def _delete_file(self, path: str) -> str:
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"
        p = self._safepath(path)
        if not p.exists():
            return f"⚠️ فایل '{path}' وجود ندارد."
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = BACKUP_DIR / f"{p.stem}_{ts}{p.suffix}"
        shutil.copy2(p, backup)
        try:
            os.remove(p)
            return f"✅ حذف شد. 📦 بکاپ: {backup.name}"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _move_file(self, source: str, destination: str) -> str:
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"
        src = self._safepath(source)
        dst = self._safepath(destination)
        if not src.exists():
            return f"⚠️ '{source}' وجود ندارد."
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, dst)
            return f"✅ {source} → {destination}"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _git_status(self) -> str:
        try:
            r = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                cwd=PROJECT_BASE,
            )
            return r.stdout or "✅ Working tree clean"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _git_diff(self, path: str = "") -> str:
        try:
            cmd = ["git", "diff"] + (["--", path] if path else [])
            r = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_BASE)
            return r.stdout[:MAX_CONTENT_LENGTH] or "✅ بدون تغییر"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _git_commit(self, message: str) -> str:
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"
        try:
            subprocess.run(
                ["git", "add", "."], cwd=PROJECT_BASE, check=True, capture_output=True
            )
            r = subprocess.run(
                ["git", "commit", "-m", message],
                capture_output=True,
                text=True,
                cwd=PROJECT_BASE,
            )
            return r.stdout[:2000] or "✅ Commit انجام شد."
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _git_log(self, n: int = 10) -> str:
        try:
            r = subprocess.run(
                ["git", "log", f"-{n}", "--oneline", "--decorate"],
                capture_output=True,
                text=True,
                cwd=PROJECT_BASE,
            )
            return r.stdout or "📭 هیچ کامیتی یافت نشد."
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _git_branch(self, name: str, action: str = "create") -> str:
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"
        try:
            cmd = (
                ["git", "checkout", "-b", name]
                if action == "create"
                else ["git", "checkout", name]
            )
            r = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_BASE)
            return r.stdout[:2000] or f"✅ برنچ {name}"
        except Exception as e:
            return f"❌ خطا: {e}"

    SAFE_PREFIXES = [
        "ls",
        "dir",
        "echo",
        "pytest",
        "python",
        "python3",
        "node",
        "npm",
        "git",
        "cat",
        "head",
        "tail",
        "pip",
        "pip3",
        "type",
        "where",
        "which",
        "date",
        "time",
        "whoami",
        "hostname",
        "ipconfig",
        "ifconfig",
        "netstat",
        "ps",
        "tasklist",
        "systeminfo",
        "uname",
    ]

    async def _execute_command(self, command: str) -> str:
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"
        first = command.strip().split()[0] if command.strip() else ""
        if not any(
            first == prefix or first.startswith(prefix) for prefix in self.SAFE_PREFIXES
        ):
            return f"⛔ دستور '{first}' در whitelist نیست. دستورات مجاز: {', '.join(self.SAFE_PREFIXES[:10])}..."
        try:
            r = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=MAX_COMMAND_TIMEOUT,
                encoding="utf-8",
                errors="replace",
                cwd=PROJECT_BASE,
            )
            out = r.stdout + r.stderr
            if len(out) > MAX_CONTENT_LENGTH:
                out = out[:MAX_CONTENT_LENGTH] + "\n... [خلاصه شد]"
            return out or "(اجرا شد - بدون خروجی)"
        except subprocess.TimeoutExpired:
            return f"⏰ زمان اجرا (>{MAX_COMMAND_TIMEOUT}ثانیه) به پایان رسید."
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _get_system_info(self) -> str:
        try:
            import psutil

            cpu = psutil.cpu_percent(interval=0.3)
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage("/" if os.name != "nt" else "C:")
            boot = datetime.fromtimestamp(psutil.boot_time())
            return (
                f"🔹 CPU: {cpu}%\n"
                f"🔹 RAM: {ram.percent}% ({human_bytes(ram.used)} / {human_bytes(ram.total)})\n"
                f"🔹 دیسک: {disk.percent}% ({human_bytes(disk.used)} / {human_bytes(disk.total)})\n"
                f"🔹 پردازه‌ها: {len(psutil.pids())}\n"
                f"🔹 روشن از: {boot.strftime('%Y-%m-%d %H:%M')}\n"
                f"🔹 سیستم: {platform.system()} {platform.release()}"
            )
        except ImportError:
            return f"🔹 سیستم: {platform.system()} {platform.release()}\n🔹 (psutil نصب نیست)"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _list_processes(self, limit: int = 20) -> str:
        try:
            import psutil

            procs = []
            for p in psutil.process_iter(
                ["pid", "name", "cpu_percent", "memory_percent"]
            ):
                try:
                    procs.append(p.info)
                except:
                    continue
            procs.sort(key=lambda x: x.get("cpu_percent", 0), reverse=True)
            lines = []
            for p in procs[:limit]:
                lines.append(
                    f"{p['pid']:>6}  {p['name'][:25]:<25} CPU:{p.get('cpu_percent',0):>5.1f}% MEM:{p.get('memory_percent',0):>5.1f}%"
                )
            return "\n".join(lines) or "📭 پردازه‌ای نیست."
        except ImportError:
            return "⚠️ psutil نصب نیست."
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _kill_process(self, pid: int) -> str:
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"
        try:
            import psutil

            p = psutil.Process(pid)
            name = p.name()
            p.terminate()
            time.sleep(1)
            if p.is_running():
                p.kill()
            return f"✅ پردازه {pid} ({name}) کشته شد."
        except ImportError:
            return "⚠️ psutil نصب نیست."
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _ping_host(self, host: str) -> str:
        try:
            param = "-n" if os.name == "nt" else "-c"
            r = subprocess.run(
                ["ping", param, "2", host], capture_output=True, text=True, timeout=10
            )
            return r.stdout[:3000] or f"✅ {host} پاسخ داد."
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _run_tests(self, path: str = "") -> str:
        try:
            cmd = (
                f"python -m pytest {path} -v --tb=short -x"
                if path
                else "python -m pytest -v --tb=short -x"
            )
            r = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=PROJECT_BASE,
            )
            return r.stdout[:4000] or r.stderr[:4000] or "✅ تست‌ها اجرا شدند."
        except subprocess.TimeoutExpired:
            return "⏰ زمان تست به پایان رسید."
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _run_tests_with_coverage(self, path: str) -> str:
        try:
            cmd = f"pytest {path} --cov=. --cov-report=html"
            r = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=PROJECT_BASE,
            )
            return f"✅ گزارش پوشش کد در htmlcov/ ذخیره شد.\n{r.stdout[:4000]}"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _analyze_security(self, path: str) -> str:
        try:
            r = subprocess.run(
                ["bandit", "-r", path, "-f", "txt"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            return r.stdout or "✅ هیچ آسیب‌پذیری با ریسک بالا یافت نشد."
        except FileNotFoundError:
            return "⚠️ bandit نصب نیست. برای نصب: pip install bandit"
        except subprocess.TimeoutExpired:
            return "⏰ زمان بررسی امنیت به پایان رسید."
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _backup_project(self, destination: str = None) -> str:
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی فعال است"
        if not destination:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            destination = f"Project_Backup_{ts}.zip"
        try:
            dest_path = self._safepath(destination)
            shutil.make_archive(str(dest_path).replace(".zip", ""), "zip", PROJECT_BASE)
            return f"✅ بکاپ کامل پروژه در {dest_path} ذخیره شد."
        except Exception as e:
            return f"❌ خطا در بکاپ‌گیری: {e}"

    async def _execute_python(self, code: str) -> str:
        dangerous_patterns = [
            r"\bos\.(system|popen|fork|exec)\b",
            r"\bsubprocess\b",
            r"\b__import__\b",
            r"\beval\b",
            r"\bexec\b",
            r"\bcompile\b",
            r"open\(\s*['\"]/",
            r"\bshutil\.(rmtree|remove)\b",
        ]
        for pat in dangerous_patterns:
            if re.search(pat, code, re.IGNORECASE):
                return f"⛔ کد شامل عملیات خطرناک است: {pat}"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp = f.name
        try:
            r = subprocess.run(
                [sys.executable, tmp],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=PROJECT_BASE,
            )
            return (r.stdout + r.stderr)[
                :MAX_CONTENT_LENGTH
            ] or "(اجرا شد - بدون خروجی)"
        except subprocess.TimeoutExpired:
            return "⏰ زمان اجرا به پایان رسید."
        except Exception as e:
            return f"❌ خطا: {e}"
        finally:
            try:
                os.unlink(tmp)
            except:
                pass

    async def _analyze_code(self, path: str) -> str:
        p = self._safepath(path)
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
            funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
            classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
            imports = [
                n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)
            ]
            imports_from = []
            for n in ast.walk(tree):
                if isinstance(n, ast.ImportFrom) and n.module:
                    imports_from.append(n.module)
            return (
                f"📊 تحلیل کد {path}:\n"
                f"🔹 توابع ({len(funcs)}): {', '.join(funcs[:15]) or 'ندارد'}\n"
                f"🔹 کلاس‌ها ({len(classes)}): {', '.join(classes[:10]) or 'ندارد'}\n"
                f"🔹 ایمپورت‌ها ({len(imports)+len(imports_from)}): {', '.join(imports[:10] + imports_from[:10]) or 'ندارد'}"
            )
        except SyntaxError as e:
            return f"❌ خطای نحوی: {e}"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _semantic_search(self, query: str, n_results: int = 5) -> str:
        res = self.semantic_search.search(query, n_results)
        if not res:
            return "🔍 نتیج‌ای یافت نشد."
        lines = []
        for r in res:
            lines.append(f"📄 {r['path']}")
            lines.append(f"   {r['content'][:200]}...")
        return "\n".join(lines)

    async def _grep_search(self, pattern: str, file_pattern: str = "*.py") -> str:
        results = []
        for fpath in glob.glob(
            f"**/{file_pattern}", root_dir=PROJECT_BASE, recursive=True
        ):
            try:
                full = Path(PROJECT_BASE) / fpath
                with open(full, "r", encoding="utf-8") as f:
                    for i, line in enumerate(f, 1):
                        if re.search(pattern, line, re.IGNORECASE):
                            results.append(f"{fpath}:{i}: {line.strip()[:80]}")
                            if len(results) >= 30:
                                break
            except:
                continue
        return "\n".join(results) or "🔍 چیزی یافت نشد."

    async def _query_database(self, query: str, limit: int = 50) -> str:
        q_upper = query.strip().upper()
        if not q_upper.startswith("SELECT"):
            return "⛔ فقط کوئری SELECT مجاز است."
        if not self.db:
            return "⚠️ دیتابیس متصل نیست."
        try:
            rows = await self.db.execute(query)
            if not rows:
                return "📭 نتیجه‌ای یافت نشد."
            all_rows = await rows.fetchall()
            if not all_rows:
                return "📭 نتیجه‌ای یافت نشد."
            lines = ["📊 نتایج کوئری:"]
            for row in all_rows[:limit]:
                lines.append(" | ".join(str(c) for c in row))
            if len(all_rows) > limit:
                lines.append(f"... و {len(all_rows) - limit} نتیجه دیگر")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ خطا در کوئری: {e}"

    async def _browse_web(self, url: str) -> str:
        try:
            import requests
            from bs4 import BeautifulSoup

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            r = requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            return text[:5000] or "📄 محتوای خالی"
        except ImportError:
            return "⚠️ کتابخانه requests/bs4 نصب نیست."
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _clipboard_get(self) -> str:
        try:
            import pyperclip

            return f"📋 کلیپ‌بورد:\n{pyperclip.paste()[:2000]}"
        except ImportError:
            return "⚠️ pyperclip نصب نیست."
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _zip_archive(
        self, source: str, dest: str = "", action: str = "compress"
    ) -> str:
        src = self._safepath(source)
        if not src.exists():
            return f"⚠️ '{source}' وجود ندارد."
        if action == "compress":
            if not dest:
                dest = f"{src.name}.zip"
            dst = self._safepath(dest)
            try:
                shutil.make_archive(str(dst).replace(".zip", ""), "zip", src)
                return f"✅ آرشیو {dest} ایجاد شد."
            except Exception as e:
                return f"❌ خطا: {e}"
        else:
            if not dest:
                dest = src.parent / src.stem
            dst = self._safepath(dest)
            try:
                with zipfile.ZipFile(src, "r") as z:
                    z.extractall(dst)
                return f"✅ آرشیو در {dest} باز شد."
            except Exception as e:
                return f"❌ خطا: {e}"

    # ─── ابزارهای جدید ───

    async def _web_search(self, query: str, num_results: int = 5) -> str:
        if self.web_search:
            return self.web_search.search(query, num_results)
        return "❌ جستجوی وب غیرفعال است."

    async def _generate_tests(self, path: str) -> str:
        if self.test_gen:
            return self.test_gen.generate_tests(path)
        return "❌ تولید تست غیرفعال است."

    async def _code_review(self, path: str) -> str:
        if self.reviewer:
            return self.reviewer.review(path)
        return "❌ بازبینی کد غیرفعال است."

    async def _fix_bug(self, path: str, error: str) -> str:
        if self.healer:
            return self.healer.fix_bug(path, error)
        return "❌ خودترمیمی غیرفعال است."

    async def _plan_task(self, task: str) -> str:
        if self.planner:
            return self.planner.execute_plan(task)
        return "❌ برنامه‌ریزی غیرفعال است."

    async def _get_project_context(self) -> str:
        project = Path(PROJECT_BASE)
        info = f"📂 پروژه: {project}\n"
        info += f"🔹 تعداد فایل‌های پایتون: {len(self.semantic_search.collection.get()['ids']) if self.semantic_search.collection else '?'}\n"
        info += f"🔹 حافظه معنایی: {'فعال' if self.semantic_search.collection else 'غیرفعال'}\n"
        return info

    async def _system_dashboard(self) -> str:
        try:
            import psutil

            cpu = psutil.cpu_percent(interval=0.3)
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            uptime = time.time() - psutil.boot_time()
            return f"""
📊 داشبورد:
─────────────────
CPU: {cpu}%
RAM: {ram.used / (1024**3):.1f}GB / {ram.total / (1024**3):.1f}GB ({ram.percent}%)
دیسک: {disk.used / (1024**3):.1f}GB / {disk.total / (1024**3):.1f}GB ({disk.percent}%)
پردازه: {len(psutil.pids())}
آپ‌تایم: {int(uptime // 3600)} ساعت {int((uptime % 3600) // 60)} دقیقه
"""
        except ImportError:
            return "⚠️ psutil نصب نیست."

    # ─── نقشهٔ ابزارها ───

    TOOL_MAP = {
        "read_file": _read_file,
        "write_file": _write_file,
        "edit_file": _edit_file,
        "list_directory": _list_directory,
        "delete_file": _delete_file,
        "move_file": _move_file,
        "git_status": _git_status,
        "git_diff": _git_diff,
        "git_commit": _git_commit,
        "git_log": _git_log,
        "git_branch": _git_branch,
        "execute_command": _execute_command,
        "get_system_info": _get_system_info,
        "list_processes": _list_processes,
        "kill_process": _kill_process,
        "ping_host": _ping_host,
        "run_tests": _run_tests,
        "execute_python": _execute_python,
        "analyze_code": _analyze_code,
        "semantic_search": _semantic_search,
        "grep_search": _grep_search,
        "query_database": _query_database,
        "browse_web": _browse_web,
        "clipboard_get": _clipboard_get,
        "zip_archive": _zip_archive,
        "web_search": _web_search,
        "generate_tests": _generate_tests,
        "code_review": _code_review,
        "fix_bug": _fix_bug,
        "plan_task": _plan_task,
        "get_project_context": _get_project_context,
        "system_dashboard": _system_dashboard,
        "run_tests_coverage": _run_tests_with_coverage,
        "analyze_security": _analyze_security,
        "backup_project": _backup_project,
    }

    async def _execute_tool(self, name: str, args: Dict) -> str:
        tool_info = self.ALLOWED_TOOLS.get(name)
        if not tool_info:
            return f"⛔ ابزار '{name}' مجاز نیست."
        if self.read_only and tool_info["risk"] != "low":
            return "⛔ حالت فقط‌خواندنی فعال است."
        write_tools = {"write_file", "edit_file", "delete_file", "move_file"}
        if name in write_tools:
            path = args.get("path") or args.get("source") or args.get("dest")
            if not path:
                return "❌ مسیر مشخص نشده است."
            # اگر full_access فعال باشد، نیازی به چک امنیتی نیست
            if not self.full_access and not self._is_safe_write_path(path):
                return (
                    f"⛔ دسترسی نوشتن در این مسیر مجاز نیست: {path}\n"
                    "فقط می‌توانید در پوشه‌های data/ و reports/ تغییر ایجاد کنید."
                )
        func = self.TOOL_MAP.get(name)
        if not func:
            return f"⛔ ابزار '{name}' پیاده‌سازی نشده."
        try:
            result = await func(self, **args)
            return str(result)
        except TypeError as e:
            return f"❌ پارامترهای اشتباه: {e}"
        except Exception as e:
            log_error(f"خطا در ابزار {name}: {e}")
            return f"❌ خطا در اجرای ابزار: {e}"

    # ─────────────────── Extended Thinking ────

    async def _think(self, task: str, context: str = "") -> str:
        if not self.client:
            return "🔍 (حالت آفلاین - تحلیل ساده)"
        prompt = (
            "You are a world-class software engineer. Think about this:\n\n"
            f"CONTEXT: {context}\n\nTASK: {task}\n\n"
            "Analyze: 1) REAL goal? 2) Edge cases? 3) Dependencies? "
            "4) Optimal solution? 5) Testing strategy?\n"
            "Provide concise analysis then action plan."
        )
        try:
            r = self.client.chat.completions.create(
                model="deepseek-reasoner",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2048,
            )
            return r.choices[0].message.content.strip()
        except Exception as e:
            log_warning(f"Thinking failed: {e}")
            return ""

    # ─────────────────── حالت‌ها و سیستم‌پرامپت ────

    def _select_model(self, task: str) -> str:
        coding_pats = [
            r"\b(کد|بنویس|ساخت|develop|write|create|build|python|تابع|کلاس)\b",
            r"\b(refactor|debug|fix|test|api|database|sql|query)\b",
            r"\b(رفع|اشکال|خطا|error|bug|issue)\b",
        ]
        if any(re.search(p, task, re.I) for p in coding_pats) or len(task) > 200:
            return "deepseek-reasoner"
        return self.model

    def _build_system_prompt(self, mode: str) -> str:
        prompts = {
            "code": "You are a SENIOR SOFTWARE ENGINEER. Write PRODUCTION-READY code with type hints, docstrings, error handling. Follow SOLID principles. Include tests.",
            "chat": "You are a FRIENDLY AI ASSISTANT. Have natural conversations. Be helpful and clear.",
            "analyze": "You are a CODE ANALYST EXPERT. Find bugs, vulnerabilities, code smells. Suggest improvements.",
            "precise": "You are a world-class software engineer. Provide COMPLETE, PRODUCTION-READY code with full documentation. Consider all edge cases.",
        }
        return prompts.get(mode, prompts["precise"])

    # ─────────────────── حلقهٔ اصلی Agent ────

    async def agent(self, task: str, mode: str = "precise") -> str:
        if self.busy:
            return "⏳ مغز در حال انجام کار دیگری است..."
        self.busy = True
        self.stats["agents_run"] += 1
        try:
            cached = self.agent_cache.get(task, self.model)
            if cached:
                log_info("💡 پاسخ از کش Agent بازیابی شد")
                return cached
            self.memory.add("user", task)
            await self._emit("command", {"task": task, "mode": mode})
            thinking = await self._think(task)
            if thinking:
                log_info(f"💭 تحلیل: {thinking[:200]}...")
            selected_model = self._select_model(task)
            system_prompt = self._build_system_prompt(mode)
            context_history = self.memory.get_context(task)
            messages: List[Dict] = (
                [
                    {"role": "system", "content": system_prompt},
                ]
                + context_history
                + [
                    {"role": "user", "content": task},
                ]
            )
            if any(kw in task for kw in ["فایل", "کد", "خطا", "پروژه", "کلاس", "تابع"]):
                search_res = self.semantic_search.search(task, n_results=3)
                if search_res:
                    ctx = "فایل‌های مرتبط:\n" + "\n".join(
                        [f"- {r['path']}: {r['content'][:200]}" for r in search_res]
                    )
                    messages.insert(1, {"role": "system", "content": ctx})
            step = 0
            final_answer = ""
            while step < MAX_AGENT_STEPS:
                step += 1
                log_info(f"  ⚙️ گام {step}/{MAX_AGENT_STEPS}")
                if not self.client:
                    final_answer = self._offline_agent(task)
                    break
                try:
                    response = self.client.chat.completions.create(
                        model=selected_model,
                        messages=messages,
                        tools=self.tools_definition,
                        temperature=0.1,
                        max_tokens=4096,
                    )
                except Exception as e:
                    log_error(f"API error: {e}")
                    if selected_model != "deepseek-chat":
                        selected_model = "deepseek-chat"
                        continue
                    final_answer = f"❌ خطای API: {e}"
                    break
                msg = response.choices[0].message
                if not msg.tool_calls:
                    final_answer = msg.content or "✅ عملیات تکمیل شد."
                    messages.append({"role": "assistant", "content": final_answer})
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
                    args = (
                        json.loads(tc.function.arguments)
                        if tc.function.arguments
                        else {}
                    )
                    log_info(f"     🔧 {name}: {str(args)[:120]}")
                    result = await self._execute_tool(name, args)
                    log_info(f"     📤 {result[:200]}...")
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
                        test_res = await self._auto_run_tests(args.get("path", ""))
                        if test_res:
                            messages.append(
                                {
                                    "role": "system",
                                    "content": f"🧪 تست خودکار:\n{test_res}",
                                }
                            )
            final_answer = final_answer or f"⚠️ به سقف گام ({MAX_AGENT_STEPS}) رسید."
            self.agent_cache.set(task, final_answer, self.model)
            self.memory.add("assistant", final_answer)
            await self._emit(
                "response", {"task": task[:100], "answer": final_answer[:200]}
            )
            self.cache.save()
            self.agent_cache.save()
            success = "❌" not in final_answer and "خطا" not in final_answer
            self.learning_engine.record_interaction(
                user_input=task,
                response=final_answer,
                success=success,
                details={"tools_used": [], "steps": step, "model": selected_model},
            )
            log_success(f"✅ Agent کامل شد. {step} گام")
            return final_answer
        except Exception as e:
            self.stats["errors"] += 1
            log_error(f"Agent error: {e}")
            return f"❌ خطای غیرمنتظره: {e}"
        finally:
            self.busy = False

    def _offline_agent(self, task: str) -> str:
        t = task.strip().lower()
        for cmd, action in self.ALLOWED_DIRECT_COMMANDS.items():
            if cmd in t:
                if action == "greeting":
                    return "سلام! مغز هوشمند ربات پایش سرنخ آماده است. برای دستورات پیشرفته، کلید API را تنظیم کنید."
                elif action == "status":
                    return self.get_status()
                elif action == "help":
                    return self.get_help()
        if "فایل" in t and ("خوان" in t or "باز" in t):
            return "🔍 در حالت آفلاین، لطفاً از /agent یا دستورات مستقیم استفاده کنید."
        if "سیستم" in t or "وضعیت" in t:
            return self.get_status()
        return (
            "🔍 در حالت آفلاین هستم. می‌توانم:\n"
            "- /agent <task> : اجرای Agent (نیاز به API)\n"
            "- /status : وضعیت سیستم\n"
            "- /help : راهنما\n"
            "لطفاً کلید API را تنظیم کنید: agent.set_api_key('sk-...')"
        )

    async def _auto_run_tests(self, file_path: str) -> str:
        if not file_path:
            return ""
        p = Path(file_path)
        candidates = [
            p.parent / f"test_{p.name}",
            Path("tests") / f"test_{p.name}",
            Path("test") / f"test_{p.name}",
        ]
        for tf in candidates:
            if tf.exists():
                return await self._run_tests(str(tf))
        return ""

    # ─────────────────── API عمومی برای admin_handler ────

    async def process_admin_command(self, text: str) -> str:
        self.stats["commands_processed"] += 1
        log_info(f"👤 دستور مدیر: {text[:100]}")
        t = text.strip().lower()
        if t in ["/status", "وضعیت", "status"]:
            return self.get_status()
        if t in ["/help", "راهنما", "help", "/راهنما"]:
            return self.get_help()
        if t in ["/clear", "پاک", "clear"]:
            self.cache.clear()
            self.agent_cache.clear()
            self.memory.clear()
            return "🗑️ کش و حافظه پاک شد."
        if t in ["/stats", "آمار", "stats"]:
            return self.get_stats()
        if t.startswith("/mode ") or t.startswith("حالت "):
            mode = t.split(maxsplit=1)[1]
            self.set_mode(mode)
            return f"✅ حالت '{mode}' فعال شد."
        if AI_ENABLED:
            try:
                from core.ai_assistant import get_ai_assistant

                ai = get_ai_assistant()
                if await ai.available():
                    intent = await ai.parse_intent(text)
                    action = intent.get("action", "unknown")
                    args = intent.get("args", {})
                    result = await self._handle_action(action, args, text)
                    if result:
                        return result
            except Exception as e:
                log_warning(f"AI assistant error: {e}")
        if self.client:
            return await self.agent(text)
        else:
            return self._offline_agent(text)

    async def _handle_action(
        self, action: str, args: Dict, original_text: str
    ) -> Optional[str]:
        try:
            if action == "status":
                return self.get_status()
            elif action == "health":
                return await self._get_health_report()
            elif action == "help":
                return self.get_help()
            elif action == "keywords":
                return await self._get_keywords_report()
            elif action == "platform_status":
                platform = args.get("value", "")
                return await self._get_platform_status(platform)
            elif action == "report":
                return await self._get_report()
            elif action == "crm":
                return "📊 خروجی CRM را از پنل اصلی (panel.py) بگیرید."
            elif action == "logs":
                return self._get_recent_logs()
            elif action == "lead_details":
                which = args.get("value", "last")
                return await self._get_lead_details(which)
            elif action == "add_keyword":
                kw = args.get("value", "")
                return (
                    f"🔧 افزودن کلیدواژه '{kw}' - از پنل admin/keywords استفاده کنید."
                )
            elif action == "remove_keyword":
                kw = args.get("value", "")
                return f"🔧 حذف کلیدواژه '{kw}' - از پنل admin/keywords استفاده کنید."
            elif action == "has_keyword":
                kw = args.get("value", "")
                return await self._check_keyword(kw)
            elif action in ("ask", "feature_request"):
                return await self.agent(original_text)
            elif action == "unknown":
                return None
            else:
                return None
        except Exception as e:
            log_error(f"Handler error for action {action}: {e}")
            return None

    # ─────────────────── متدهای گزارش‌دهی ────

    def get_status(self) -> str:
        mem = self.memory
        return (
            f"🧠 **وضعیت مغز هوشمند**\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🔹 مدل: {self.model}\n"
            f"🔹 حالت: {self.current_mode}\n"
            f"🔹 کلاینت: {'✅ فعال' if self.client else '❌ آفلاین'}\n"
            f"🔹 فقط‌خواندنی: {'✅' if self.read_only else '❌'}\n"
            f"🔹 دسترسی کامل: {'✅' if self.full_access else '❌'}\n"
            f"🔹 حافظه معنایی: {'✅' if mem.collection else '❌ (ChromaDB غیرفعال)'}\n"
            f"🔹 حافظه API: {'✅' if mem.api_memory else '❌'}\n"
            f"🔹 Semantic Search: {'✅' if self.semantic_search.collection else '❌'}\n"
            f"🔹 حافظه کوتاه: {len(mem.short_term)} پیام | خلاصه: {len(mem.summaries)} عدد\n"
            f"🔹 کش: {self.cache.stats()['entries']} مدخل\n"
            f"🔹 دستورات پردازش‌شده: {self.stats['commands_processed']}\n"
            f"🔹 Agent اجرا شده: {self.stats['agents_run']}\n"
            f"🔹 خطاها: {self.stats['errors']}\n"
            f"🔹 شروع کار: {self.stats['started_at']}\n"
            f"🔹 یادگیری: {len(self.learning_engine.successful_interactions)} موفق, {len(self.learning_engine.failed_interactions)} ناموفق"
        )

    def get_help(self) -> str:
        return (
            "📚 **راهنمای مغز هوشمند**\n"
            "━━━━━━━━━━━━━━━\n\n"
            "**💬 دستورات مستقیم:**\n"
            "• /status یا «وضعیت» → وضعیت سیستم\n"
            "• /help یا «راهنما» → این راهنما\n"
            "• /stats یا «آمار» → آمار مغز\n"
            "• /clear یا «پاک» → پاک‌سازی کش\n"
            "• /mode <precise|code|chat|analyze> → تغییر حالت\n\n"
            "**🤖 Agent خودکار (نیاز به API):**\n"
            "• «فایل x را بخوان» → خواندن فایل\n"
            "• «کد من را تحلیل کن» → تحلیل با AST\n"
            "• «در کد دنبال x بگرد» → grep جستجو\n"
            "• «تست‌ها را اجرا کن» → pytest\n"
            "• «سیستم را بررسی کن» → وضعیت CPU/RAM\n\n"
            "**📊 گزارش‌ها:**\n"
            "• «گزارش بده» → گزارش روزانه\n"
            "• «آمار کلیدواژه‌ها» → keywords\n"
            "• «سلامت سیستم» → health\n\n"
            "💡 *برای دستورات پیشرفته، از /agent <task> استفاده کنید.*"
        )

    def get_stats(self) -> str:
        return (
            f"📊 **آمار مغز**\n"
            f"━━━━━━━━━━\n"
            f"🔹 دستورات پردازش‌شده: {self.stats['commands_processed']}\n"
            f"🔹 Agent اجرا شده: {self.stats['agents_run']}\n"
            f"🔹 خطاهای ثبت‌شده: {self.stats['errors']}\n"
            f"🔹 حافظه کوتاه‌مدت: {len(self.memory.short_term)} پیام\n"
            f"🔹 خلاصه‌های حافظه: {len(self.memory.summaries)}\n"
            f"🔹 کش: {self.cache.stats()['entries']} مدخل\n"
            f"🔹 مدت فعالیت: {(datetime.now() - datetime.fromisoformat(self.stats['started_at'])).total_seconds() / 60:.1f} دقیقه"
        )

    def set_mode(self, mode: str):
        valid = {
            "precise",
            "debug",
            "architect",
            "concise",
            "creative",
            "code",
            "chat",
            "analyze",
        }
        if mode in valid:
            self.current_mode = mode
            log_success(f"✅ حالت '{mode}' فعال شد")

    async def _get_health_report(self) -> str:
        try:
            from core.health_check import HealthChecker

            if self.db:
                hc = HealthChecker(self.db)
                report = await hc.check_all()
                if hasattr(report, "__dict__"):
                    return json.dumps(report.__dict__, ensure_ascii=False, indent=2)[
                        :3000
                    ]
                return str(report)[:3000]
        except Exception as e:
            return f"❌ خطا در گزارش سلامت: {e}"
        return "⚠️ HealthChecker در دسترس نیست."

    async def _get_keywords_report(self) -> str:
        if not self.db:
            return "⚠️ دیتابیس متصل نیست."
        try:
            stats = await self.db.get_keyword_stats(limit=30)
            if not stats:
                return "📭 هیچ کلیدواژه‌ای ثبت نشده است."
            lines = ["📊 **آمار کلیدواژه‌ها**", "━━━━━━━━━━━━━━━"]
            for kw, count in stats[:20]:
                lines.append(f"• {kw}: {count} بار")
            if len(stats) > 20:
                lines.append(f"... و {len(stats)-20} کلیدواژه دیگر")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _get_platform_status(self, platform: str) -> str:
        if not self.db:
            return "⚠️ دیتابیس متصل نیست."
        try:
            last_poll = await self.db.get_last_poll(platform)
            if not last_poll:
                return f"📡 **{platform}**: هنوز داده‌ای ثبت نشده."
            ago = (datetime.now() - last_poll).total_seconds()
            if ago < 60:
                return f"📡 **{platform}**: فعال ✅ (لحظاتی پیش)"
            elif ago < 3600:
                return f"📡 **{platform}**: فعال ✅ ({ago/60:.0f} دقیقه پیش)"
            else:
                return f"📡 **{platform}**: ⚠️ ({ago/3600:.1f} ساعت پیش)"
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _get_report(self) -> str:
        if not self.db:
            return "⚠️ دیتابیس متصل نیست."
        try:
            total = await self.db.get_total_count()
            daily = await self.db.get_daily_stats(days=7)
            lines = [
                "📊 **گزارش سریع**",
                "━━━━━━━━━━━━",
                f"🔹 کل پیام‌ها: {total.get('messages', 0)}",
                f"🔹 سرنخ‌ها: {total.get('leads', 0)}",
                f"🔹 گروه‌ها: {total.get('groups', 0)}",
                "━━━━━━━━━━━━",
                "📅 **۷ روز اخیر:**",
            ]
            for d in daily[:7]:
                lines.append(f"  {d.get('date', '?'):12s}: {d.get('count', 0)} پیام")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ خطا: {e}"

    def _get_recent_logs(self) -> str:
        log_file = Path(LOGS_DIR)
        if not log_file.exists():
            return "📭 پوشه لاگ وجود ندارد."
        logs = list(log_file.glob("*.log")) + list(log_file.glob("*.txt"))
        if not logs:
            return "📭 فایل لاگی یافت نشد."
        latest = max(logs, key=lambda p: p.stat().st_mtime)
        try:
            lines = latest.read_text(encoding="utf-8").splitlines()
            recent = lines[-30:] if len(lines) > 30 else lines
            return f"📋 **آخرین لاگ‌ها ({latest.name}):**\n" + "\n".join(recent)
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _get_lead_details(self, which: str = "last") -> str:
        if not self.db:
            return "⚠️ دیتابیس متصل نیست."
        try:
            leads = await self.db.get_high_score_leads(min_score=0, limit=1)
            if not leads:
                return "📭 هیچ سرنخی ثبت نشده."
            lead = leads[0]
            return (
                f"📌 **جزئیات سرنخ**\n"
                f"━━━━━━━━━━━━━\n"
                f"🆔 شناسه: {lead.get('id', '?')[:20]}...\n"
                f"💬 پلتفرم: {lead.get('platform', '?')}\n"
                f"👤 چت: {lead.get('chat_name', '?')}\n"
                f"📝 متن: {str(lead.get('text', ''))[:200]}\n"
                f"🏷️ کلیدواژه: {lead.get('keywords', '')}\n"
                f"⭐ امتیاز: {lead.get('opportunity_score', 0)}\n"
                f"🕐 زمان: {lead.get('timestamp', '?')}"
            )
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _check_keyword(self, kw: str) -> str:
        if not kw:
            return "⚠️ لطفاً کلیدواژه را وارد کنید."
        try:
            from analyzers.keyword_finder import get_keyword_finder

            finder = get_keyword_finder()
            if finder.has_match(kw):
                return f"✅ کلیدواژه '{kw}' در سیستم وجود دارد."
            return f"❌ کلیدواژه '{kw}' در سیستم یافت نشد."
        except Exception as e:
            return f"❌ خطا: {e}"

    # ─────────────── متدهای جدید از bot.py ──────────

    def generate_report(self, days: int = 7) -> str:
        report = []
        report.append("📊 **گزارش عملکرد Agent**")
        report.append("=" * 50)
        report.append(f"🔹 تعداد دستورات: {self.stats['commands_processed']}")
        report.append(f"🔹 تعداد Agent: {self.stats['agents_run']}")
        report.append(f"🔹 خطاها: {self.stats['errors']}")
        report.append(
            f"🔹 یادگیری: {len(self.learning_engine.successful_interactions)} موفق, {len(self.learning_engine.failed_interactions)} ناموفق"
        )
        return "\n".join(report)

    def analyze_logs(self, category: str = None) -> str:
        return "📋 تحلیل لاگ‌ها: (قابلیت در حال توسعه)"

    def build_from_map(self, task: str, max_retries: int = 3) -> str:
        return "🏗️ ساخت پروژه از نقشه: (قابلیت در حال توسعه)"

    def self_upgrade(self, upgrade_request: str = "بهبود کلی کد") -> str:
        return "🔄 خودارتقا: (قابلیت در حال توسعه)"


# ─── Singleton ───

_brain_instance: Optional[Brain] = None


def get_brain(
    event_bus: Optional[EventBus] = None,
    db: Optional[Any] = None,
    read_only: bool = False,
    api_key: Optional[str] = None,
    full_access: bool = False,
    use_api_memory: bool = False,
    memory_api_url: str = "",
    memory_api_key: str = "",
) -> Brain:
    global _brain_instance
    if _brain_instance is None:
        _brain_instance = Brain(
            event_bus=event_bus,
            db=db,
            read_only=read_only,
            api_key=api_key,
            full_access=full_access,
            use_api_memory=use_api_memory,
            memory_api_url=memory_api_url,
            memory_api_key=memory_api_key,
        )
    else:
        if event_bus and not _brain_instance.event_bus:
            _brain_instance.set_event_bus(event_bus)
        if db and not _brain_instance.db:
            _brain_instance.set_database(db)
        # به‌روزرسانی پرچم‌ها در صورت نیاز
        if full_access:
            _brain_instance.full_access = full_access
        if use_api_memory and memory_api_url:
            # در صورت نیاز، حافظه API را دوباره مقداردهی کن
            pass
    return _brain_instance
