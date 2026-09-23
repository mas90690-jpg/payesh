# core/access_manager.py
"""
مدیر دسترسی — کنترل دقیق و ایمن همه دسترسی‌های هوش.

منبع یکتا: `data/configs/access_control.json`.
اگه فایل نباشه، _DEFAULT (پیش‌فرض امن) استفاده می‌شه.

⚠️ این singleton سطح امنیتی حیاتی پروژه‌ست. تصمیماتش در لاگ با پیشوند
`[ACCESS:...]` ثبت می‌شن تا بتونی بعداً ببینی هوش چه اجازه‌هایی گرفته.

نسخهٔ ۴ (این نسخه):
  - 🐛 FIX: `_LOG_PREVIEW` از پایین فایل به بالای فایل منتقل شد
    (قبلاً بعد از کلاس تعریف می‌شد — گرچه عملاً کار می‌کرد ولی
    طراحی بدی بود و ریسک NameError در importهای عجیب داشت).
  - 🐛 FIX: `"full_access": False` به `_DEFAULT` اضافه شد
    (برای هم‌راستایی با `core/brain/utils._load_access`).
  - 🎯 توضیح واضح برای fallback مسیر ROOT.

نسخهٔ ۳:
  - 🎯 مسیر از `data/paths.py` میاد (منبع یکتا).
"""
from __future__ import annotations

import copy
import fnmatch
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from core.kernel.logger import log_error, log_info, log_success, log_warning

# ═══════════════════════════════════════════
# مسیر پایهٔ پروژه
# ═══════════════════════════════════════════
# اولویت: از config (منبع یکتا). اگه config در دسترس نبود،
# از موقعیت خودِ فایل حساب می‌کنیم.
#
# ⚠️ اگه این فایل در `core/access_manager.py` باشه:
#     Path(__file__).parent        → core/
#     Path(__file__).parent.parent → <project_root>/    ✅
# اگه در `core/adapters/access_manager.py` منتقلش کنی، باید
# یه `.parent` دیگه اضافه کنی.
try:
    from config import BASE_DIR as ROOT
except Exception:
    ROOT = Path(__file__).parent.parent

ROOT = Path(ROOT)

# ⭐ مسیر از data/paths.py (منبع یکتا)
from data import paths as data_paths

ACCESS_FILE = data_paths.ACCESS_CONTROL

# ═══════════════════════════════════════════
# ثابت‌ها
# ═══════════════════════════════════════════
# آستانهٔ هشدار کندی بارگذاری (میلی‌ثانیه)
_SLOW_LOAD_MS = 500

# 🐛 FIX: قبلاً این متغیر در انتهای فایل بود (بعد از کلاس) — الان
# به بالای فایل منتقل شد تا از هر جای کلاس قابل استفاده باشه و
# خوانایی بهتری داشته باشه.
_LOG_PREVIEW = 60

# ─── کلمات ممنوعه در کوئری (extended) ───
_BANNED_SQL_KEYWORDS = (
    "DELETE", "UPDATE", "INSERT", "DROP", "TRUNCATE", "ALTER",
    "ATTACH", "PRAGMA", "VACUUM", "CREATE", "REPLACE",
    "GRANT", "REVOKE", "LOAD_EXTENSION", "INTO OUTFILE",
    "INTO DUMPFILE",
)

# ─── پیش‌فرض امن (deep-copy برای جلوگیری از mutation) ───
# 🐛 FIX: `"full_access": False` اضافه شد تا با
# `core/brain/utils._load_access` هم‌راستا باشه.
_DEFAULT: dict[str, Any] = {
    "full_access": False,
    "read": {
        "allow": ["**/*"],
        "deny": [".env", "storage/sessions/**"],
    },
    "write": {
        "allow": [],
        "deny": ["**/*"],
    },
    "database": {
        "read_tables": [],
        "write_tables": [],
        "max_rows_select": 100,
    },
    "settings": {
        "read": [],
        "write": [],
        "deny": [],
    },
    "platforms": {
        "toggleable": [],
        "status_check": False,
    },
    "commands": {
        "allow": [],
        "deny": ["*"],
    },
}


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _deep_default() -> dict[str, Any]:
    """کپی تازه از _DEFAULT (جلوگیری از آلودگی)."""
    return copy.deepcopy(_DEFAULT)


