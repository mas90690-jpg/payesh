# core/formula_manager.py
"""
Formula Manager — ذخیره و اجرای امن فرمول‌های SQL با متن.

استانداردها:
  - Whitelist عملیات (SELECT/DELETE/UPDATE/INSERT)
  - Whitelist جداول (messages, leads, ...)
  - Ban کلمات خطرناک (DROP, ALTER, TRUNCATE, ...)
  - اعتبارسنجی SQL با EXPLAIN قبل از ذخیره
  - بکاپ خودکار + Transaction + Rollback
  - تأیید دو مرحله‌ای برای نوشتن (TTL ۵ دقیقه) — فقط پس از preview
  - Rate limiting (۱۰ write در دقیقه)
  - Audit log JSONL
  - Hot-reload (بدون ری‌استارت)
  - Atomic write (tmp + replace)

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX بحرانی: confirm فقط وقتی اجازه داره که قبلاً preview صادر
    شده باشه (issued is not None). قبلاً confirm مستقیم کار می‌کرد →
    بدون تأیید واقعی، write اجرا می‌شد.
  - 🐛 FIX: _run_read اتصال sqlite رو با try/finally می‌بنده (نشتی نداشته باشه)
  - 🐛 FIX: _pending_confirms با auto-cleanup — هر دور اجرا، confirmedهای
    منقضی حذف می‌شن (جلوگیری از نشتی حافظه)
  - 🐛 FIX: _audit دیگر بی‌صدا نیست
  - 🐛 FIX: _preview_write خطا را لاگ می‌کند
  - 🐛 FIX: _bump_run دیگر در read اجرا نمی‌شود (بهینه)
  - 🎯 لاگ دقیق و ساخت‌یافته با پیشوند [FORMULA:...]:
      * init: db_path + limits
      * load: تعداد فرمول + mtime + migration
      * save/delete: name + type + زمان
      * execute: name + type + زمان + تعداد rows
      * write: preview + rate-check + backup + transaction + audit
      * خطاها: type(e).__name__ + context
  - 🎯 ثابت‌ها از env قابل تنظیم
  - 🧹 hashlib import حذف شد (استفاده نمی‌شد)
  - 🧹 from __future__ import annotations
  - 🧹 type hints در همه‌جا
  - 🧹 آستانه‌های کندی (SLOW_*) برای هشدار
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from core.kernel.logger import log_error, log_info, log_success, log_warning

# ═══════════════════════════════════════════
# مسیرها (منبع یکتا: data/paths.py)
# ═══════════════════════════════════════════
ROOT = Path(__file__).parent.parent
STORAGE_DIR = ROOT / "storage"

from data import paths as data_paths

FORMULAS_FILE = data_paths.MY_FORMULAS
AUDIT_FILE = STORAGE_DIR / "offset" / "formula_audit.jsonl"
BACKUP_DIR = STORAGE_DIR / "backups" / "formulas"

for _d in (AUDIT_FILE.parent, BACKUP_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════
# محدودیت‌ها (قابل تنظیم از env)
# ═══════════════════════════════════════════
_MAX_FORMULAS = max(1, int(os.getenv("FORMULA_MAX_COUNT", "200")))
_MAX_SQL_LEN = max(50, int(os.getenv("FORMULA_MAX_SQL_LEN", "2000")))
_WRITE_RATE_LIMIT = max(1, int(os.getenv("FORMULA_WRITE_RATE", "10")))
_CONFIRM_TTL = max(10, int(os.getenv("FORMULA_CONFIRM_TTL", "300")))  # ۵ دقیقه
_ALLOWED_READ = ("SELECT",)
_ALLOWED_WRITE = ("DELETE", "UPDATE", "INSERT")
_BANNED = (
    "DROP", "ALTER", "TRUNCATE", "ATTACH", "PRAGMA", "VACUUM",
    "REPLACE", "CREATE", "GRANT", "REVOKE",
)
_ALLOWED_TABLES = ("messages", "leads", "daily_stats",
                   "chat_offsets", "last_poll")

# آستانه‌های هشدار (میلی‌ثانیه)
_SLOW_READ_MS = 2000
_SLOW_WRITE_MS = 5000
_SLOW_BACKUP_MS = 3000


class FormulaManager:
    """مدیر فرمول‌ها — ذخیره، اجرا، اعتبارسنجی، audit."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._cache: dict[str, dict] | None = None
        self._cache_mtime: float = 0.0
        self._pending_confirms: dict[str, float] = {}
        self._write_times: list[float] = []

        log_info(
            f"🔧 [FORMULA:init] آماده | db={db_path} | "
            f"max_formulas={_MAX_FORMULAS} max_sql={_MAX_SQL_LEN} | "
            f"rate={_WRITE_RATE_LIMIT}/min | confirm_ttl={_CONFIRM_TTL}s | "
            f"file={FORMULAS_FILE}"
        )

    # ═══════════════════════════════════════════
    # لود/ذخیره (hot-reload)
    # ═══════════════════════════════════════════
    def _load(self) -> dict[str, dict]:
        """بارگذاری فرمول‌ها با cache بر اساس mtime."""
        try:
            mtime = FORMULAS_FILE.stat().st_mtime if FORMULAS_FILE.exists() else 0.0
        except OSError:
            mtime = 0.0

        if self._cache is not None and mtime == self._cache_mtime:
            return self._cache

        if not FORMULAS_FILE.exists():
            self._cache, self._cache_mtime = {}, mtime
            return self._cache

        try:
            with open(FORMULAS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            log_error(
                f"❌ [FORMULA:load] JSON نامعتبر | "
                f"line={e.lineno} col={e.colno} | {e.msg}"
            )
            self._cache = {}
            return self._cache
        except Exception as e:
            log_error(
                f"❌ [FORMULA:load] خواندن | {type(e).__name__}: {e}"
            )
            self._cache = {}
            return self._cache

        # migration از list قدیمی به dict
        migrated = False
        if isinstance(data, list):
            data = {f"formula_{i}": {"sql": s} for i, s in enumerate(data)}
            migrated = True
            log_warning(
                f"⚠️ [FORMULA:load] migration از list به dict | "
                f"{len(data)} فرمول"
            )
        if not isinstance(data, dict):
            log_warning(
                f"⚠️ [FORMULA:load] ساختار نامعتبر | "
                f"type={type(data).__name__} — دیکشنری خالی"
            )
            data = {}

        self._cache, self._cache_mtime = data, mtime
        if not migrated:
            log_info(
                f"📚 [FORMULA:load] {len(data)} فرمول بارگذاری شد | "
                f"mtime={mtime:.0f}"
            )
        return data

    def _save(self, data: dict) -> bool:
        """ذخیره atomic (tmp + replace)."""
        try:
            FORMULAS_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = FORMULAS_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(FORMULAS_FILE)
            self._cache, self._cache_mtime = data, FORMULAS_FILE.stat().st_mtime
            return True
        except Exception as e:
            log_error(
                f"❌ [FORMULA:save] {type(e).__name__}: {e} | "
                f"count={len(data)}"
            )
            return False

    # ═══════════════════════════════════════════
    # اعتبارسنجی SQL
    # ═══════════════════════════════════════════
    def _validate_sql(self, sql: str) -> tuple[bool, str, str]:
        """اعتبارسنجی — Returns (ok, error, ftype)."""
        if not sql or not sql.strip():
            return False, "SQL خالیه", ""

        sql = sql.strip()
        if len(sql) > _MAX_SQL_LEN:
            return False, f"SQL خیلی بلنده (حداکثر {_MAX_SQL_LEN})", ""

        upper = sql.upper()

        # banned words
        for b in _BANNED:
            if re.search(rf"\b{re.escape(b)}\b", upper):
                log_warning(
                    f"⚠️ [FORMULA:validate] کلمه ممنوعه «{b}» | "
                    f"sql={sql[:60]!r}"
                )
                return False, f"کلمه «{b}» ممنوع است", ""

        # نوع دستور
        tokens = upper.split(None, 1)
        if not tokens:
            return False, "SQL نامعتبر", ""
        first = tokens[0]
        if first in _ALLOWED_READ:
            ftype = "read"
        elif first in _ALLOWED_WRITE:
            ftype = "write"
        else:
            return False, f"نوع دستور «{first}» پشتیبانی نمی‌شه", ""

        # جدول whitelist
        m = re.search(
            r"\b(?:FROM|INTO|UPDATE)\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, re.I
        )
        if m:
            tbl = m.group(1).lower()
            if tbl not in _ALLOWED_TABLES:
                log_warning(
                    f"⚠️ [FORMULA:validate] جدول مجاز نیست | "
                    f"tbl={tbl!r}"
                )
                return False, f"جدول «{tbl}» مجاز نیست", ""

        # EXPLAIN روی دیتابیس واقعی
        try:
            c = sqlite3.connect(self.db_path)
            try:
                c.execute("EXPLAIN " + sql)
            finally:
                c.close()
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            # جدول/ستون نبود = syntax سالم
            if "no such table" in msg or "no such column" in msg:
                log_info(
                    f"ℹ️ [FORMULA:validate] EXPLAIN ok (جدول/ستون موقت) | "
                    f"{e}"
                )
            else:
                log_warning(
                    f"⚠️ [FORMULA:validate] EXPLAIN fail | "
                    f"{type(e).__name__}: {e} | sql={sql[:80]!r}"
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
    # save
    # ═══════════════════════════════════════════
    def save(self, name: str, sql: str, created_by: str = "admin") -> str:
        t0 = time.monotonic()
        name = (name or "").strip()
        if not name:
            return "⛔ اسم فرمول خالیه."
        if len(name) > 80:
            return "⛔ اسم فرمول بلنده."

        data = self._load()
        if len(data) >= _MAX_FORMULAS and name not in data:
            log_warning(
                f"⚠️ [FORMULA:save] تعداد فرمول‌ها به سقف رسیده | "
                f"count={len(data)} max={_MAX_FORMULAS}"
            )
            return f"⛔ حداکثر {_MAX_FORMULAS} فرمول مجاز است."

        ok, err, ftype = self._validate_sql(sql)
        if not ok:
            log_warning(
                f"⚠️ [FORMULA:save] نامعتبر | name={name!r} | {err}"
            )
            return f"⛔ {err}"

        old = data.get(name, {})
        is_update = name in data

        entry = {
            "sql": sql.strip(),
            "type": ftype,
            "created_at": old.get("created_at") or datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
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
            f"✅ [FORMULA:save] {'به‌روز' if is_update else 'جدید'} | "
            f"name={name!r} type={ftype} | {elapsed_ms:.0f}ms"
        )
        return (
            f"✅ فرمول «{name}» ذخیره شد.\n"
            f"{icon} نوع: {ftype}\n"
            f"📄 SQL: {sql.strip()[:100]}\n"
            f"▶️ اجرا: «اجرا کن «{name}»»"
        )

    # ═══════════════════════════════════════════
    # delete
    # ═══════════════════════════════════════════
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
            return ("📭 هیچ فرمولی ذخیره نشده.\n"
                    "📝 ذخیره: «ذخیره کن «اسم» = SELECT ...»")
        lines = [f"📋 {len(data)} فرمول:"]
        for name, info in data.items():
            icon = "📖" if info.get("type") == "read" else "✏️"
            runs = info.get("run_count", 0)
            lines.append(f"   {icon} «{name}» (اجرا: {runs})")
        return "\n".join(lines)

    def show(self, name: str) -> str:
        info = self._load().get(name)
        if not info:
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
        self, name: str, text: str = "", confirm: bool = False
    ) -> str:
        data = self._load()
        info = data.get(name)
        if not info:
            log_warning(f"⚠️ [FORMULA:execute] نبود | name={name!r}")
            return f"❌ فرمول «{name}» پیدا نشد."

        ftype = info.get("type", "read")
        log_info(
            f"▶️ [FORMULA:execute] {name!r} | type={ftype} | "
            f"confirm={confirm}"
        )

        if ftype == "read":
            return self._run_read(name, info["sql"], data)
        if ftype == "write":
            return self._run_write(name, info["sql"], confirm, data)
        return f"⛔ نوع «{name}» پشتیبانی نمی‌شه."

    # ═══════════════════════════════════════════
    # _run_read
    # ═══════════════════════════════════════════
    def _run_read(self, name: str, sql: str, data: dict) -> str:
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
                f"{type(e).__name__}: {e} | {elapsed_ms:.0f}ms | "
                f"sql={sql[:80]!r}"
            )
            return f"❌ خطا: {e}"

        elapsed_ms = (time.monotonic() - t0) * 1000
        self._bump_run(name, data, save=False)  # فقط در RAM

        if not rows:
            log_info(
                f"📭 [FORMULA:read] {name!r} | بدون نتیجه | "
                f"{elapsed_ms:.0f}ms"
            )
            return f"📭 «{name}» نتیجه‌ای نداشت."

        # تک‌مقداری
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

        # چند ردیف
        lines = [f"📊 «{name}» ({len(rows)} ردیف):"]
        try:
            cols = list(rows[0].keys())
        except Exception:
            cols = []
        for r in rows[:20]:
            try:
                lines.append("   • " + " | ".join(str(r[c]) for c in cols))
            except Exception:
                lines.append("   • " + str(tuple(r)))
        if len(rows) > 20:
            lines.append(f"   ... و {len(rows)-20} ردیف دیگر")

        log_success(
            f"✅ [FORMULA:read] {name!r} | {len(rows)} ردیف | "
            f"{elapsed_ms:.0f}ms"
        )
        if elapsed_ms > _SLOW_READ_MS:
            log_warning(
                f"🐌 [FORMULA:read] کند | {elapsed_ms:.0f}ms"
            )
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # _run_write — با تأیید دو مرحله‌ای
    # ═══════════════════════════════════════════
    def _run_write(
        self, name: str, sql: str, confirm: bool, data: dict
    ) -> str:
        now = time.time()

        # ─── cleanup pending منقضی (جلوگیری از نشتی حافظه) ───
        self._cleanup_pending_confirms(now)

        # ─── rate limit ───
        self._write_times = [
            t for t in self._write_times if now - t < 60
        ]
        if len(self._write_times) >= _WRITE_RATE_LIMIT:
            log_warning(
                f"⚠️ [FORMULA:write] rate limit | name={name!r} | "
                f"used={len(self._write_times)}/{_WRITE_RATE_LIMIT}"
            )
            return f"⛔ محدودیت: حداکثر {_WRITE_RATE_LIMIT} write در دقیقه."

        # ─── مرحله ۱: preview (بدون confirm) ───
        if not confirm:
            preview = self._preview_write(sql)
            self._pending_confirms[name] = now
            log_info(
                f"📋 [FORMULA:write] preview | name={name!r} | "
                f"{preview}"
            )
            return (
                f"⚠️ «{name}» می‌خواد بنویسه:\n"
                f"   {preview}\n"
                f"برای تأیید: «تأیید «{name}»»"
            )

        # ─── مرحله ۲: confirm — ولی فقط اگه preview صادر شده باشه ───
        # 🐛 FIX: چک اجباری — قبلاً اگه کاربر مستقیم confirm می‌زد،
        # بدون preview اجازه می‌گرفت.
        issued = self._pending_confirms.get(name)
        if issued is None:
            log_warning(
                f"⛔ [FORMULA:write] confirm بدون preview | name={name!r}"
            )
            return (
                f"⛔ برای «{name}» اول باید preview بگیرید.\n"
                f"دستور: «اجرا کن «{name}»» (بدون تأیید) → بعد «تأیید «{name}»»"
            )

        # چک TTL
        if (now - issued) > _CONFIRM_TTL:
            self._pending_confirms.pop(name, None)
            log_warning(
                f"⛔ [FORMULA:write] تأیید منقضی | name={name!r} | "
                f"issued={now - issued:.0f}s ago > ttl={_CONFIRM_TTL}s"
            )
            return "⛔ تأیید منقضی شد. دوباره preview بگیر."

        # ─── backup ───
        backup_name = ""
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{name}_{ts}.db"
            backup_path = BACKUP_DIR / backup_name
            t_b = time.monotonic()
            shutil.copy2(self.db_path, backup_path)
            backup_ms = (time.monotonic() - t_b) * 1000
            log_info(
                f"💾 [FORMULA:write] backup | {backup_name} | "
                f"{backup_ms:.0f}ms"
            )
            if backup_ms > _SLOW_BACKUP_MS:
                log_warning(
                    f"🐌 [FORMULA:write] backup کند | {backup_ms:.0f}ms"
                )
        except Exception as e:
            log_error(
                f"❌ [FORMULA:write] backup | {type(e).__name__}: {e}"
            )
            return f"❌ بکاپ ناموفق: {e}"

        # ─── اجرا با transaction ───
        t0 = time.monotonic()
        count = 0
        try:
            c = sqlite3.connect(self.db_path)
            try:
                cur = c.cursor()
                cur.execute("BEGIN")
                cur.execute(sql)
                count = cur.rowcount
                c.commit()
            finally:
                c.close()
        except Exception as e:
            log_error(
                f"❌ [FORMULA:write] transaction | name={name!r} | "
                f"{type(e).__name__}: {e} | rollback"
            )
            try:
                c.rollback()
                c.close()
            except Exception:
                pass
            return f"❌ خطا (rollback): {e}"

        elapsed_ms = (time.monotonic() - t0) * 1000
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
                p = re.sub(r"\s+ORDER BY.*$", "", p, flags=re.I)
                p = re.sub(r"\s+LIMIT.*$", "", p, flags=re.I)
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
                f"⚠️ [FORMULA:preview] {type(e).__name__}: {e} | "
                f"sql={sql[:80]!r}"
            )
        return "⚠️ عملیات نوشتن"

    # ═══════════════════════════════════════════
    # helpers
    # ═══════════════════════════════════════════
    def _cleanup_pending_confirms(self, now: float) -> None:
        """حذف confirmedهای منقضی (جلوگیری از نشتی حافظه)."""
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
                f"🧹 [FORMULA:cleanup] {len(expired)} pending منقضی حذف شد | "
                f"remaining={len(self._pending_confirms)}"
            )

    def _bump_run(self, name: str, data: dict, save: bool = True):
        """افزایش counter — ذخیره اختیاری (read = بدون ذخیره)."""
        info = data.get(name)
        if not info:
            return
        info["run_count"] = info.get("run_count", 0) + 1
        info["last_run"] = datetime.now().isoformat()
        if save:
            self._save(data)

    def _audit(self, name: str, sql: str, count: int) -> None:
        """ثبت در audit log — خطا با type لاگ می‌شود."""
        try:
            entry = {
                "ts": datetime.now().isoformat(),
                "formula": name,
                "sql": sql[:500],
                "rows": count,
            }
            with open(AUDIT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            log_warning(
                f"⚠️ [FORMULA:audit] {type(e).__name__}: {e} | "
                f"formula={name!r}"
            )

    # ═══════════════════════════════════════════
    # API برای manual_brain
    # ═══════════════════════════════════════════
    def has_pending_confirm(self, name: str) -> bool:
        """آیا این فرمول منتظر تأیید است؟"""
        if name not in self._pending_confirms:
            return False
        # چک TTL
        now = time.time()
        if (now - self._pending_confirms[name]) > _CONFIRM_TTL:
            self._pending_confirms.pop(name, None)
            return False
        return True

    # ═══════════════════════════════════════════
    # عیب‌یابی
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """خلاصهٔ وضعیت — برای /debug."""
        data = self._load()
        return {
            "formulas": len(data),
            "max_formulas": _MAX_FORMULAS,
            "pending_confirms": len(self._pending_confirms),
            "write_times_in_last_min": len(self._write_times),
            "rate_limit_per_min": _WRITE_RATE_LIMIT,
            "file": str(FORMULAS_FILE),
            "audit_file": str(AUDIT_FILE),
        }


# ═══════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════
_shared: FormulaManager | None = None


def get_formula_manager(db_path: str) -> FormulaManager:
    global _shared
    if _shared is None:
        log_info(
            f"🏗️ [FORMULA:singleton] ساخت نمونهٔ اول | db={db_path}"
        )
        _shared = FormulaManager(db_path)
    return _shared
