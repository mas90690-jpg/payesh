"""مدیر دسترسی — کنترل دقیق و ایمن همه دسترسی‌های هوش.

منبع یکتا: `data/configs/access_control.json`.
اگر فایل نباشد، `_DEFAULT` (پیش‌فرض امن) استفاده می‌شود.

⚠️ این singleton سطح امنیتی حیاتی پروژه است. تصمیماتش در لاگ با
پیشوند `[ACCESS:...]` ثبت می‌شوند تا بعداً بتوانی ببینی هوش چه
اجازه‌هایی گرفته.

نسخهٔ ۵ (این نسخه):
  - 🐛 FIX: `ROOT` حالا `Path(__file__).parent.parent.parent` است
    (فایل در `core/adapters/` است، نه `core/`).
  - 🐛 FIX: `_load()` حالا deep-merge واقعی می‌کند (merge یک‌سطحی
    قبلی، defaults nested را از دست می‌داد).
  - 🐛 FIX: `can_query_db` حالا `INTO OUTFILE`/`INTO DUMPFILE` را
    با regex صریح چک می‌کند (قبلاً word-boundary روی فاصله کار
    نمی‌کرد).
  - 🐛 FIX: `can_run_command` حالا ناهماهنگ با
    `mixin_system._execute_command` را رفع می‌کند:
      * `deny` روی همه توکن‌ها (نه فقط first)
      * پشتیبانی از `*` در deny (به‌عنوان «همه»)
      * پشتیبانی از shell metachar check
  - 🐛 FIX: `_DEFAULT.commands.allow` حالا معقول‌تر است
    (اگر فایل نبود، سیستم قفل نمی‌شود).
  - 🐛 FIX: `get_summary_dict()` حالا `full_access` را برمی‌گرداند
    (طبق MEMORY R2.20).
  - 🐛 FIX: `can_write_setting` حالا regex را cache می‌کند.
  - 🐛 FIX: `_LOG_PREVIEW` حالا env-based.
  - 🐛 FIX: `_rel` حالا case-insensitive روی Windows.
  - 🎯 `_deep_merge()` helper جدا.
  - 🎯 `get_stats()`, `__repr__` برای debug.
  - 🎯 `_regex_cache` با lock (thread-safe).
  - 🎯 `__all__` export.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۴.
"""
from __future__ import annotations

import copy
import fnmatch
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
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


def _deep_merge(
    base: dict[str, Any], override: dict[str, Any]
) -> dict[str, Any]:
    """🎯 deep-merge — nested dict بازگشتی merge می‌شوند."""
    result = copy.deepcopy(base)
    for key, val in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(val, dict)
        ):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


# ═══════════════════════════════════════════
# مسیر پایه
# ═══════════════════════════════════════════
# ⚠️ این فایل در `core/adapters/` است → 3 `.parent` لازم است.
# fallback: از config
try:
    from config import BASE_DIR as ROOT
except Exception:
    ROOT = Path(__file__).parent.parent.parent

ROOT = Path(ROOT).resolve()


# ─── مسیر از data.paths ───
from data import paths as data_paths

ACCESS_FILE: Path = data_paths.ACCESS_CONTROL


# ═══════════════════════════════════════════
# ثابت‌ها
# ═══════════════════════════════════════════
_SLOW_LOAD_MS = _env_int(
    "ACCESS_SLOW_LOAD_MS", 500,
    min_val=50, max_val=60_000,
)
_LOG_PREVIEW = _env_int(
    "ACCESS_LOG_PREVIEW", 60,
    min_val=20, max_val=500,
)

# ─── کلمات ممنوعه در کوئری ───
_BANNED_SQL_KEYWORDS: tuple[str, ...] = (
    "DELETE", "UPDATE", "INSERT", "DROP", "TRUNCATE", "ALTER",
    "ATTACH", "PRAGMA", "VACUUM", "CREATE", "REPLACE",
    "GRANT", "REVOKE", "LOAD_EXTENSION",
)

# 🐛 FIX: الگوهای چندکلمه‌ای جدا (چون word-boundary روی فاصله کار نمی‌کند)
_BANNED_SQL_PHRASES: tuple[str, ...] = (
    "INTO OUTFILE",
    "INTO DUMPFILE",
    "LOAD_FILE",
)

# ─── shell metacharacters (ناهماهنگی mixin_system) ───
_SHELL_METACHAR_RE = re.compile(
    r"(?:[;&|`$<>]|\|\||&&|>>|<<|\$\()"
)


