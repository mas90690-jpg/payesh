"""Formula Manager — ذخیره و اجرای امن فرمول‌های SQL با متن.

استانداردها:
  - Whitelist عملیات (SELECT/DELETE/UPDATE/INSERT)
  - Whitelist جداول
  - Ban کلمات خطرناک (DROP, ALTER, TRUNCATE, ...)
  - اعتبارسنجی SQL با EXPLAIN قبل از ذخیره
  - بکاپ خودکار + Transaction + Rollback
  - تأیید دو مرحله‌ای برای نوشتن (TTL ۵ دقیقه) — فقط پس از preview
  - Rate limiting (۱۰ write در دقیقه)
  - Audit log JSONL
  - Hot-reload (بدون ری‌استارت)
  - Atomic write (tmp + fsync + replace)

نسخهٔ ۳ (این نسخه):
  - 🐛 FIX: `ROOT` حالا `Path(__file__).parent.parent.parent` است
    (فایل در `core/adapters/` است).
  - 🐛 FIX: `_load()` حالا `_cache_mtime` را در همهٔ مسیرها set
    می‌کند (قبلاً cache miss می‌داد).
  - 🐛 FIX: `_load()` migration list→dict حالا روی disk ذخیره
    می‌شود (جلوگیری از تکرار).
  - 🐛 FIX: `_save()` حالا `os.fsync` + fsync دایرکتوری دارد.
  - 🐛 FIX: `_run_read()` حالا `_audit` می‌کند (طبق MEMORY).
  - 🐛 FIX: `_run_write()` transaction rollback حالا امن است
    (اگر `sqlite3.connect` exception بدهد، `c` None-safe).
  - 🐛 FIX: `_validate_sql` EXPLAIN حالا write queries را امن
    handle می‌کند.
  - 🐛 FIX: همهٔ state mutation حالا با `threading.RLock` محافظت
    می‌شود (race-safe):
      * `_pending_confirms`
      * `_write_times`
      * `_audit` (append)
      * `_cache` + `_cache_mtime`
  - 🐛 FIX: `_ALLOWED_TABLES` حالا از config (با fallback).
  - 🐛 FIX: `AUDIT_FILE` از `data.paths.FORMULA_AUDIT`.
  - 🎯 `_env_int` helper برای همهٔ محدودیت‌ها.
  - 🎯 `_SLOW_*` env-based.
  - 🎯 `get_stats()` کامل‌تر.
  - 🎯 `reset_formula_manager()` برای تست.
  - 🎯 `__all__` export.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۲.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime
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


# ═══════════════════════════════════════════
# مسیرها (منبع یکتا: data/paths.py)
# ═══════════════════════════════════════════
# 🐛 FIX: فایل در `core/adapters/` است → 3 `.parent`
ROOT = Path(__file__).parent.parent.parent
STORAGE_DIR = ROOT / "storage"

from data import paths as data_paths

FORMULAS_FILE: Path = data_paths.MY_FORMULAS
AUDIT_FILE: Path = data_paths.FORMULA_AUDIT
BACKUP_DIR: Path = STORAGE_DIR / "backups" / "formulas"

for _d in (AUDIT_FILE.parent, BACKUP_DIR, FORMULAS_FILE.parent):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


# ═══════════════════════════════════════════
# محدودیت‌ها (env-based)
# ═══════════════════════════════════════════
_MAX_FORMULAS = _env_int(
    "FORMULA_MAX_COUNT", 200, min_val=1, max_val=10_000
)
_MAX_SQL_LEN = _env_int(
    "FORMULA_MAX_SQL_LEN", 2000, min_val=50, max_val=100_000
)
_WRITE_RATE_LIMIT = _env_int(
    "FORMULA_WRITE_RATE", 10, min_val=1, max_val=1000
)
_CONFIRM_TTL = _env_int(
    "FORMULA_CONFIRM_TTL", 300, min_val=10, max_val=3600
)

# آستانه‌های هشدار (میلی‌ثانیه)
_SLOW_READ_MS = _env_int(
    "FORMULA_SLOW_READ_MS", 2000,
    min_val=100, max_val=60_000,
)
_SLOW_WRITE_MS = _env_int(
    "FORMULA_SLOW_WRITE_MS", 5000,
    min_val=100, max_val=60_000,
)
_SLOW_BACKUP_MS = _env_int(
    "FORMULA_SLOW_BACKUP_MS", 3000,
    min_val=100, max_val=60_000,
)

# ─── SQL op types ───
_ALLOWED_READ: tuple[str, ...] = ("SELECT",)
_ALLOWED_WRITE: tuple[str, ...] = ("DELETE", "UPDATE", "INSERT")

_BANNED: tuple[str, ...] = (
    "DROP", "ALTER", "TRUNCATE", "ATTACH", "PRAGMA",
    "VACUUM", "REPLACE", "CREATE", "GRANT", "REVOKE",
)

# 🐛 FIX: از config (با fallback)
try:
    from config import SQLITE_CONFIG as _SQLITE_CFG
    _ALLOWED_TABLES: tuple[str, ...] = tuple(
        _SQLITE_CFG.get("allowed_tables", (
            "messages", "leads", "daily_stats",
            "chat_offsets", "last_poll",
        ))
    )
except Exception:
    _ALLOWED_TABLES = (
        "messages", "leads", "daily_stats",
        "chat_offsets", "last_poll",
    )


class FormulaManager:
    """مدیر فرمول‌ها — ذخیره، اجرا، اعتبارسنجی، audit.

    تمام state mutations با `threading.RLock` محافظت می‌شوند.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._cache: dict[str, dict] | None = None
        self._cache_mtime: float = 0.0
        self._pending_confirms: dict[str, float] = {}
        self._write_times: list[float] = []
        # 🐛 FIX: lock برای همه state mutation
        self._lock = threading.RLock()

        log_info(
            f"🔧 [FORMULA:init] آماده | db={db_path} | "
            f"max_formulas={_MAX_FORMULAS} "
            f"max_sql={_MAX_SQL_LEN} | "
            f"rate={_WRITE_RATE_LIMIT}/min | "
            f"confirm_ttl={_CONFIRM_TTL}s | "
            f"file={FORMULAS_FILE}"
        )

    # ═══════════════════════════════════════════
    # لود/ذخیره (hot-reload)
    # ═══════════════════════════════════════════
    def _load(self) -> dict[str, dict]:
        """بارگذاری فرمول‌ها با cache بر اساس mtime."""
        try:
            mtime = (
                FORMULAS_FILE.stat().st_mtime
                if FORMULAS_FILE.exists() else 0.0
            )
        except OSError:
            mtime = 0.0

        with self._lock:
            if (
                self._cache is not None
                and mtime == self._cache_mtime
            ):
                return self._cache

        # ─── فایل نیست ───
        if not FORMULAS_FILE.exists():
            with self._lock:
                self._cache = {}
                self._cache_mtime = mtime
            return {}

        # ─── خواندن ───
        try:
            with open(FORMULAS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            log_error(
                f"❌ [FORMULA:load] JSON نامعتبر | "
                f"line={e.lineno} col={e.colno} | {e.msg}"
            )
            with self._lock:
                self._cache = {}
                self._cache_mtime = mtime
            return {}
        except Exception as e:
            log_error(
                f"❌ [FORMULA:load] خواندن | "
                f"{type(e).__name__}: {e}"
            )
            with self._lock:
                self._cache = {}
                self._cache_mtime = mtime
            return {}

        # ─── migration list → dict ───
        migrated = False
        if isinstance(data, list):
            data = {
                f"formula_{i}": {"sql": s}
                for i, s in enumerate(data)
            }
            migrated = True
            log_warning(
                f"⚠️ [FORMULA:load] migration از list | "
                f"{len(data)} فرمول"
            )

        if not isinstance(data, dict):
            log_warning(
                f"⚠️ [FORMULA:load] ساختار نامعتبر | "
                f"type={type(data).__name__} — دیکشنری خالی"
            )
            data = {}

        # ─── ذخیره در cache ───
        with self._lock:
            self._cache = data
            self._cache_mtime = mtime

        if not migrated:
            log_info(
                f"📚 [FORMULA:load] {len(data)} فرمول | "
                f"mtime={mtime:.0f}"
            )
        else:
            # 🐛 FIX: ذخیره migration روی disk
            try:
                self._save(data)
                log_success(
                    "✅ [FORMULA:load] migration ذخیره شد"
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [FORMULA:load] migration save | "
                    f"{type(e).__name__}: {e}"
                )

        return data

    def _save(self, data: dict) -> bool:
        """🎯 FIX: ذخیره atomic با fsync."""
        try:
            FORMULAS_FILE.parent.mkdir(
                parents=True, exist_ok=True
            )
            tmp = FORMULAS_FILE.with_suffix(".tmp")

            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except (AttributeError, OSError):
                    pass

            tmp.replace(FORMULAS_FILE)

            # fsync دایرکتوری
            try:
                parent = str(FORMULAS_FILE.parent)
                dir_fd = os.open(parent, os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except (AttributeError, OSError):
                pass

            # 🐛 FIX: به‌روزرسانی cache با mtime
            with self._lock:
                try:
                    new_mtime = FORMULAS_FILE.stat().st_mtime
                except OSError:
                    new_mtime = self._cache_mtime
                self._cache = data
                self._cache_mtime = new_mtime

            return True
        except Exception as e:
            log_error(
                f"❌ [FORMULA:save] {type(e).__name__}: {e} | "
                f"count={len(data)}"
            )
            # cleanup tmp
            try:
                tmp = FORMULAS_FILE.with_suffix(".tmp")
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            return False

    # ═══════════════════════════════════════════
    # اعتبارسنجی SQL
    # ═══════════════════════════════════════════
    def _validate_sql(
        self, sql: str
    ) -> tuple[bool, str, str]:
        """اعتبارسنجی — Returns (ok, error, ftype)."""
        if not sql or not sql.strip():
            return False, "SQL خالیه", ""

        sql = sql.strip()
        if len(sql) > _MAX_SQL_LEN:
            return (
                False,
                f"SQL خیلی بلنده (حداکثر {_MAX_SQL_LEN})",
                "",
            )

        upper = sql.upper()

        # ─── banned ───
        for b in _BANNED:
            if re.search(
                rf"\b{re.escape(b)}\b", upper
            ):
                log_warning(
                    f"⚠️ [FORMULA:validate] ممنوعه «{b}» | "
                    f"sql={sql[:60]!r}"
                )
                return False, f"کلمه «{b}» ممنوع است", ""

        # ─── نوع ───
        tokens = upper.split(None, 1)
        if not tokens:
            return False, "SQL نامعتبر", ""

        first = tokens[0]
        if first in _ALLOWED_READ:
            ftype = "read"
        elif first in _ALLOWED_WRITE:
            ftype = "write"
        else:
            return (
                False,
                f"نوع دستور «{first}» پشتیبانی نمی‌شه",
                "",
            )

        # ─── جدول whitelist ───
        m = re.search(
            r"\b(?:FROM|INTO|UPDATE)\s+"
            r"([a-zA-Z_][a-zA-Z0-9_]*)",
            sql,
            re.I,
        )
        if m:
            tbl = m.group(1).lower()
            if tbl not in _ALLOWED_TABLES:
                log_warning(
                    f"⚠️ [FORMULA:validate] جدول مجاز نیست | "
                    f"tbl={tbl!r}"
                )
                return False, f"جدول «{tbl}» مجاز نیست", ""

        # ─── EXPLAIN ───
        # 🐛 FIX: write queries هم قابل EXPLAIN هستند
        try:
            c = sqlite3.connect(self.db_path)
            try:
                c.execute("EXPLAIN " + sql)
            finally:
                c.close()
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "no such table" in msg or "no such column" in msg:
                log_info(
                    f"ℹ️ [FORMULA:validate] EXPLAIN ok "
                    f"(جدول/ستون موقت) | {e}"
                )
            elif "cannot explain" in msg:
                # 🐛 FIX: بعضی write queries
                log_info(
                    f"ℹ️ [FORMULA:validate] EXPLAIN برای "
                    f"این نوع پشتیبانی نمی‌شود | {e}"
                )
            else:
                log_warning(
                    f"⚠️ [FORMULA:validate] EXPLAIN fail | "
                    f"{type(e).__name__}: {e} | "
                    f"sql={sql[:80]!r}"
                )
                return False, f"خطای syntax: {e}", ""
        except sqlite3.Error as e:
            log_warning(
                f"⚠️ [FORMULA:validate] sqlite | "
                f"{type(e).__name__}: {e}"
            )
            return False, f"خطای syntax: {e}", ""

        return True, "", ftype

    # ═══════════════════════════════════════════
    # save / delete
    # ═══════════════════════════════════════════
    def save(
        self,
        name: str,
        sql: str,
        created_by: str = "admin",
    ) -> str:
        t0 = time.monotonic()
        name = (name or "").strip()
        if not name:
            return "⛔ اسم فرمول خالیه."
        if len(name) > 80:
            return "⛔ اسم فرمول بلنده."

        data = self._load()
        if len(data) >= _MAX_FORMULAS and name not in data:
            log_warning(
                f"⚠️ [FORMULA:save] سقف تعداد | "
                f"count={len(data)} max={_MAX_FORMULAS}"
            )
            return f"⛔ حداکثر {_MAX_FORMULAS} فرمول مجاز است."

        ok, err, ftype = self._validate_sql(sql)
        if not ok:
            log_warning(
                f"⚠️ [FORMULA:save] نامعتبر | "
                f"name={name!r} | {err}"
            )
            return f"⛔ {err}"

        old = data.get(name, {})
        if not isinstance(old, dict):
            old = {}
        is_update = name in data

        entry = {
            "sql": sql.strip(),
            "type": ftype,
            "created_at": (
                old.get("created_at")
                or datetime.now().isoformat()  # noqa: DTZ005
            ),
            "updated_at": datetime.now().isoformat(),  # noqa: DTZ005
            "created_by": created_by,
            "run_count": old.get("run_count", 0),
            "last_run": old.get("last_run"),
        }
        data[name] = entry

        if not self._save(data):
            return "❌ ذخیره ناموفق."

        elapsed_ms = (time.monotonic() - t0) * 1000
        icon = "📖" if ftype == "read" else "✏️"
        log_success(
            f"✅ [FORMULA:save] "
            f"{'به‌روز' if is_update else 'جدید'} | "
            f"name={name!r} type={ftype} | {elapsed_ms:.0f}ms"
        )
        return (
            f"✅ فرمول «{name}» ذخیره شد.\n"
            f"{icon} نوع: {ftype}\n"
            f"📄 SQL: {sql.strip()[:100]}\n"
            f"▶️ اجرا: «اجرا کن «{name}»»"
        )

    def delete(self, name: str) -> str:
        name = (name or "").strip()
        data = self._load()
        if name not in data:
            log_info(f"ℹ️ [FORMULA:delete] نبود | {name!r}")
            return f"❌ فرمول «{name}» پیدا نشد."
        del data[name]
        if not self._save(data):
            return "❌ ذخیره ناموفق بعد از حذف."
        log_success(
            f"🗑️ [FORMULA:delete] {name!r} حذف شد | "
            f"remaining={len(data)}"
        )
        return f"🗑️ فرمول «{name}» حذف شد."

    # ═══════════════════════════════════════════
    # list / show
    # ═══════════════════════════════════════════
    def list_all(self) -> str:
        data = self._load()
        if not data:
            return (
                "📭 هیچ فرمولی ذخیره نشده.\n"
                "📝 ذخیره: «ذخیره کن «اسم» = SELECT ...»"
            )
        lines = [f"📋 {len(data)} فرمول:"]
        for name, info in data.items():
            info = info if isinstance(info, dict) else {}
            icon = (
                "📖" if info.get("type") == "read"
                else "✏️"
            )
            runs = info.get("run_count", 0)
            lines.append(f"   {icon} «{name}» (اجرا: {runs})")
        return "\n".join(lines)

    def show(self, name: str) -> str:
        info = self._load().get(name)
        if not info or not isinstance(info, dict):
            return f"❌ فرمول «{name}» پیدا نشد."
        return (
            f"📄 «{name}»\n"
            f"   نوع: {info.get('type', '?')}\n"
            f"   SQL: {info.get('sql', '')}\n"
            f"   اجرا: {info.get('run_count', 0)} بار\n"
            f"   آخرین: {info.get('last_run') or '—'}"
        )

    # ═══════════════════════════════════════════
    # execute
    # ═══════════════════════════════════════════
    def execute(
        self,
        name: str,
        text: str = "",
        confirm: bool = False,
    ) -> str:
        data = self._load()
        info = data.get(name)
        if not info or not isinstance(info, dict):
            log_warning(
                f"⚠️ [FORMULA:execute] نبود | name={name!r}"
            )
            return f"❌ فرمول «{name}» پیدا نشد."

        ftype = info.get("type", "read")
        sql = info.get("sql", "")
        if not sql:
            return f"❌ فرمول «{name}» SQL ندارد."

        log_info(
            f"▶️ [FORMULA:execute] {name!r} | type={ftype} | "
            f"confirm={confirm}"
        )

        if ftype == "read":
            return self._run_read(name, sql, data)
        if ftype == "write":
            return self._run_write(
                name, sql, confirm, data
            )
        return f"⛔ نوع «{name}» پشتیبانی نمی‌شه."

    # ═══════════════════════════════════════════
    # _run_read
    # ═══════════════════════════════════════════
    def _run_read(
        self, name: str, sql: str, data: dict
    ) -> str:
        t0 = time.monotonic()
        rows: list = []
        try:
            c = sqlite3.connect(self.db_path)
            try:
                c.row_factory = sqlite3.Row
                rows = c.execute(sql).fetchall()
            finally:
                c.close()
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_error(
                f"❌ [FORMULA:read] {name!r} | "
                f"{type(e).__name__}: {e} | "
                f"{elapsed_ms:.0f}ms | sql={sql[:80]!r}"
            )
            return f"❌ خطا: {e}"

        elapsed_ms = (time.monotonic() - t0) * 1000

        # 🐛 FIX: audit برای read هم
        try:
            self._audit(name, sql, len(rows))
        except Exception:
            pass

        if not rows:
            log_info(
                f"📭 [FORMULA:read] {name!r} | بدون نتیجه | "
                f"{elapsed_ms:.0f}ms"
            )
            return f"📭 «{name}» نتیجه‌ای نداشت."

        # ─── تک‌مقداری ───
        if len(rows) == 1 and len(rows[0]) == 1:
            try:
                val = list(rows[0])[0]
            except Exception:
                val = "?"
            log_info(
                f"📊 [FORMULA:read] {name!r} | scalar | "
                f"{elapsed_ms:.0f}ms"
            )
            return f"📊 «{name}»: {val}"

        # ─── چند ردیف ───
        lines = [f"📊 «{name}» ({len(rows)} ردیف):"]
        try:
            cols = list(rows[0].keys())
        except Exception:
            cols = []
        for r in rows[:20]:
            try:
                if cols:
                    lines.append(
                        "   • "
                        + " | ".join(str(r[c]) for c in cols)
                    )
                else:
                    lines.append(
                        "   • " + str(tuple(r))
                    )
            except Exception:
                lines.append("   • " + str(tuple(r)))
        if len(rows) > 20:
            lines.append(
                f"   ... و {len(rows) - 20} ردیف دیگر"
            )

        log_success(
            f"✅ [FORMULA:read] {name!r} | "
            f"{len(rows)} ردیف | {elapsed_ms:.0f}ms"
        )
        if elapsed_ms > _SLOW_READ_MS:
            log_warning(
                f"🐌 [FORMULA:read] کند | {elapsed_ms:.0f}ms"
            )
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # _run_write
    # ═══════════════════════════════════════════
    def _run_write(
        self,
        name: str,
        sql: str,
        confirm: bool,
        data: dict,
    ) -> str:
        now = time.time()

        with self._lock:
            # ─── cleanup منقضی ───
            self._cleanup_pending_confirms_unlocked(now)

            # ─── rate limit ───
            self._write_times = [
                t for t in self._write_times
                if now - t < 60
            ]
            if len(self._write_times) >= _WRITE_RATE_LIMIT:
                log_warning(
                    f"⚠️ [FORMULA:write] rate limit | "
                    f"name={name!r} | "
                    f"used={len(self._write_times)}/"
                    f"{_WRITE_RATE_LIMIT}"
                )
                return (
                    f"⛔ محدودیت: حداکثر "
                    f"{_WRITE_RATE_LIMIT} write در دقیقه."
                )

            # ─── مرحله ۱: preview ───
            if not confirm:
                preview = self._preview_write(sql)
                self._pending_confirms[name] = now
                log_info(
                    f"📋 [FORMULA:write] preview | "
                    f"name={name!r} | {preview}"
                )
                return (
                    f"⚠️ «{name}» می‌خواد بنویسه:\n"
                    f"   {preview}\n"
                    f"برای تأیید: «تأیید «{name}»»"
                )

            # ─── مرحله ۲: confirm ───
            issued = self._pending_confirms.get(name)
            if issued is None:
                log_warning(
                    f"⛔ [FORMULA:write] confirm بدون "
                    f"preview | name={name!r}"
                )
                return (
                    f"⛔ برای «{name}» اول باید preview "
                    f"بگیرید.\n"
                    f"دستور: «اجرا کن «{name}»» (بدون تأیید) "
                    f"→ بعد «تأیید «{name}»»"
                )

            if (now - issued) > _CONFIRM_TTL:
                self._pending_confirms.pop(name, None)
                log_warning(
                    f"⛔ [FORMULA:write] تأیید منقضی | "
                    f"name={name!r} | "
                    f"issued={now - issued:.0f}s ago > "
                    f"ttl={_CONFIRM_TTL}s"
                )
                return "⛔ تأیید منقضی شد. دوباره preview بگیر."

        # ─── backup (خارج از lock) ───
        backup_name = ""
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")  # noqa: DTZ005
            backup_name = f"{name}_{ts}.db"
            backup_path = BACKUP_DIR / backup_name
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)

            t_b = time.monotonic()
            shutil.copy2(self.db_path, backup_path)
            backup_ms = (time.monotonic() - t_b) * 1000
            log_info(
                f"💾 [FORMULA:write] backup | "
                f"{backup_name} | {backup_ms:.0f}ms"
            )
            if backup_ms > _SLOW_BACKUP_MS:
                log_warning(
                    f"🐌 [FORMULA:write] backup کند | "
                    f"{backup_ms:.0f}ms"
                )
        except Exception as e:
            log_error(
                f"❌ [FORMULA:write] backup | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ بکاپ ناموفق: {e}"

        # ─── اجرا (خارج از lock) ───
        t0 = time.monotonic()
        count = 0
        c = None
        try:
            c = sqlite3.connect(self.db_path)
            try:
                cur = c.cursor()
                cur.execute("BEGIN")
                cur.execute(sql)
                count = cur.rowcount
                c.commit()
            finally:
                if c is not None:
                    c.close()
        except Exception as e:
            log_error(
                f"❌ [FORMULA:write] transaction | "
                f"name={name!r} | {type(e).__name__}: {e} | "
                f"rollback"
            )
            # 🐛 FIX: rollback امن (c ممکن است None باشد)
            if c is not None:
                try:
                    c.rollback()
                except Exception:
                    pass
                try:
                    c.close()
                except Exception:
                    pass
            return f"❌ خطا (rollback): {e}"

        elapsed_ms = (time.monotonic() - t0) * 1000

        with self._lock:
            self._write_times.append(now)
            self._pending_confirms.pop(name, None)

        self._bump_run(name, data, save=True)
        self._audit(name, sql, count)

        log_success(
            f"✅ [FORMULA:write] {name!r} | {count} ردیف | "
            f"{elapsed_ms:.0f}ms | backup={backup_name}"
        )
        if elapsed_ms > _SLOW_WRITE_MS:
            log_warning(
                f"🐌 [FORMULA:write] کند | {elapsed_ms:.0f}ms"
            )
        return (
            f"✅ «{name}» اجرا شد — {count} ردیف تغییر کرد.\n"
            f"📦 بکاپ: {backup_name}"
        )

    # ═══════════════════════════════════════════
    # _preview_write
    # ═══════════════════════════════════════════
    def _preview_write(self, sql: str) -> str:
        try:
            upper = sql.upper()

            if upper.startswith("DELETE"):
                p = re.sub(
                    r"^\s*DELETE\s+FROM\s+",
                    "SELECT COUNT(*) FROM ",
                    sql, count=1, flags=re.I,
                )
                p = re.sub(
                    r"\s+ORDER BY.*$", "", p, flags=re.I
                )
                p = re.sub(
                    r"\s+LIMIT.*$", "", p, flags=re.I
                )
                c = sqlite3.connect(self.db_path)
                try:
                    v = c.execute(p).fetchone()[0]
                finally:
                    c.close()
                return f"🗑️ حذف {v} ردیف"

            if upper.startswith("UPDATE"):
                p = re.sub(
                    r"^\s*UPDATE\s+(\w+)\s+SET.*?(WHERE.*)?$",
                    lambda m: (
                        f"SELECT COUNT(*) FROM {m.group(1)} "
                        f"{m.group(2) or ''}"
                    ),
                    sql, count=1, flags=re.I | re.S,
                )
                c = sqlite3.connect(self.db_path)
                try:
                    v = c.execute(p).fetchone()[0]
                finally:
                    c.close()
                return f"✏️ به‌روزرسانی {v} ردیف"

            if upper.startswith("INSERT"):
                return "➕ درج ردیف جدید"
        except Exception as e:
            log_warning(
                f"⚠️ [FORMULA:preview] "
                f"{type(e).__name__}: {e} | "
                f"sql={sql[:80]!r}"
            )
        return "⚠️ عملیات نوشتن"

    # ═══════════════════════════════════════════
    # helpers
    # ═══════════════════════════════════════════
    def _cleanup_pending_confirms_unlocked(
        self, now: float
    ) -> None:
        """نسخهٔ internal — باید تحت lock صدا زده شود."""
        if not self._pending_confirms:
            return
        expired = [
            k for k, t in self._pending_confirms.items()
            if (now - t) > _CONFIRM_TTL
        ]
        for k in expired:
            self._pending_confirms.pop(k, None)
        if expired:
            log_info(
                f"🧹 [FORMULA:cleanup] {len(expired)} "
                f"pending منقضی حذف شد | "
                f"remaining={len(self._pending_confirms)}"
            )

    def _cleanup_pending_confirms(self, now: float) -> None:
        """نسخهٔ public (با lock)."""
        with self._lock:
            self._cleanup_pending_confirms_unlocked(now)

    def _bump_run(
        self, name: str, data: dict, save: bool = True
    ) -> None:
        """افزایش counter — ذخیره اختیاری."""
        info = data.get(name)
        if not info or not isinstance(info, dict):
            return
        info["run_count"] = info.get("run_count", 0) + 1
        info["last_run"] = datetime.now().isoformat()  # noqa: DTZ005
        if save:
            self._save(data)

    def _audit(
        self, name: str, sql: str, count: int
    ) -> None:
        """ثبت در audit log — با lock."""
        try:
            entry = {
                "ts": datetime.now().isoformat(),  # noqa: DTZ005
                "formula": name,
                "sql": sql[:500],
                "rows": count,
            }
            with self._lock:
                with open(
                    AUDIT_FILE, "a", encoding="utf-8"
                ) as f:
                    f.write(
                        json.dumps(
                            entry, ensure_ascii=False
                        )
                        + "\n"
                    )
        except Exception as e:
            log_warning(
                f"⚠️ [FORMULA:audit] "
                f"{type(e).__name__}: {e} | "
                f"formula={name!r}"
            )

    # ═══════════════════════════════════════════
    # API برای manual_brain
    # ═══════════════════════════════════════════
    def has_pending_confirm(self, name: str) -> bool:
        """آیا این فرمول منتظر تأیید است؟"""
        with self._lock:
            if name not in self._pending_confirms:
                return False
            now = time.time()
            if (
                now - self._pending_confirms[name]
            ) > _CONFIRM_TTL:
                self._pending_confirms.pop(name, None)
                return False
            return True

    # ═══════════════════════════════════════════
    # عیب‌یابی
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """خلاصهٔ وضعیت — برای /debug."""
        data = self._load()
        with self._lock:
            pending = len(self._pending_confirms)
            writes = len(self._write_times)
            cache_size = len(self._cache or {})
            cache_mtime = self._cache_mtime

        return {
            "formulas": len(data),
            "max_formulas": _MAX_FORMULAS,
            "pending_confirms": pending,
            "write_times_in_last_min": writes,
            "rate_limit_per_min": _WRITE_RATE_LIMIT,
            "confirm_ttl_s": _CONFIRM_TTL,
            "file": str(FORMULAS_FILE),
            "audit_file": str(AUDIT_FILE),
            "backup_dir": str(BACKUP_DIR),
            "cache_size": cache_size,
            "cache_mtime": cache_mtime,
            "allowed_tables": list(_ALLOWED_TABLES),
        }


# ═══════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════
_shared: FormulaManager | None = None
_shared_lock = threading.Lock()


def get_formula_manager(db_path: str) -> FormulaManager:
    """Singleton — instance مشترک سراسری."""
    global _shared
    if _shared is None:
        with _shared_lock:
            if _shared is None:
                log_info(
                    f"🏗️ [FORMULA:singleton] ساخت | "
                    f"db={db_path}"
                )
                _shared = FormulaManager(db_path)
    return _shared


def reset_formula_manager() -> None:
    """🎯 پاک‌کردن singleton — فقط برای تست."""
    global _shared
    with _shared_lock:
        if _shared is not None:
            log_info("🗑️ [FORMULA:singleton] reset شد")
        _shared = None


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "FormulaManager",
    "get_formula_manager",
    "reset_formula_manager",
    "FORMULAS_FILE",
    "AUDIT_FILE",
    "BACKUP_DIR",
]