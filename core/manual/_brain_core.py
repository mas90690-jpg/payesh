# core/manual/_brain_core.py
"""
هستهٔ ManualBrain — helpers, init, load/save, stats, templates.

این ماژول «پایه» است: ManualBrain از آن ارث می‌برد.
  - helperهای env-based
  - helperهای فایل (tail_lines, md5, atomic_write)
  - ثابت‌ها (bad markers, platform_FA)
  - بارگذاری (learned, candidates, stats)
  - گارد کیفیت پاسخ
  - Templates

نسخهٔ ۸ (این نسخه):
  - 🎯 استفاده از config برای top-level params (ADMIN_IDS, PLATFORMS_ORDER).
  - 🎯 استفاده از data.paths برای همهٔ مسیرها.
  - 🐛 FIX: `_atomic_write_json` حالا `os.fsync` می‌زند.
  - 🐛 FIX: `_save_stats` حالا throttled است.
  - 🎯 آستانه‌ها env-based.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# 🎯 P0: از config
from config import ADMIN_IDS, PLATFORMS_ORDER  # noqa: F401
from core.kernel.logger import log_info, log_success, log_warning

# 🎯 P0: از data.paths
from data import paths as data_paths


# ═══════════════════════════════════════════════════════════
# مسیرها (از data.paths)
# ═══════════════════════════════════════════════════════════
ROOT: Path = data_paths.PROJECT_ROOT
STORAGE_DIR: Path = data_paths.STORAGE_DIR

LEARNED_FILE: Path = data_paths.LEARNED_RULES
CANDIDATE_FILE: Path = data_paths.CANDIDATE_RULES
UNKNOWN_FILE: Path = data_paths.UNKNOWN_QUESTIONS
STATS_FILE: Path = data_paths.RULE_STATS
TEMPLATES_FILE: Path = data_paths.TEMPLATES
CUSTOM_KW_FILE: Path = data_paths.KEYWORDS_CUSTOM
LOG_FILE: Path = data_paths.LOG_FILE


# ═══════════════════════════════════════════════════════════
# helpers (env-based)
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


# ═══════════════════════════════════════════════════════════
# آستانه‌ها (env-based)
# ═══════════════════════════════════════════════════════════
UNKNOWN_MIN_REPEAT = _env_int(
    "MBRAIN_UNKNOWN_MIN_REPEAT", 2, min_val=1, max_val=100
)
FUZZY_THRESHOLD = _env_float(
    "MBRAIN_FUZZY_THRESHOLD", 0.72, min_val=0.1, max_val=1.0
)
MAX_UNKNOWN_LINES = _env_int(
    "MBRAIN_MAX_UNKNOWN_LINES", 2000, min_val=100, max_val=100_000
)
MIN_ANSWER_LEN = 4
MAX_TRIGGER_LEN = 120
MIN_TRIGGER_LEN = 2
MIN_KW_LEN = 2
MAX_KW_LEN = 100

# seek از انتهای لاگ
LOG_TAIL_BYTES = 512 * 1024
UNKNOWN_TAIL_BYTES = 512 * 1024

# آستانه‌های کندی (env-based)
SLOW_PROCESS_MS = _env_int(
    "MBRAIN_SLOW_PROCESS_MS", 3000, min_val=100, max_val=60_000
)
SLOW_ACTION_MS = _env_int(
    "MBRAIN_SLOW_ACTION_MS", 2000, min_val=100, max_val=60_000
)

# throttle برای save_stats
STATS_SAVE_INTERVAL_S = _env_float(
    "MBRAIN_STATS_SAVE_INTERVAL_S", 3.0,
    min_val=0.1, max_val=60.0,
)

# پیش‌نمایش لاگ
LOG_PREVIEW_LEN = 60


# ═══════════════════════════════════════════════════════════
# ثابت‌های محتوا
# ═══════════════════════════════════════════════════════════
BAD_ANSWER_MARKERS = (
    "خطای API", "خطا در API", "Insufficient Balance",
    "Error code:", "error code:", "rate limit", "RateLimitError",
    "timed out", "TimeoutError", "ConnectionError",
    "Traceback", "Exception:", "Stack trace", "HTTP 4", "HTTP 5",
    "not available", "در دسترس نیست", "موجودی حساب",
    "WinDoctor:", "هوش مصنوعی در دسترس نیست",
    "متوجه نشدم", "مغز مشغول است",
    "درخواست قابلیت جدید ثبت شد",
)

BAD_ANSWER_PREFIXES = ("❌", "⚠️", "🚫", "⛔")

PLATFORM_FA: dict[str, str] = {
    "ایتا": "eitaa", "بله": "bale", "روبیکا": "rubika",
    "تلگرام": "telegram", "واتساپ": "whatsapp",
    "لینکدین": "linkedin", "وب": "web", "سایت": "web",
    "eitaa": "eitaa", "bale": "bale", "rubika": "rubika",
    "telegram": "telegram", "whatsapp": "whatsapp",
    "linkedin": "linkedin", "web": "web",
    "اینستاگرام": "instagram", "اینستا": "instagram",
}

VALID_TIERS = ("T1", "T2", "T3")


# ═══════════════════════════════════════════════════════════
# helpers فایل
# ═══════════════════════════════════════════════════════════
def md5_short(text: str) -> str:
    """md5 hex (FIPS-safe) — ۱۲ کاراکتر اول."""
    h = hashlib.md5(text.encode("utf-8"), usedforsecurity=False)
    return h.hexdigest()[:12]


def tail_lines(path: Path, max_bytes: int) -> list[str]:
    """آخرین خطوط فایل بدون خوندن کل آن."""
    try:
        if not path.exists():
            return []
        size = path.stat().st_size
        with open(str(path), "rb") as f:
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
                f.readline()
            data = f.read()
        return data.decode("utf-8", errors="ignore").splitlines()
    except Exception as e:
        log_warning(
            f"⚠️ [MBRAIN:tail] {path.name} | "
            f"{type(e).__name__}: {e}"
        )
        return []


def atomic_write_json(path: Path, data: Any) -> bool:
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

        # fsync دایرکتوری (POSIX)
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
            f"⚠️ [MBRAIN:write] {path.name} | "
            f"{type(e).__name__}: {e}"
        )
        return False


# ═══════════════════════════════════════════════════════════
# BrainCoreMixin
# ═══════════════════════════════════════════════════════════
class BrainCoreMixin:
    """Mixin پایه — init, load/save, stats, templates, گارد کیفیت."""

    # ─── init state ───
    def _init_core_state(self):
        """راه‌اندازی state اولیه — در `__init__` صدا زده می‌شود."""
        self.core_rules: list[dict[str, Any]] = []
        self.learned_rules: list[dict[str, Any]] = []
        self._candidates: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.stats: dict[str, dict] = {}
        self._templates_cache: dict[str, str] | None = None
        # 🎯 FIX: throttle for save_stats
        self._last_stats_save: float = 0.0

    # ═══════════════════════════════════════════
    # بارگذاری
    # ═══════════════════════════════════════════
    def _load_json_list(
        self, path: Path, label: str
    ) -> list[dict] | None:
        """لود لیست JSON — None اگه خطا یا فایل نبود."""
        if not path.exists():
            return None
        try:
            with open(str(path), encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                log_warning(
                    f"⚠️ [MBRAIN:load:{label}] ساختار نامعتبر | "
                    f"type={type(data).__name__} — رد شد"
                )
                return None
            return data
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [MBRAIN:load:{label}] JSON نامعتبر | "
                f"line={e.lineno} col={e.colno} | {e.msg}"
            )
            return None
        except Exception as e:
            log_warning(
                f"⚠️ [MBRAIN:load:{label}] "
                f"{type(e).__name__}: {e}"
            )
            return None

    def _load_learned(self):
        data = self._load_json_list(LEARNED_FILE, "learned")
        if data is None:
            return
        clean = [r for r in data if self._is_rule_healthy(r)]
        dropped = len(data) - len(clean)
        self.learned_rules = clean
        log_info(
            f"📚 [MBRAIN:load:learned] {len(clean)} قانون بارگذاری شد"
        )
        if dropped:
            log_warning(
                f"🧹 [MBRAIN:load:learned] {dropped} قانون آلوده حذف شد"
            )
            self._write_learned()

    def _load_candidates(self):
        data = self._load_json_list(CANDIDATE_FILE, "candidate")
        if data is None:
            return
        clean = [c for c in data if self._is_rule_healthy(c)]
        dropped = len(data) - len(clean)
        self._candidates = clean
        log_info(
            f"📚 [MBRAIN:load:candidate] {len(clean)} candidate بارگذاری شد"
        )
        if dropped:
            log_warning(
                f"🧹 [MBRAIN:load:candidate] "
                f"{dropped} candidate آلوده حذف شد"
            )
            self._write_candidates()

    def _cleanup_polluted(self):
        before = len(self.core_rules)
        self.core_rules = [
            r for r in self.core_rules if self._is_rule_healthy(r)
        ]
        if len(self.core_rules) < before:
            log_warning(
                f"🧹 [MBRAIN:cleanup] "
                f"{before - len(self.core_rules)} core rule آلوده حذف شد"
            )

    def _is_rule_healthy(self, rule: dict) -> bool:
        """آیا این قانون سالم است (نه آلوده)؟"""
        if not isinstance(rule, dict):
            return False
        if not rule.get("triggers") or not isinstance(
            rule["triggers"], list
        ):
            return False
        action = rule.get("action")
        if not isinstance(action, dict):
            return False
        if action.get("type") == "text":
            text_key = action.get("text_key") or action.get("key")
            if text_key:
                return True
            text = action.get("text", "")
            if not self._is_valid_answer(text):
                return False
        return True

    # ═══════════════════════════════════════════
    # نوشتن روی دیسک
    # ═══════════════════════════════════════════
    @staticmethod
    def _atomic_write_json(path: Path, data: Any) -> bool:
        """🎯 FIX: نوشتن atomic با fsync."""
        return atomic_write_json(path, data)

    def _write_learned(self):
        self._atomic_write_json(LEARNED_FILE, self.learned_rules)

    def _write_candidates(self):
        self._atomic_write_json(CANDIDATE_FILE, self._candidates)

    def _save_learned(self, rule: dict):
        self.learned_rules.append(rule)
        self._write_learned()

    def _load_stats(self):
        if not STATS_FILE.exists():
            return
        try:
            with open(str(STATS_FILE), encoding="utf-8") as f:
                self.stats = json.load(f)
            log_info(
                f"📊 [MBRAIN:load:stats] {len(self.stats)} رکورد بارگذاری شد"
            )
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [MBRAIN:load:stats] JSON نامعتبر | line={e.lineno}"
            )
            self.stats = {}
        except Exception as e:
            log_warning(
                f"⚠️ [MBRAIN:load:stats] {type(e).__name__}: {e}"
            )
            self.stats = {}

    def _save_stats(self, force: bool = False):
        """🎯 FIX: throttled save — جلوگیری از I/O زیاد."""
        now = time.monotonic()
        if (
            not force
            and (now - self._last_stats_save) < STATS_SAVE_INTERVAL_S
        ):
            return
        self._last_stats_save = now
        self._atomic_write_json(STATS_FILE, self.stats)

    def _bump_stat(self, name: str, key: str):
        """افزایش آمار — با throttle در save."""
        with self._lock:
            if name not in self.stats:
                self.stats[name] = {
                    "success": 0, "fail": 0, "last_used": None
                }
            self.stats[name][key] = self.stats[name].get(key, 0) + 1
            self.stats[name]["last_used"] = datetime.now().isoformat()
        self._save_stats()

    # ═══════════════════════════════════════════
    # Templates
    # ═══════════════════════════════════════════
    def _get_template(self, key: str) -> str:
        if self._templates_cache is None:
            try:
                if TEMPLATES_FILE.exists():
                    with open(
                        str(TEMPLATES_FILE), encoding="utf-8"
                    ) as f:
                        data = json.load(f)
                    self._templates_cache = (
                        data.get("templates", {}) or {}
                    )
                    log_info(
                        f"📚 [MBRAIN:templates] "
                        f"{len(self._templates_cache)} قالب بارگذاری شد"
                    )
                else:
                    self._templates_cache = {}
                    log_info(
                        f"ℹ️ [MBRAIN:templates] فایل نیست: "
                        f"{TEMPLATES_FILE.name}"
                    )
            except Exception as e:
                log_warning(
                    f"⚠️ [MBRAIN:templates] "
                    f"{type(e).__name__}: {e}"
                )
                self._templates_cache = {}
        return self._templates_cache.get(
            key, f"📭 قالب «{key}» یافت نشد."
        )

    # ═══════════════════════════════════════════
    # گارد کیفیت پاسخ
    # ═══════════════════════════════════════════
    @staticmethod
    def _is_valid_answer(answer: str) -> bool:
        if not answer or not isinstance(answer, str):
            return False
        a = answer.strip()
        if len(a) < MIN_ANSWER_LEN:
            return False
        if a.startswith(BAD_ANSWER_PREFIXES):
            return False
        low = a.lower()
        for marker in BAD_ANSWER_MARKERS:
            if marker.lower() in low:
                return False
        if not re.search(r"[\w\u0600-\u06FF]", a):
            return False
        return True

    @staticmethod
    def _is_valid_trigger(trigger: str) -> bool:
        if not trigger or not isinstance(trigger, str):
            return False
        t = trigger.strip()
        if len(t) < MIN_TRIGGER_LEN or len(t) > MAX_TRIGGER_LEN:
            return False
        return True

    # ═══════════════════════════════════════════
    # normalize
    # ═══════════════════════════════════════════
    @staticmethod
    def _normalize(text: str) -> str:
        t = (text or "").lower()
        t = t.replace("ي", "ی").replace("ك", "ک")
        t = t.replace("\u200c", " ")
        t = re.sub(r"\s+", " ", t).strip()
        return t

    # ═══════════════════════════════════════════
    # ابزارهای استخراج
    # ═══════════════════════════════════════════
    @staticmethod
    def _extract_number(text: str) -> int | None:
        """عدد را از متن استخراج می‌کند (فارسی/عربی/انگلیسی)."""
        if not text:
            return None
        trans = str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789",
        )
        t = text.translate(trans)
        m = re.search(r"(?:^|\s)(\d{1,5})(?:\s|$)", t)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
        return None

    @staticmethod
    def _extract_quoted(text: str) -> list[str]:
        """همهٔ عبارت‌های داخل « » را برمی‌گرداند."""
        return [m.strip() for m in re.findall(r"«([^»]+)»", text)]

    @staticmethod
    def _extract_value_fallback(
        text: str, action_words: list[str]
    ) -> str | None:
        """🎯 FIX: اگر action_words خالی باشد، امن است."""
        if not text:
            return None
        t = text.strip()

        t = re.sub(
            r"^\s*(کلید\s*واژه(\s*سفارشی|\s*دستی|\s*ها)?|keyword)"
            r"\s*[:،]?\s*",
            "",
            t,
            flags=re.IGNORECASE,
        )

        if action_words:
            for w in action_words:
                if not w:
                    continue
                t = re.sub(
                    rf"\s*{re.escape(w)}\s*",
                    " ",
                    t,
                    flags=re.IGNORECASE,
                )

        t = re.sub(
            r"\s*(را|رو|کن|بکن|بده|بذار|انجام\s*بده)\s*",
            " ",
            t,
        )

        t = re.sub(r"\s+", " ", t).strip()

        if not t or len(t) < 2:
            return None
        return t

    @staticmethod
    def _extract_tier(text: str) -> str:
        """تشخیص tier از متن — پیش‌فرض T1."""
        low = (text or "").lower()
        trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
        low = low.translate(trans)

        if any(w in low for w in [
            "سطح 2", "سطح2", "رده 2", "رده2",
            "تی 2", "تی2", "tier 2", "tier2",
            "level 2", "level2",
        ]):
            return "T2"
        if any(w in low for w in [
            "سطح 3", "رده 3", "تی 3", "tier 3", "level 3",
        ]):
            return "T3"
        return "T1"

    @staticmethod
    def _extract_platform(text: str) -> str | None:
        low = (text or "").lower()
        for fa, code in PLATFORM_FA.items():
            pattern = (
                r"(^|[\s،.:؛!?؟])"
                + re.escape(fa)
                + r"([\s،.:؛!?؟]|$)"
            )
            if re.search(pattern, low):
                return code
        return None