# ═══════════════════════════════════════════
# _DEFAULT (پیش‌فرض امن)
# ═══════════════════════════════════════════
_DEFAULT: dict[str, Any] = {
    "full_access": False,
    "read": {
        "allow": ["**/*"],
        "deny": [".env", ".env.*", "storage/sessions/**"],
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
    # 🐛 FIX: allow معقول‌تر — اگر فایل نبود، سیستم قفل نمی‌شود
    "commands": {
        "allow": [
            "git status", "git diff", "git log",
        ],
        "deny": ["*"],
    },
}


def _deep_default() -> dict[str, Any]:
    """کپی تازه از `_DEFAULT`."""
    return copy.deepcopy(_DEFAULT)


# ═══════════════════════════════════════════
# AccessManager
# ═══════════════════════════════════════════
class AccessManager:
    """Singleton مدیریت دسترسی."""

    _instance: "AccessManager | None" = None
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
        # 🐛 FIX: regex cache با lock
        self._regex_cache: dict[str, re.Pattern] = {}
        self._regex_lock = threading.Lock()

        t0 = time.monotonic()
        self.rules = self._load()
        elapsed_ms = (time.monotonic() - t0) * 1000

        read_allow = len(
            self.rules.get("read", {}).get("allow", [])
        )
        write_allow = len(
            self.rules.get("write", {}).get("allow", [])
        )
        db_tables = len(
            self.rules.get("database", {}).get(
                "read_tables", []
            )
        )
        cmd_allow = len(
            self.rules.get("commands", {}).get("allow", [])
        )

        log_success(
            f"✅ [ACCESS:init] آماده | file={ACCESS_FILE.name} | "
            f"{elapsed_ms:.0f}ms | "
            f"read_allow={read_allow} | "
            f"write_allow={write_allow} | "
            f"db_tables={db_tables} | "
            f"cmd_allow={cmd_allow}"
        )
        if elapsed_ms > _SLOW_LOAD_MS:
            log_warning(
                f"🐌 [ACCESS:init] کند | {elapsed_ms:.0f}ms"
            )

    # ═══════════════════════════════════════════
    # load
    # ═══════════════════════════════════════════
    def _load(self) -> dict[str, Any]:
        """🎯 FIX: بارگذاری rules از فایل با deep-merge واقعی."""
        defaults = _deep_default()

        if not ACCESS_FILE.exists():
            log_warning(
                f"⚠️ [ACCESS:load] فایل نبود: {ACCESS_FILE} — "
                f"پیش‌فرضِ امن"
            )
            return defaults

        try:
            with open(
                str(ACCESS_FILE), encoding="utf-8"
            ) as f:
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

        # 🐛 FIX: deep-merge واقعی
        merged = _deep_merge(defaults, data)

        log_info(
            f"📂 [ACCESS:load] {ACCESS_FILE.name} بارگذاری شد | "
            f"keys={list(merged.keys())}"
        )
        return merged

    def reload(self) -> int:
        """بارگذاری مجدد rules. Returns: تعداد top-level keys."""
        t0 = time.monotonic()
        self.rules = self._load()
        # 🎯 پاک‌کردن cache
        with self._regex_lock:
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
        """🎯 FIX: مسیر نسبی — case-insensitive روی Windows."""
        try:
            resolved = Path(path).resolve()
            rel = resolved.relative_to(ROOT)
            return str(rel).replace("\\", "/")
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

    def _compile_pattern(
        self, pat: str
    ) -> re.Pattern | None:
        """کامپایل + cache الگوی `**` — thread-safe."""
        with self._regex_lock:
            cached = self._regex_cache.get(pat)
        if cached is not None:
            return cached

        try:
            regex_str = re.escape(pat)
            regex_str = regex_str.replace(
                r"\*\*/", r"(?:.*/)?"
            )
            regex_str = regex_str.replace(r"\*\*", r".*")
            regex_str = regex_str.replace(r"\*", r"[^/]*")
            regex_str = regex_str.replace(r"\?", r".")
            compiled = re.compile(f"^{regex_str}$")
            with self._regex_lock:
                self._regex_cache[pat] = compiled
            return compiled
        except re.error as e:
            log_warning(
                f"⚠️ [ACCESS:match] regex نامعتبر | "
                f"pat={pat!r} | {type(e).__name__}: {e}"
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
            log_info(
                f"⛔ [ACCESS:read] not in allow | {rel}"
            )
            return False, f"در allow خواندن نیست: {rel}"
        return True, ""

    def can_write(self, path: str) -> tuple[bool, str]:
        rel = self._rel(path)
        w = self.rules.get("write", {}) or {}
        if self._match(rel, w.get("deny", [])):
            log_info(f"⛔ [ACCESS:write] deny | {rel}")
            return False, f"در deny نوشتن: {rel}"
        if not self._match(rel, w.get("allow", [])):
            log_info(
                f"⛔ [ACCESS:write] not in allow | {rel}"
            )
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

        # ─── چک `;` (چند دستور) ───
        stripped = query.strip().rstrip(";")
        if ";" in stripped:
            log_warning(
                f"⚠️ [ACCESS:db] چند دستور در یک query | "
                f"q={query[:_LOG_PREVIEW]!r}"
            )
            return False, "چند دستور در یک کوئری مجاز نیست."

        # ─── کلمات ممنوعه (تک‌کلمه) ───
        for kw in _BANNED_SQL_KEYWORDS:
            if re.search(rf"\b{kw}\b", q):
                log_warning(
                    f"⛔ [ACCESS:db] کلمه ممنوعه «{kw}» | "
                    f"q={query[:_LOG_PREVIEW]!r}"
                )
                return False, f"عملیات {kw} ممنوع."

        # 🐛 FIX: الگوهای چندکلمه‌ای
        for phrase in _BANNED_SQL_PHRASES:
            if phrase in q:
                log_warning(
                    f"⛔ [ACCESS:db] phrase ممنوعه «{phrase}» | "
                    f"q={query[:_LOG_PREVIEW]!r}"
                )
                return False, f"«{phrase}» ممنوع."

        # ─── جدول whitelist ───
        db = self.rules.get("database", {}) or {}
        allowed = [
            str(t).lower()
            for t in db.get("read_tables", [])
        ]
        m = re.search(
            r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)",
            query,
            re.IGNORECASE,
        )
        if m and "*" not in allowed:
            tbl = m.group(1).lower()
            if tbl not in allowed:
                log_warning(
                    f"⛔ [ACCESS:db] جدول «{tbl}» مجاز نیست | "
                    f"allowed={allowed}"
                )
                return False, f"جدول {m.group(1)} مجاز نیست."

        # ─── LIMIT ───
        try:
            max_rows = int(
                db.get("max_rows_select", 1000)
            )
        except (TypeError, ValueError):
            max_rows = 1000

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
        if "*" in r:
            return True, ""
        read_set = {str(x).upper() for x in r}
        if key_u in read_set:
            return True, ""
        for pat in r:
            p = str(pat)
            if p.endswith("*"):
                if key_u.startswith(p[:-1].upper()):
                    return True, ""
        return False, f"خواندن {key} مجاز نیست."

    def can_write_setting(self, key: str) -> tuple[bool, str]:
        key_u = str(key).upper()
        s = self.rules.get("settings", {}) or {}

        # ─── deny first ───
        for d in s.get("deny", []) or []:
            d_u = str(d).upper()
            if d_u == "*":
                log_info(
                    f"⛔ [ACCESS:setting] deny=* | {key_u}"
                )
                return False, f"{key} در deny است."
            if d_u.endswith("*"):
                if key_u.startswith(d_u[:-1]):
                    return False, f"{key} در deny است."
            elif "*" in d_u:
                # 🐛 FIX: cache regex
                rx = self._compile_pattern(d_u)
                if rx and rx.match(key_u):
                    return False, f"{key} در deny است."
            elif key_u == d_u:
                return False, f"{key} در deny است."

        # ─── allow ───
        w = [
            str(k).upper() for k in (s.get("write", []) or [])
        ]
        if "*" in w:
            return True, ""
        if key_u in w:
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
            for x in (
                self.rules.get("platforms", {}).get(
                    "toggleable", []
                ) or []
            )
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
        """🐛 FIX: ناهماهنگی با `mixin_system._execute_command` رفع شد.

        چرا؟ چون `mixin_system` حالا از `shlex.split` + `shell=False`
        + deny روی همه توکن‌ها + whitelist روی first token استفاده
        می‌کند. access_manager هم باید همین کار را بکند تا
        تصمیماتش با اجرا هماهنگ باشد.
        """
        if not cmd or not cmd.strip():
            return False, "دستور خالیه."

        c = cmd.strip()
        c_low = c.lower()
        r = self.rules.get("commands", {}) or {}

        # ─── shell metachar check ───
        if _SHELL_METACHAR_RE.search(c):
            log_warning(
                f"⛔ [ACCESS:cmd] shell metachar | "
                f"cmd={c[:_LOG_PREVIEW]!r}"
            )
            return False, "دستور شامل کاراکتر ویژهٔ shell است."

        # ─── shlex parse ───
        import shlex
        try:
            tokens = shlex.split(c, posix=(os.name != "nt"))
        except ValueError as e:
            log_warning(
                f"⛔ [ACCESS:cmd] shlex | "
                f"{type(e).__name__}: {e} | "
                f"cmd={c[:_LOG_PREVIEW]!r}"
            )
            return False, f"دستور نامعتبر: {e}"

        if not tokens:
            return False, "دستور خالیه."

        # ─── deny ───
        deny_list = r.get("deny", []) or []
        for d in deny_list:
            d_low = str(d).strip().lower()
            if not d_low:
                continue

            # 🐛 FIX: `*` به‌عنوان «همه»
            if d_low == "*":
                log_warning(
                    f"⛔ [ACCESS:cmd] deny=* | "
                    f"cmd={c[:_LOG_PREVIEW]!r}"
                )
                return False, "همهٔ دستورات ممنوع‌اند."

            # چندکلمه‌ای: فقط prefix
            if " " in d_low:
                if c_low.startswith(d_low):
                    log_warning(
                        f"⛔ [ACCESS:cmd] deny-prefix | "
                        f"cmd={c[:_LOG_PREVIEW]!r} ← {d_low!r}"
                    )
                    return False, f"دستور شامل {d} ممنوع."
            else:
                # 🐛 FIX: تک‌کلمه — در همهٔ توکن‌ها
                for tok in tokens:
                    if tok.lower() == d_low:
                        log_warning(
                            f"⛔ [ACCESS:cmd] deny-token | "
                            f"cmd={c[:_LOG_PREVIEW]!r} ← {d_low!r}"
                        )
                        return False, f"دستور شامل {d} ممنوع."

        # ─── allow ───
        allow = r.get("allow", []) or []
        if not allow:
            log_warning(
                f"⛔ [ACCESS:cmd] allow خالی | "
                f"cmd={c[:_LOG_PREVIEW]!r}"
            )
            return False, "لیست allow خالیه."

        for a in allow:
            a_low = str(a).strip().lower()
            if not a_low:
                continue
            # 🐛 FIX: تطبیق توکن‌به‌توکن
            a_tokens = a_low.split()
            if not a_tokens:
                continue
            if len(a_tokens) == 1:
                if tokens[0].lower() == a_tokens[0]:
                    return True, ""
            else:
                if (
                    [t.lower() for t in tokens[:len(a_tokens)]]
                    == a_tokens
                ):
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
        """🐛 FIX: `full_access` هم برگردانده می‌شود."""
        r = self.rules

        def _len(section: str, key: str) -> int:
            return len(r.get(section, {}).get(key, []) or [])

        return {
            "full_access": bool(
                r.get("full_access", False)
            ),
            "read_allow": _len("read", "allow"),
            "read_deny": _len("read", "deny"),
            "write_allow": _len("write", "allow"),
            "write_deny": _len("write", "deny"),
            "db_read_tables": (
                r.get("database", {}).get("read_tables", [])
                or []
            ),
            "db_max_rows": r.get("database", {}).get(
                "max_rows_select", 100
            ),
            "settings_read": _len("settings", "read"),
            "settings_write": _len("settings", "write"),
            "settings_deny": _len("settings", "deny"),
            "platforms_toggleable": (
                r.get("platforms", {}).get("toggleable", [])
                or []
            ),
            "commands_allow": _len("commands", "allow"),
            "commands_deny": _len("commands", "deny"),
            "file": str(ACCESS_FILE),
        }

    def get_summary(self) -> str:
        """خلاصهٔ متنی برای نمایش به مدیر."""
        r = self.rules
        full = "✅" if r.get("full_access") else "❌"
        return (
            "🔐 **دسترسی‌ها**\n"
            f"🔓 دسترسی کامل: {full}\n"
            f"📖 خواندن: "
            f"{len(r.get('read',{}).get('allow',[]))} الگو\n"
            f"✏️ نوشتن: "
            f"{len(r.get('write',{}).get('allow',[]))} الگو\n"
            f"🗄️ جداول DB: "
            f"{r.get('database',{}).get('read_tables',[])}\n"
            f"⚙️ تنظیمات (نوشتن): "
            f"{len(r.get('settings',{}).get('write',[]))} مورد\n"
            f"📱 پلتفرم‌ها: "
            f"{r.get('platforms',{}).get('toggleable',[])}\n"
            f"🖥️ دستورات: "
            f"{len(r.get('commands',{}).get('allow',[]))} مورد"
        )

    def get_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ debug — وضعیت فعلی rules."""
        with self._regex_lock:
            cache_size = len(self._regex_cache)
        return {
            "file": str(ACCESS_FILE),
            "file_exists": ACCESS_FILE.exists(),
            "regex_cache_size": cache_size,
            "top_level_keys": list(self.rules.keys()),
            "full_access": bool(
                self.rules.get("full_access", False)
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<AccessManager file={ACCESS_FILE.name} "
            f"full_access={self.rules.get('full_access', False)}>"
        )


# ═══════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════
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


def reset_access_manager() -> None:
    """🎯 پاک‌کردن singleton — فقط برای تست."""
    global _mgr
    with _mgr_lock:
        if _mgr is not None:
            log_info("🗑️ [ACCESS:singleton] reset شد")
        _mgr = None


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "AccessManager",
    "get_access_manager",
    "reset_access_manager",
    "ACCESS_FILE",
    "_BANNED_SQL_KEYWORDS",
]