class AccessManager:
    """Singleton مدیریت دسترسی."""

    _instance: AccessManager | None = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    obj = super().__new__(cls)
                    obj._initialized = False
                    cls._instance = obj
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.rules: dict[str, Any] = {}
        # کش regex کامپایل‌شده برای _match
        self._regex_cache: dict[str, re.Pattern] = {}

        t0 = time.monotonic()
        self.rules = self._load()
        elapsed_ms = (time.monotonic() - t0) * 1000

        log_success(
            f"✅ [ACCESS:init] آماده | file={ACCESS_FILE.name} | "
            f"{elapsed_ms:.0f}ms | "
            f"read_allow={len(self.rules.get('read', {}).get('allow', []))} | "
            f"write_allow={len(self.rules.get('write', {}).get('allow', []))} | "
            f"db_tables={len(self.rules.get('database', {}).get('read_tables', []))} | "
            f"cmd_allow={len(self.rules.get('commands', {}).get('allow', []))}"
        )
        if elapsed_ms > _SLOW_LOAD_MS:
            log_warning(
                f"🐌 [ACCESS:init] کند | {elapsed_ms:.0f}ms"
            )

    # ═══════════════════════════════════════════
    # load
    # ═══════════════════════════════════════════
    def _load(self) -> dict[str, Any]:
        """بارگذاری rules از فایل — با deep-copy پیش‌فرض."""
        defaults = _deep_default()

        if not ACCESS_FILE.exists():
            log_warning(
                f"⚠️ [ACCESS:load] فایل نبود: {ACCESS_FILE} — "
                f"پیش‌فرضِ امن"
            )
            return defaults

        try:
            with open(str(ACCESS_FILE), encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            log_error(
                f"❌ [ACCESS:load] JSON نامعتبر | "
                f"line={e.lineno} col={e.colno} | {e.msg} — "
                f"پیش‌فرضِ امن"
            )
            return defaults
        except Exception as e:
            log_error(
                f"❌ [ACCESS:load] خواندن | "
                f"{type(e).__name__}: {e} — پیش‌فرضِ امن"
            )
            return defaults

        if not isinstance(data, dict):
            log_warning(
                f"⚠️ [ACCESS:load] ساختار نامعتبر | "
                f"type={type(data).__name__} — پیش‌فرضِ امن"
            )
            return defaults

        # shallow merge top-level
        merged: dict[str, Any] = {**defaults, **data}

        # deep merge برای top-level dict-ها
        for k, default_val in defaults.items():
            if k not in data:
                continue
            if not isinstance(data[k], dict) or not isinstance(default_val, dict):
                continue
            merged[k] = {**default_val, **data[k]}

        log_info(
            f"📂 [ACCESS:load] {ACCESS_FILE.name} بارگذاری شد | "
            f"keys={list(merged.keys())}"
        )
        return merged

    def reload(self) -> int:
        """بارگذاری مجدد rules. Returns: تعداد top-level keys."""
        t0 = time.monotonic()
        self.rules = self._load()
        self._regex_cache.clear()
        elapsed_ms = (time.monotonic() - t0) * 1000
        n = len(self.rules)
        log_info(
            f"🔄 [ACCESS:reload] {n} rule-set بارگذاری شد | "
            f"{elapsed_ms:.0f}ms"
        )
        return n

    # ═══════════════════════════════════════════
    # helpers
    # ═══════════════════════════════════════════
    @staticmethod
    def _rel(path: str) -> str:
        """مسیر نسبی به ROOT (برای pattern matching)."""
        try:
            return str(
                Path(path).resolve().relative_to(ROOT)
            ).replace("\\", "/")
        except ValueError:
            log_warning(
                f"⚠️ [ACCESS:path] خارج از پروژه | path={path!r}"
            )
            return str(path).replace("\\", "/")
        except Exception as e:
            log_warning(
                f"⚠️ [ACCESS:path] {type(e).__name__}: {e} | "
                f"path={path!r}"
            )
            return str(path).replace("\\", "/")

    def _compile_pattern(self, pat: str) -> re.Pattern | None:
        """کامپایل + cache الگوی `**` — با پشتیبانی ریشه‌ای."""
        if pat in self._regex_cache:
            return self._regex_cache[pat]
        try:
            regex_str = re.escape(pat)
            regex_str = regex_str.replace(r"\*\*/", r"(?:.*/)?")
            regex_str = regex_str.replace(r"\*\*", r".*")
            regex_str = regex_str.replace(r"\*", r"[^/]*")
            regex_str = regex_str.replace(r"\?", r".")
            compiled = re.compile(f"^{regex_str}$")
            self._regex_cache[pat] = compiled
            return compiled
        except re.error as e:
            log_warning(
                f"⚠️ [ACCESS:match] regex نامعتبر | pat={pat!r} | "
                f"{type(e).__name__}: {e}"
            )
            return None

    def _match(self, rel: str, patterns: list) -> bool:
        """آیا مسیر نسبی با الگوها منطبق است؟"""
        if not patterns:
            return False
        for pat in patterns:
            if not pat:
                continue
            pat = str(pat).replace("\\", "/")

            if fnmatch.fnmatch(rel, pat):
                return True

            if "**" in pat:
                rx = self._compile_pattern(pat)
                if rx and rx.match(rel):
                    return True

            if "/" not in pat:
                try:
                    if fnmatch.fnmatch(Path(rel).name, pat):
                        return True
                except Exception:
                    pass
        return False

    # ═══════════════════════════════════════════
    # read / write
    # ═══════════════════════════════════════════
    def can_read(self, path: str) -> tuple[bool, str]:
        rel = self._rel(path)
        r = self.rules.get("read", {}) or {}
        if self._match(rel, r.get("deny", [])):
            log_info(f"⛔ [ACCESS:read] deny | {rel}")
            return False, f"در deny خواندن: {rel}"
        if not self._match(rel, r.get("allow", [])):
            log_info(f"⛔ [ACCESS:read] not in allow | {rel}")
            return False, f"در allow خواندن نیست: {rel}"
        return True, ""

    def can_write(self, path: str) -> tuple[bool, str]:
        rel = self._rel(path)
        w = self.rules.get("write", {}) or {}
        if self._match(rel, w.get("deny", [])):
            log_info(f"⛔ [ACCESS:write] deny | {rel}")
            return False, f"در deny نوشتن: {rel}"
        if not self._match(rel, w.get("allow", [])):
            log_info(f"⛔ [ACCESS:write] not in allow | {rel}")
            return False, f"در allow نوشتن نیست: {rel}"
        return True, ""

    def is_read_allowed(self, path: str) -> bool:
        return self.can_read(path)[0]

    def is_write_allowed(self, path: str) -> bool:
        return self.can_write(path)[0]

    # ═══════════════════════════════════════════
    # database
    # ═══════════════════════════════════════════
    def can_query_db(self, query: str) -> tuple[bool, str]:
        if not query or not query.strip():
            return False, "کوئری خالیه."

        q = query.strip().upper()

        if not q.startswith("SELECT"):
            log_info(
                f"⛔ [ACCESS:db] not SELECT | "
                f"q={query[:_LOG_PREVIEW]!r}"
            )
            return False, "فقط SELECT مجاز است."

        stripped = query.strip().rstrip(";")
        if ";" in stripped:
            log_warning(
                f"⚠️ [ACCESS:db] چند دستور در یک query | "
                f"q={query[:_LOG_PREVIEW]!r}"
            )
            return False, "چند دستور در یک کوئری مجاز نیست."

        for kw in _BANNED_SQL_KEYWORDS:
            if re.search(rf"\b{kw}\b", q):
                log_warning(
                    f"⛔ [ACCESS:db] کلمه ممنوعه «{kw}» | "
                    f"q={query[:_LOG_PREVIEW]!r}"
                )
                return False, f"عملیات {kw} ممنوع."

        db = self.rules.get("database", {}) or {}
        allowed = [str(t).lower() for t in db.get("read_tables", [])]
        m = re.search(
            r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)", query, re.IGNORECASE
        )
        if m and "*" not in allowed:
            tbl = m.group(1).lower()
            if tbl not in allowed:
                log_warning(
                    f"⛔ [ACCESS:db] جدول «{tbl}» مجاز نیست | "
                    f"allowed={allowed}"
                )
                return False, f"جدول {m.group(1)} مجاز نیست."

        max_rows = int(db.get("max_rows_select", 1000))
        lm = re.search(r"\bLIMIT\s+(\d+)", q)
        if lm:
            try:
                v = int(lm.group(1))
            except ValueError:
                v = 0
            if v > max_rows:
                log_warning(
                    f"⛔ [ACCESS:db] LIMIT از سقف بزرگتر | "
                    f"limit={v} max={max_rows}"
                )
                return False, f"LIMIT از {max_rows} بیشتره."

        return True, ""

    def is_query_allowed(self, query: str) -> bool:
        return self.can_query_db(query)[0]

    # ═══════════════════════════════════════════
    # settings
    # ═══════════════════════════════════════════
    def can_read_setting(self, key: str) -> tuple[bool, str]:
        r = self.rules.get("settings", {}).get("read", []) or []
        key_u = str(key).upper()
        if "*" in r or key_u in {str(x).upper() for x in r}:
            return True, ""
        for pat in r:
            if str(pat).endswith("*"):
                if key_u.startswith(str(pat)[:-1].upper()):
                    return True, ""
        return False, f"خواندن {key} مجاز نیست."

    def can_write_setting(self, key: str) -> tuple[bool, str]:
        key_u = str(key).upper()
        s = self.rules.get("settings", {}) or {}

        for d in s.get("deny", []) or []:
            d_u = str(d).upper()
            if d_u == "*":
                log_info(
                    f"⛔ [ACCESS:setting] deny=* | {key_u}"
                )
                return False, f"{key} در deny است."
            if d_u.endswith("*"):
                if key_u.startswith(d_u[:-1]):
                    log_info(
                        f"⛔ [ACCESS:setting] deny-prefix | "
                        f"{key_u} ← {d_u}"
                    )
                    return False, f"{key} در deny است."
            elif "*" in d_u:
                rx = re.compile(
                    "^"
                    + re.escape(d_u).replace(r"\*", ".*")
                    + "$"
                )
                if rx.match(key_u):
                    return False, f"{key} در deny است."
            elif key_u == d_u:
                return False, f"{key} در deny است."

        w = [str(k).upper() for k in (s.get("write", []) or [])]
        if "*" in w or key_u in w:
            return True, ""
        for pat in w:
            if pat.endswith("*"):
                if key_u.startswith(pat[:-1]):
                    return True, ""
        return False, f"نوشتن {key} مجاز نیست."

    def is_setting_read_allowed(self, key: str) -> bool:
        return self.can_read_setting(key)[0]

    def is_setting_write_allowed(self, key: str) -> bool:
        return self.can_write_setting(key)[0]

    # ═══════════════════════════════════════════
    # platform
    # ═══════════════════════════════════════════
    def can_toggle_platform(self, p: str) -> tuple[bool, str]:
        allowed = [
            str(x).lower()
            for x in (self.rules.get("platforms", {}).get("toggleable", []) or [])
        ]
        if str(p).lower() in allowed:
            return True, ""
        log_info(
            f"⛔ [ACCESS:platform] {p} مجاز نیست | "
            f"allowed={allowed}"
        )
        return False, f"پلتفرم {p} قابل تغییر نیست."

    def is_platform_toggle_allowed(self, p: str) -> bool:
        return self.can_toggle_platform(p)[0]

    # ═══════════════════════════════════════════
    # command
    # ═══════════════════════════════════════════
    def can_run_command(self, cmd: str) -> tuple[bool, str]:
        if not cmd or not cmd.strip():
            return False, "دستور خالیه."

        c = cmd.strip().lower()
        r = self.rules.get("commands", {}) or {}

        for d in r.get("deny", []) or []:
            d_low = str(d).lower()
            if not d_low:
                continue
            if c.startswith(d_low):
                log_warning(
                    f"⛔ [ACCESS:cmd] deny-prefix | "
                    f"cmd={c[:_LOG_PREVIEW]!r} ← {d_low!r}"
                )
                return False, f"دستور شامل {d} ممنوع."
            if " " in d_low:
                if re.search(
                    rf"(?:^|\s){re.escape(d_low)}(?:\s|$)", c
                ):
                    return False, f"دستور شامل {d} ممنوع."

        allow = r.get("allow", []) or []
        if not allow:
            log_warning(
                f"⛔ [ACCESS:cmd] allow خالی | cmd={c[:_LOG_PREVIEW]!r}"
            )
            return False, "لیست allow خالیه."
        for a in allow:
            a_low = str(a).lower()
            if a_low and c.startswith(a_low):
                return True, ""

        log_info(
            f"⛔ [ACCESS:cmd] در whitelist نیست | "
            f"cmd={c[:_LOG_PREVIEW]!r}"
        )
        return False, "دستور در whitelist نیست."

    def is_command_allowed(self, cmd: str) -> bool:
        return self.can_run_command(cmd)[0]

    # ═══════════════════════════════════════════
    # خلاصه
    # ═══════════════════════════════════════════
    def get_summary_dict(self) -> dict[str, Any]:
        """خلاصه JSON-safe — برای /health یا admin."""
        r = self.rules
        return {
            "read_allow": len(r.get("read", {}).get("allow", []) or []),
            "read_deny": len(r.get("read", {}).get("deny", []) or []),
            "write_allow": len(r.get("write", {}).get("allow", []) or []),
            "write_deny": len(r.get("write", {}).get("deny", []) or []),
            "db_read_tables": r.get("database", {}).get("read_tables", []) or [],
            "db_max_rows": r.get("database", {}).get("max_rows_select", 100),
            "settings_read": len(r.get("settings", {}).get("read", []) or []),
            "settings_write": len(r.get("settings", {}).get("write", []) or []),
            "settings_deny": len(r.get("settings", {}).get("deny", []) or []),
            "platforms_toggleable": (
                r.get("platforms", {}).get("toggleable", []) or []
            ),
            "commands_allow": len(r.get("commands", {}).get("allow", []) or []),
            "commands_deny": len(r.get("commands", {}).get("deny", []) or []),
            "file": str(ACCESS_FILE),
        }

    def get_summary(self) -> str:
        """خلاصهٔ متنی برای نمایش به مدیر."""
        r = self.rules
        return (
            "🔐 **دسترسی‌ها**\n"
            f"📖 خواندن: {len(r.get('read',{}).get('allow',[]))} الگو\n"
            f"✏️ نوشتن: {len(r.get('write',{}).get('allow',[]))} الگو\n"
            f"🗄️ جداول DB: {r.get('database',{}).get('read_tables',[])}\n"
            f"⚙️ تنظیمات (نوشتن): {len(r.get('settings',{}).get('write',[]))} مورد\n"
            f"📱 پلتفرم‌ها: {r.get('platforms',{}).get('toggleable',[])}\n"
            f"🖥️ دستورات: {len(r.get('commands',{}).get('allow',[]))} مورد"
        )


# ═══════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════
# 🐛 FIX: `_LOG_PREVIEW = 60` از اینجا حذف شد و به بالای فایل منتقل شد.

_mgr: AccessManager | None = None
_mgr_lock = threading.Lock()


def get_access_manager() -> AccessManager:
    """دریافت نمونهٔ singleton."""
    global _mgr
    if _mgr is None:
        with _mgr_lock:
            if _mgr is None:
                _mgr = AccessManager()
    return _mgr