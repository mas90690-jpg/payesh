# -*- coding: utf-8 -*-
"""
core/safe_brain.py — مغز امن ربات پایش سرنخ
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
یک لایهٔ امن (Safe Wrapper) روی Brain که:

- فقط دستورات مجاز (whitelist) را اجرا می‌کند
- از کش (SmartCache) و حافظه (project_memory, global_memory) برای کاهش هزینه استفاده می‌کند
- دستورات مخرب (حذف فایل، اجرای دستور، ویرایش بحرانی) را بلاک می‌کند
- برای پاسخ‌های ساده از AIAssistant.chat() استفاده می‌کند (کاهش هزینه)
- برای کارهای پیچیده از Brain.agent() استفاده می‌کند
- پشتیبانی از حالت full_access برای دور زدن کامل محدودیت‌های امنیتی
- پشتیبانی از حافظه API (هماهنگ با brain.py)

متد اصلی:
    process(text: str) -> str: پردازش متن و برگرداندن پاسخ امن
"""

import os
import json
import time
import hashlib
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path

from config import BASE_DIR, STORAGE_DIR, AI as AI_CONFIG, AI_ENABLED
from core.logger import (
    log_info,
    log_success,
    log_warning,
    log_error,
    get_logger,
)

# ════════════════════════════════════════════
# مسیرهای ذخیره‌سازی حافظه و کش
# ════════════════════════════════════════════
SAFE_BRAIN_DIR = Path(STORAGE_DIR) / "safe_brain"
SAFE_MEMORY_DIR = SAFE_BRAIN_DIR / "memory"
SAFE_CACHE_DIR = SAFE_BRAIN_DIR / "cache"
for d in [SAFE_BRAIN_DIR, SAFE_MEMORY_DIR, SAFE_CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ════════════════════════════════════════════
# ثابت‌های امنیتی
# ════════════════════════════════════════════

# فقط این اکشن‌ها مجاز هستند (whitelist) — در حالت full_access نادیده گرفته می‌شوند
SAFE_ACTIONS = frozenset(
    {
        "status",
        "report",
        "crm",
        "logs",
        "keywords",
        "add_keyword",
        "remove_keyword",
        "has_keyword",
        "platform_status",
        "health",
        "lead_details",
        "get_param",
        "set_param",
        "help",
        "restart",
        "explain_logs",
        "feature_request",
        "ask",
    }
)

# دستورات سطح بالا که خواندنی/اطلاعاتی هستند (نیاز به تأیید ندارند)
READONLY_ACTIONS = frozenset(
    {
        "status",
        "help",
        "keywords",
        "has_keyword",
        "platform_status",
        "health",
        "lead_details",
        "get_param",
        "logs",
        "explain_logs",
    }
)

# ابزارهای مجاز برای Agent (فقط خواندنی + git_status/git_log)
SAFE_TOOLS = frozenset(
    {
        # ─── خواندنی (همه‌جا) ───
        "read_file",
        "list_directory",
        "git_status",
        "git_diff",
        "git_log",
        "get_system_info",
        "list_processes",
        "ping_host",
        "analyze_code",
        "semantic_search",
        "grep_search",
        "query_database",
        "browse_web",
        "clipboard_get",
        "zip_archive",
        # ─── نوشتنی (فقط در پوشه‌های مجاز) ───
        "write_file",
        "edit_file",
        "delete_file",
        "move_file",
        "git_commit",
        "git_branch",
        "execute_command",  # با محدودیت
    }
)

# کلیدواژه‌های ممنوعه (حتی داخل متن) — نمی‌گذاریم Agent این کارها را بکند
# در حالت full_access نادیده گرفته می‌شوند
BLOCKED_PATTERNS = [
    "rm -rf",
    "rmdir /",
    "del /f",
    "format ",
    "mkfs",
    "dd if=",
    "shutdown",
    "reboot",
    "poweroff",
    "init 0",
    "init 6",
    "DROP TABLE",
    "DROP DATABASE",
    "DELETE FROM",
    "TRUNCATE",
    "os.system",
    "subprocess.call",
    "subprocess.Popen",
    "eval(",
    "exec(",
    "__import__",
    "compile(",
]

# حداکثر تعداد دفعات تماس با API (محدودیت هزینه) — در full_access نادیده گرفته می‌شود
MAX_API_CALLS_PER_HOUR = 20


# ════════════════════════════════════════════
# حافظهٔ سبک برای SafeBrain (جلوگیری از تکرار)
# ════════════════════════════════════════════


class SafeMemory:
    """حافظهٔ سبک محلی برای SafeBrain — ذخیره در فایل JSON"""

    def __init__(self, name: str = "safe_memory", max_entries: int = 100):
        self.filepath = SAFE_MEMORY_DIR / f"{name}.json"
        self.max_entries = max_entries
        self.memory: List[Dict] = []
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if self.filepath.exists():
            try:
                with self._lock:
                    self.memory = json.loads(self.filepath.read_text(encoding="utf-8"))
            except Exception:
                self.memory = []

    def _save(self):
        try:
            with self._lock:
                data = json.dumps(self.memory, ensure_ascii=False, indent=2)
            self.filepath.write_text(data, encoding="utf-8")
        except Exception as e:
            log_warning(f"SafeMemory save failed: {e}")

    def add(self, role: str, content: str):
        """افزودن یک ورودی به حافظه"""
        with self._lock:
            self.memory.append(
                {
                    "role": role,
                    "content": content[:500],
                    "timestamp": time.time(),
                }
            )
            if len(self.memory) > self.max_entries:
                self.memory = self.memory[-self.max_entries :]
        self._save()

    def get_recent(self, n: int = 5) -> List[Dict]:
        """دریافت n ورودی آخر"""
        with self._lock:
            return self.memory[-n:] if self.memory else []

    def get_context(self, n: int = 5) -> str:
        """متن زمینه از حافظه برای پرامپت"""
        recent = self.get_recent(n)
        if not recent:
            return ""
        lines = []
        for entry in recent:
            role = "کاربر" if entry["role"] == "user" else "دستیار"
            lines.append(f"{role}: {entry['content'][:200]}")
        return "\n".join(lines)

    def search(self, query: str, n: int = 3) -> List[Dict]:
        """جستجوی ساده (substring) در حافظه (بدون ChromaDB برای سبکی)"""
        results = []
        q = query.lower()
        with self._lock:
            for entry in reversed(self.memory):
                if q in entry["content"].lower():
                    results.append(entry)
                    if len(results) >= n:
                        break
        return results

    def clear(self):
        with self._lock:
            self.memory = []
        self._save()

    @property
    def count(self) -> int:
        with self._lock:
            return len(self.memory)


class SafeCache:
    """کش ساده با TTL برای SafeBrain — ذخیره در فایل JSON"""

    def __init__(self, name: str = "safe_cache", ttl_hours: int = 48):
        self.filepath = SAFE_CACHE_DIR / f"{name}.json"
        self.ttl = timedelta(hours=ttl_hours)
        self.cache: Dict[str, Dict] = {}
        self._lock = threading.Lock()
        self._load()

    def _key(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def _load(self):
        if self.filepath.exists():
            try:
                with self._lock:
                    self.cache = json.loads(self.filepath.read_text(encoding="utf-8"))
            except Exception:
                self.cache = {}

    def _save(self):
        try:
            with self._lock:
                data = json.dumps(self.cache, ensure_ascii=False)
            self.filepath.write_text(data, encoding="utf-8")
        except Exception as e:
            log_warning(f"SafeCache save failed: {e}")

    def get(self, text: str) -> Optional[str]:
        key = self._key(text)
        with self._lock:
            entry = self.cache.get(key)
        if entry:
            age = datetime.now() - datetime.fromtimestamp(entry["timestamp"])
            if age < self.ttl:
                return entry["answer"]
        return None

    def set(self, text: str, answer: str):
        key = self._key(text)
        with self._lock:
            self.cache[key] = {"answer": answer, "timestamp": time.time()}
            if len(self.cache) > 500:
                sorted_items = sorted(
                    self.cache.items(), key=lambda x: x[1]["timestamp"]
                )
                self.cache = dict(sorted_items[-400:])
        self._save()

    def clear(self):
        with self._lock:
            self.cache = {}
        self._save()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self.cache)


# ════════════════════════════════════════════
# کلاس اصلی SafeBrain
# ════════════════════════════════════════════


class SafeBrain:
    """
    مغز امن ربات — لایهٔ محافظ روی Brain

    این کلاس:
    - فقط دستورات مجاز (whitelist) را اجرا می‌کند
    - دستورات مخرب را بلاک می‌کند
    - از کش و حافظه استفاده می‌کند تا هزینه API کاهش یابد
    - برای سؤال‌های ساده از AIAssistant.chat() (Ollama) استفاده می‌کند
    - برای کارهای پیچیده از Brain.agent() استفاده می‌کند
    - در حالت full_access، تمام محدودیت‌های امنیتی را دور می‌زند
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        brain: Optional[Any] = None,
        full_access: bool = False,
        use_api_memory: bool = False,
        memory_api_url: str = "",
        memory_api_key: str = "",
    ):
        # ─── کلید API ───
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")

        # ─── پرچم دسترسی کامل ───
        self.full_access = full_access

        # ─── حافظه و کش ───
        self.project_memory = SafeMemory("project_memory", max_entries=200)
        self.global_memory = SafeMemory("global_memory", max_entries=500)
        self.cache = SafeCache("safe_brain_cache", ttl_hours=48)

        # ─── آمار مصرف API ───
        self._api_calls_this_hour = 0
        self._api_hour_start = time.time()

        # ─── مغز اصلی (Brain) ───
        self._brain = brain
        self._brain_instance = None  # lazy init
        self._ai_assistant = None  # lazy init

        # ─── تنظیمات حافظه API برای Brain ───
        self.use_api_memory = use_api_memory
        self.memory_api_url = memory_api_url
        self.memory_api_key = memory_api_key

        # ─── وضعیت ───
        self.stats: Dict[str, Any] = {
            "processed": 0,
            "cached": 0,
            "api_calls": 0,
            "blocked": 0,
            "errors": 0,
            "started_at": datetime.now().isoformat(),
        }

        log_success(
            "🧠 SafeBrain آماده است | API: {} فعال | full_access: {}".format(
                "✅" if self.api_key else "❌", "✅" if self.full_access else "❌"
            )
        )

    # ────────────────── مقداردهی lazy ───────────────────

    def _get_brain(self):
        """دریافت نمونهٔ Brain (lazy) با پاس دادن پارامترهای جدید"""
        if self._brain_instance is None:
            try:
                from core.brain import Brain

                self._brain_instance = Brain(
                    api_key=self.api_key,
                    read_only=False,  # SafeBrain به‌طور پیش‌فرض فقط‌خواندنی نیست
                    full_access=self.full_access,
                    use_api_memory=self.use_api_memory,
                    memory_api_url=self.memory_api_url,
                    memory_api_key=self.memory_api_key,
                )
                log_success("🧠 مغز اصلی (Brain) راه‌اندازی شد")
            except Exception as e:
                log_error(f"❌ خطا در راه‌اندازی مغز: {e}")
                return None
        return self._brain_instance

    def _get_ai(self):
        """دریافت نمونهٔ AIAssistant (lazy)"""
        if self._ai_assistant is None:
            try:
                from core.ai_assistant import get_ai_assistant

                self._ai_assistant = get_ai_assistant()
            except Exception as e:
                log_warning(f"⚠️ AIAssistant در دسترس نیست: {e}")
                return None
        return self._ai_assistant

    # ────────────────── کنترل مصرف API ───────────────────

    def _can_call_api(self) -> bool:
        """بررسی محدودیت تماس با API (بیش از N بار در ساعت) — در full_access نادیده گرفته می‌شود"""
        if self.full_access:
            return True
        now = time.time()
        if now - self._api_hour_start > 3600:
            self._api_calls_this_hour = 0
            self._api_hour_start = now
        if self._api_calls_this_hour >= MAX_API_CALLS_PER_HOUR:
            log_warning(
                "⚠️ محدودیت ساعتی API رسید ({}). استفاده از کش/حافظه.".format(
                    MAX_API_CALLS_PER_HOUR
                )
            )
            return False
        self._api_calls_this_hour += 1
        self.stats["api_calls"] += 1
        return True

    # ────────────────── بررسی امنیت ───────────────────

    def _is_safe(self, text: str) -> Tuple[bool, str]:
        """
        بررسی امنیت متن ورودی.
        در حالت full_access، همیشه (True, "") برمی‌گرداند.
        Returns: (is_safe, reason)
        """
        if self.full_access:
            return True, ""

        low = text.lower()

        # الگوهای ممنوعه
        for pattern in BLOCKED_PATTERNS:
            if pattern.lower() in low:
                return False, f"الگوی ممنوعه: {pattern}"

        # دستورات مخرب سیستم
        dangerous_commands = [
            "rm -rf",
            "rmdir /s",
            "del /f /s",
            "format ",
            "shutdown",
            "reboot",
            "init 0",
            ":(){ :|:& };:",
            "dd if=",
            "> /dev/sda",
            "mkfs.",
            "chmod 777",
        ]
        for cmd in dangerous_commands:
            if cmd in low:
                return False, f"دستور مخرب سیستم: {cmd}"

        return True, ""

    def _is_destructive_action(self, action: str) -> bool:
        """آیا این اکشن مخرب است؟ در full_access همیشه False برمی‌گرداند."""
        if self.full_access:
            return False
        return action not in READONLY_ACTIONS and action not in {
            "add_keyword",
            "remove_keyword",
            "set_param",
        }

    # ────────────────── پردازش اصلی ───────────────────

    def _is_safe_write_path(self, path_str: str) -> bool:
        """
        بررسی مسیر برای نوشتن.
        در حالت full_access، همه‌ی مسیرها مجاز هستند.
        """
        if self.full_access:
            return True

        from pathlib import Path
        from config import BASE_DIR

        try:
            p = Path(path_str).resolve()
            base = Path(BASE_DIR).resolve()
        except Exception:
            return False

        # اگر مسیر خارج از پروژه باشد، ممنوع
        if not str(p).startswith(str(base)):
            return False

        # مسیرهای مجاز برای نوشتن
        allowed_prefixes = [
            base / "data",
            base / "storage" / "reports",
            base / "storage" / "database",  # فقط برای بکاپ‌گیری دستی (اختیاری)
        ]
        for allowed in allowed_prefixes:
            if str(p).startswith(str(allowed)):
                return True

        # مسیرهای ممنوع (کدهای اصلی)
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

        # پیش‌فرض: فقط خواندنی
        return False

    async def process(self, text: str) -> str:
        """
        پردازش امن متن و برگرداندن پاسخ.

        Args:
            text: متن ورودی (دستور یا سؤال)

        Returns:
            str: پاسخ امن
        """
        text = (text or "").strip()
        if not text:
            return ""

        self.stats["processed"] += 1
        log_info(
            f"🧠 SafeBrain.process: {text[:100]} | full_access: {self.full_access}"
        )

        # ── 1. بررسی امنیت (در full_access همیشه قبول می‌شود) ──
        safe, reason = self._is_safe(text)
        if not safe:
            self.stats["blocked"] += 1
            log_warning(f"⛔ درخواست مسدود شد: {reason}")
            return f"⛔ درخواست مسدود شد: {reason}"

        # ── 2. بررسی کش ──
        cached = self.cache.get(text)
        if cached:
            self.stats["cached"] += 1
            log_info("💡 پاسخ از کش بازیابی شد")
            return cached

        # ── 3. بررسی حافظه ──
        memory_results = self.global_memory.search(text, n=1)
        if memory_results:
            log_info("💡 پاسخ مشابه در حافظه یافت شد")

        # ── 4. تشخیص نوع درخواست ──
        action, value = self._rule_parse(text)

        # ── 5. اگر دستور شناخته شده است، مستقیم اجرا کن ──
        if action is not None:
            # در full_access، همه‌ی اکشن‌ها مجاز هستند
            if self.full_access or action in SAFE_ACTIONS:
                result = await self._execute_safe_action(action, value, text)
                if result:
                    # ذخیره در کش و حافظه
                    self.cache.set(text, result)
                    self.global_memory.add("assistant", result)
                    return result
            else:
                self.stats["blocked"] += 1
                log_warning(f"⛔ اکشن غیرمجاز: {action}")
                return f"⛔ اکشن '{action}' مجاز نیست."

        # ── 6. سؤال آزاد → تلاش با AIAssistant (Ollama) ──
        ai = self._get_ai()
        if ai and AI_CONFIG.get("enabled", True):
            try:
                # ابتدا امتحان کن با chat ساده جواب بده
                if await ai.available():
                    # زمینه از حافظه
                    context = self.global_memory.get_context(3)
                    system = (
                        "تو «مغز هوشمند ربات پایش سرنخ سپاهان رویش» هستی؛ یک دستیار داخلی ربات. "
                        "همیشه به فارسی، کوتاه، دقیق و حرفه‌ای جواب بده. "
                        "هرگز نگو که «کلود» یا یک مدل هوش مصنوعی بیرونی هستی؛ تو بخشی از این رباتی. "
                        "اگر چیزی را نمی‌دانی، صادقانه بگو.\n\n"
                    )
                    if context:
                        system += f"زمینه گفتگو:\n{context}\n\n"

                    ans = await ai.chat(text, system=system)
                    if ans:
                        # ذخیره در حافظه
                        self.global_memory.add("user", text)
                        self.global_memory.add("assistant", ans)
                        self.cache.set(text, ans)
                        return ans
            except Exception as e:
                log_warning(f"AIAssistant.chat failed: {e}")

        # ── 7. اگر API در دسترس است، از Brain.agent استفاده کن ──
        if self.api_key and self._can_call_api():
            brain = self._get_brain()
            if brain:
                try:
                    # اضافه کردن متن به حافظه
                    self.project_memory.add("user", text)
                    self.global_memory.add("user", text)

                    # اجرای Agent با محدودیت ابزارهای امن (در full_access همه‌ی ابزارها مجازند)
                    result = await brain.agent(text)

                    # ذخیره در حافظه
                    self.project_memory.add("assistant", result)
                    self.global_memory.add("assistant", result)
                    self.cache.set(text, result)

                    return result
                except Exception as e:
                    log_error(f"Brain.agent error: {e}")
                    self.stats["errors"] += 1

        # ── 8. Fallback نهایی ──
        return self._fallback_response(text)

    # ────────────────── تشخیص دستور با قاعده ───────────────────

    @staticmethod
    def _extract_value(text: str) -> Optional[str]:
        """استخراج مقدار داخل «» یا "" یا ''"""
        import re

        m = re.search(r"[«\"']([^»\"']+)[»\"']", text)
        return m.group(1).strip() if m else None

    @staticmethod
    def _platform_in(low: str) -> Optional[str]:
        """تشخیص نام پلتفرم در متن"""
        _PLATFORM_MAP = {
            "ایتا": "eitaa",
            "بله": "bale",
            "روبیکا": "rubika",
            "تلگرام": "telegram",
            "واتساپ": "whatsapp",
            "واتس‌اپ": "whatsapp",
            "لینکدین": "linkedin",
            "وب": "web",
            "eitaa": "eitaa",
            "bale": "bale",
            "rubika": "rubika",
            "telegram": "telegram",
            "whatsapp": "whatsapp",
            "linkedin": "linkedin",
            "web": "web",
        }
        for word, canon in _PLATFORM_MAP.items():
            if word in low:
                return canon
        return None

    def _rule_parse(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        تشخیص دستور با قاعده (بدون AI).
        Returns: (action, value) — اگر action=None باشد یعنی دستور ناشناخته است.
        """
        low = text.lower()
        val = self._extract_value(text)

        if any(w in low for w in ["help", "راهنما", "کمک", "دستورات", "منو"]):
            return "help", None
        if any(w in low for w in ["restart", "ری‌استارت", "ریستارت", "راه‌اندازی مجدد"]):
            return "restart", None
        if ("حذف" in low or "remove" in low or "پاک" in low or "delete" in low) and (
            "کلیدواژه" in low or "keyword" in low or val
        ):
            return "remove_keyword", val
        if ("اضافه" in low or "add" in low) and (
            "کلیدواژه" in low or "keyword" in low or val
        ):
            return "add_keyword", val
        if ("دار" in low or "هست" in low or "has" in low or "exist" in low) and (
            "کلیدواژه" in low or "keyword" in low
        ):
            return "has_keyword", val
        if ("سرنخ" in low or "پایش" in low or "lead" in low) and any(
            w in low
            for w in [
                "جزئیات",
                "اولین",
                "آخرین",
                "detail",
                "first",
                "last",
                "نشون",
                "بگو",
            ]
        ):
            which = "first" if ("اولین" in low or "first" in low) else "last"
            return "lead_details", which
        if any(
            w in low
            for w in [
                "سلامت",
                "health",
                "چک کامل",
                "تندرستی",
                "همه پلتفرم",
                "وضعیت کامل",
            ]
        ):
            return "health", None
        _want = any(
            w in low
            for w in [
                "میخوام",
                "می‌خوام",
                "میخواهم",
                "می‌خواهم",
                "کاش",
                "اضافه کن",
                "اضاف کن",
            ]
        )
        _cap = any(w in low for w in ["بتون", "قابلیت", "feature", "توانایی"])
        if _want and _cap and "کلیدواژه" not in low:
            return "feature_request", None
        if any(w in low for w in ["کلیدواژه", "keyword"]):
            return "keywords", None
        if any(w in low for w in ["گزارش", "report"]):
            return "report", None
        if any(w in low for w in ["crm", "سی آر ام", "خروجی"]):
            return "crm", None
        if ("لاگ" in low or "log" in low) and any(
            w in low for w in ["چیه", "یعنی", "توضیح", "معنی", "explain", "چرا"]
        ):
            return "explain_logs", None
        if any(w in low for w in ["لاگ", "log"]):
            return "logs", None
        if any(
            w in low
            for w in [
                "وضعیت",
                "آمار",
                "اوضاع",
                "حال",
                "چطور",
                "سرنخ",
                "lead",
                "status",
                "stat",
            ]
        ) and not self._platform_in(low):
            return "status", None
        p = self._platform_in(low)
        if p and any(
            w in low for w in ["کار", "وصل", "working", "status", "?", "؟", "فعال"]
        ):
            return "platform_status", p

        return None, val

    # ────────────────── اجرای اکشن‌های امن ───────────────────

    async def _execute_safe_action(
        self, action: str, value: Optional[str], raw_text: str
    ) -> str:
        """اجرای اکشن‌های مجاز (امن) — در full_access همه‌ی اکشن‌ها اجرا می‌شوند"""
        try:
            if action == "help":
                return self._help()
            elif action == "status":
                return self._status()
            elif action == "keywords":
                return self._keywords()
            elif action == "has_keyword":
                return self._has_keyword(value or self._extract_value(raw_text) or "")
            elif action == "add_keyword":
                return self._add_keyword(value or self._extract_value(raw_text) or "")
            elif action == "remove_keyword":
                return self._remove_keyword(
                    value or self._extract_value(raw_text) or ""
                )
            elif action == "logs":
                return self._logs()
            elif action == "health":
                return "🩺 سلامت کامل: این ماژول در SafeBrain ساده شده است."
            elif action == "lead_details":
                return self._lead_details(value or "last")
            elif action == "platform_status":
                return self._platform_status(value or "bale")
            elif action == "restart":
                return "🔄 دستور ری‌استارت در SafeBrain مسدود شده است. از admin_handler استفاده کنید."
            elif action == "feature_request":
                return "📝 درخواست شما ثبت شد."
            elif action == "explain_logs":
                return await self._explain_logs()
            elif action == "report":
                return "📄 گزارش: از دستور /report در admin_handler استفاده کنید."
            elif action == "crm":
                return "📤 خروجی CRM: از دستور /crm در admin_handler استفاده کنید."
            else:
                # اگر full_access فعال باشد، هر اکشن دیگری را هم اجرا کن (با ریسک خودش)
                if self.full_access:
                    return f"⚠️ اکشن '{action}' در SafeBrain تعریف نشده، اما به دلیل full_access اجرا می‌شود. (از Brain.agent استفاده کنید)"
                return f"⚠️ اکشن '{action}' در SafeBrain پیاده‌سازی نشده است."
        except Exception as e:
            log_error(f"SafeBrain execute error: {e}")
            self.stats["errors"] += 1
            return f"❌ خطا در اجرا: {e}"

    # ────────────────── پاسخ‌های پیش‌فرض ───────────────────

    @staticmethod
    def _help() -> str:
        return (
            "🛡️ **SafeBrain — دستورات مجاز:**\n"
            "━━━━━━━━━━━━━━━\n"
            "• وضعیت / آمار\n"
            "• گزارش\n"
            "• لاگ\n"
            "• کلیدواژه‌ها\n"
            "• کلیدواژه «...» را اضافه کن\n"
            "• کلیدواژه «...» را داری؟\n"
            "• حذف کلیدواژه «...»\n"
            "• لینکدین کار می‌کند؟\n"
            "• سلامت کامل\n"
            "• جزئیات آخرین سرنخ\n"
            "• سؤال آزاد (هر چی می‌خواهی بپرس)\n\n"
            "💡 *همه دستورات امن هستند و هیچ تغییری در سیستم ایجاد نمی‌کنند.*"
        )

    def _status(self) -> str:
        """وضعیت SafeBrain"""
        return (
            f"🧠 **SafeBrain Status**\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🔹 پردازش‌شده: {self.stats['processed']}\n"
            f"🔹 پاسخ از کش: {self.stats['cached']}\n"
            f"🔹 تماس با API: {self.stats['api_calls']}\n"
            f"🔹 مسدودشده: {self.stats['blocked']}\n"
            f"🔹 خطاها: {self.stats['errors']}\n"
            f"🔹 حافظه پروژه: {self.project_memory.count} مدخل\n"
            f"🔹 حافظه سراسری: {self.global_memory.count} مدخل\n"
            f"🔹 کش: {self.cache.size} مدخل\n"
            f"🔹 شروع کار: {self.stats['started_at']}\n"
            f"🔹 دسترسی کامل: {'✅' if self.full_access else '❌'}"
        )

    def _keywords(self) -> str:
        """لیست کلیدواژه‌ها"""
        try:
            from analyzers.keyword_finder import get_keyword_finder

            kw = get_keyword_finder()
            ks = kw.keywords
            sample = "، ".join(ks[:15])
            return f"🔑 {len(ks)} کلیدواژه بارگذاری شده.\nنمونه: {sample}"
        except Exception as e:
            return f"⚠️ خطا: {e}"

    @staticmethod
    def _has_keyword(value: str) -> str:
        if not value:
            return "کدام کلیدواژه؟ داخل « » بنویس."
        try:
            from analyzers.keyword_finder import get_keyword_finder

            kw = get_keyword_finder()
            found = bool(kw.has_match(value) or value in kw.keywords)
            return f"{'✅ بله' if found else '❌ نه'}، «{value}» {'هست' if found else 'نیست'}."
        except Exception as e:
            return f"⚠️ خطا: {e}"

    @staticmethod
    def _add_keyword(value: str) -> str:
        if not value:
            return "کلیدواژه را داخل « » بنویس."
        try:
            import json
            from config import BASE_DIR

            path = Path(BASE_DIR) / "data" / "keywords_custom.json"
            data = []
            if path.exists():
                with open(str(path), "r", encoding="utf-8") as f:
                    data = json.load(f)
            if not isinstance(data, list):
                data = []
            if value in data:
                return f"«{value}» از قبل هست."
            data.append(value)
            with open(str(path), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return f"✅ «{value}» اضافه شد."
        except Exception as e:
            return f"❌ خطا: {e}"

    @staticmethod
    def _remove_keyword(value: str) -> str:
        if not value:
            return "کدام کلیدواژه؟ داخل « » بنویس."
        try:
            import json
            from config import BASE_DIR

            path = Path(BASE_DIR) / "data" / "keywords_custom.json"
            data = []
            if path.exists():
                with open(str(path), "r", encoding="utf-8") as f:
                    data = json.load(f)
            if not isinstance(data, list) or value not in data:
                return f"«{value}» در لیست کلیدواژه‌های سفارشی نیست."
            data = [k for k in data if k != value]
            with open(str(path), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return f"🗑️ «{value}» حذف شد."
        except Exception as e:
            return f"❌ خطا: {e}"

    def _logs(self, n: int = 10) -> str:
        """نمایش آخرین لاگ‌ها"""
        path = Path(STORAGE_DIR) / "logs" / "app.jsonl"
        if not path.exists():
            return "📭 لاگی موجود نیست."
        try:
            with open(str(path), "r", encoding="utf-8") as f:
                lines = f.readlines()[-n:]
            out = []
            for ln in lines:
                try:
                    o = json.loads(ln)
                    out.append(f"[{o.get('level','')}] {o.get('msg','')[:90]}")
                except Exception:
                    out.append(ln.strip()[:90])
            return "🗒️ آخرین لاگ‌ها:\n" + "\n".join(out)
        except Exception as e:
            return f"⚠️ خطا: {e}"

    async def _explain_logs(self) -> str:
        """توضیح لاگ‌ها با AI"""
        logs = self._logs(15)
        ai = self._get_ai()
        if ai and AI_CONFIG.get("enabled", True):
            try:
                if await ai.available():
                    prompt = (
                        f"این لاگ‌های ربات را خیلی ساده به فارسی توضیح بده که چه اتفاقی افتاده و "
                        f"اگر خطایی هست یعنی چه و چه باید کرد:\n\n{logs}"
                    )
                    ans = await ai.chat(prompt)
                    if ans:
                        return ans
            except Exception:
                pass
        return logs or "📭 لاگی موجود نیست."

    @staticmethod
    def _lead_details(which: str) -> str:
        """جزئیات سرنخ (ساده شده)"""
        return (
            f"🔎 {('اولین' if which == 'first' else 'آخرین')} سرنخ:\n"
            "برای مشاهده جزئیات کامل، به دیتابیس اصلی مراجعه کنید:\n"
            f"storage/database/messages.db"
        )

    @staticmethod
    def _platform_status(platform: str) -> str:
        """وضعیت ساده پلتفرم"""
        return f"ℹ️ وضعیت {platform}: برای اطلاعات دقیق از health checker استفاده کنید."

    def _fallback_response(self, text: str) -> str:
        """پاسخ نهایی در صورت عدم دسترسی به AI"""
        return (
            "🤖 SafeBrain:\n"
            "هوش مصنوعی در دسترس نیست. از دستورات مستقیم استفاده کنید:\n"
            "• «وضعیت» → آمار\n"
            "• «راهنما» → دستورات\n"
            "• «کلیدواژه‌ها» → لیست\n"
            "• «سلامت» → بررسی سیستم"
        )


# ════════════════════════════════════════════
# Singleton
# ════════════════════════════════════════════

_shared_safe_brain: Optional[SafeBrain] = None


def get_safe_brain(
    api_key: Optional[str] = None,
    full_access: bool = False,
    use_api_memory: bool = False,
    memory_api_url: str = "",
    memory_api_key: str = "",
) -> SafeBrain:
    """دریافت نمونهٔ Singleton SafeBrain با پارامترهای جدید"""
    global _shared_safe_brain
    if _shared_safe_brain is None:
        _shared_safe_brain = SafeBrain(
            api_key=api_key,
            full_access=full_access,
            use_api_memory=use_api_memory,
            memory_api_url=memory_api_url,
            memory_api_key=memory_api_key,
        )
    else:
        # به‌روزرسانی پرچم‌ها در صورت نیاز
        if full_access:
            _shared_safe_brain.full_access = full_access
        if use_api_memory and memory_api_url:
            _shared_safe_brain.use_api_memory = use_api_memory
            _shared_safe_brain.memory_api_url = memory_api_url
            _shared_safe_brain.memory_api_key = memory_api_key
    return _shared_safe_brain
