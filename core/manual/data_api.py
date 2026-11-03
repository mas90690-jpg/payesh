# core/manual/data_api.py
"""
API دسترسی کامل به داده — برای استفادهٔ هوش دستی.

اصل طراحی:
  - همهٔ متدها sync هستند و سریع (SQLite + فایل‌های کوچک).
  - همهٔ متدها در صورت خطا، مقدار امن برمی‌گردانند (0/[]/{}/None).
  - فقط SELECT از دیتابیس، بدون هیچ نوشتنی.
  - پشتیبانی از هر دو فرمت keywords_custom.json:
      فرمت قدیمی: ["کود", "سم"]
      فرمت جدید: {"version": "...", "keywords": {"کود": {"tier": "T1"}}}

نسخهٔ ۴ (این نسخه):
  - 🎯 استفاده از `data.paths` برای همهٔ مسیرها (LOG_FILE, REPORTS_DIR, ...).
  - 🎯 `_BANNED_SQL` حالا از `access_manager._BANNED_SQL_KEYWORDS` مشتق
    می‌شود (منبع یکتا).
  - 🎯 `_SLOW_QUERY_MS`, `_LOG_TAIL_BYTES` حالا env-based.
  - 🎯 `_write_custom_kw` حالا `os.fsync` می‌زند (durability).
  - 🎯 `add_target_group` حالا atomic write + fsync.
  - 🎯 `_read_custom_kw_raw` حالا با `FileNotFoundError` جدا
    مدیریت می‌شود.
  - 🐛 FIX: `custom_keywords_with_tier` حالا `T3` را هم قبول می‌کند
    (قبلاً فقط T1/T2).
  - 🎯 `_env_int` helper برای thresholds.
  - 🎯 `platform_last_poll` حالا tz-aware هم می‌پذیرد.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۳.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from core.kernel.logger import log_error, log_info, log_success, log_warning

# 🎯 P0: مسیرها از data.paths
from data import paths as data_paths

# 🎯 P0: منبع یکتا برای banned SQL
try:
    from core.adapters.access_manager import (
        _BANNED_SQL_KEYWORDS as _ACCESS_BANNED_SQL,
    )
except ImportError:
    _ACCESS_BANNED_SQL = None


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════
def _env_int(
    name: str,
    default: int,
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


# ═══════════════════════════════════════════════════════════
# مسیرها (از data.paths)
# ═══════════════════════════════════════════════════════════
ROOT: Path = data_paths.PROJECT_ROOT
STORAGE_DIR: Path = data_paths.STORAGE_DIR
LOGS_DIR: Path = data_paths.LOGS_DIR
REPORTS_DIR: Path = data_paths.REPORTS_DIR
LOG_FILE: Path = data_paths.LOG_FILE

CUSTOM_KW: str = str(data_paths.KEYWORDS_CUSTOM)
TARGET_GROUPS: str = str(data_paths.TARGET_GROUPS)


# ═══════════════════════════════════════════════════════════
# آستانه‌ها (env-based)
# ═══════════════════════════════════════════════════════════
_LOG_TAIL_BYTES = _env_int(
    "DATAPI_LOG_TAIL_BYTES",
    512 * 1024,
    min_val=10_000,
    max_val=10_000_000,
)
_SLOW_QUERY_MS = _env_int(
    "DATAPI_SLOW_QUERY_MS",
    500,
    min_val=50,
    max_val=60_000,
)

# SQL keywords که نمی‌توانند در SELECT باشند
# 🎯 FIX: از access_manager مشتق می‌شود
if _ACCESS_BANNED_SQL:
    _BANNED_SQL: tuple[str, ...] = tuple(_ACCESS_BANNED_SQL)
else:
    _BANNED_SQL = (
        "DROP",
        "DELETE",
        "UPDATE",
        "INSERT",
        "ALTER",
        "TRUNCATE",
        "ATTACH",
        "PRAGMA",
        "VACUUM",
        "CREATE",
        "REPLACE",
        "GRANT",
        "REVOKE",
    )

# 🎯 T3 هم حالا معتبر است
_VALID_TIERS: tuple[str, ...] = ("T1", "T2", "T3")


# ═══════════════════════════════════════════════════════════
# DataAPI
# ═══════════════════════════════════════════════════════════
class DataAPI:
    """دسترسی کامل به همهٔ داده‌های ربات (خواندنی + امن)."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    # ═══════════════════════════════════════════
    # دیتابیس (SELECT فقط)
    # ═══════════════════════════════════════════
    def sql(self, query: str, params: tuple = ()) -> list[dict]:
        """اجرای کوئری SELECT. هر چیز دیگری رد می‌شود.

        Returns:
            لیست dict (اگر خطا → لیست خالی).
        """
        q = (query or "").strip()
        qu = q.upper()

        # ─── گارد امنیتی ───
        if not qu.startswith("SELECT"):
            log_warning(f"⛔ [DATAPI:sql] رد کوئری غیر SELECT | {q[:80]!r}")
            return []
        if any(kw in qu for kw in _BANNED_SQL):
            log_warning(f"⛔ [DATAPI:sql] کلمهٔ ممنوعه | {q[:80]!r}")
            return []

        t0 = time.monotonic()
        c = None
        try:
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            rows = c.execute(q, params).fetchall()
            result = [dict(r) for r in rows]

            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > _SLOW_QUERY_MS:
                log_warning(
                    f"🐌 [DATAPI:sql] کند | {elapsed_ms:.0f}ms | "
                    f"rows={len(result)} | {q[:80]!r}"
                )
            return result
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_error(
                f"❌ [DATAPI:sql] {type(e).__name__}: {e} | "
                f"{elapsed_ms:.0f}ms | {q[:80]!r} | "
                f"params={params!r}"
            )
            return []
        finally:
            if c is not None:
                with contextlib.suppress(Exception):
                    c.close()

    def scalar(self, query: str, params: tuple = ()) -> Any:
        """یک مقدار تک (اولین ستون اولین ردیف) — 0 اگر نبود."""
        rows = self.sql(query, params)
        if not rows:
            return 0
        try:
            return next(iter(rows[0].values()))
        except Exception:
            return 0

    def row(self, query: str, params: tuple = ()) -> dict | None:
        """یک ردیف یا None."""
        rows = self.sql(query, params)
        return rows[0] if rows else None

    # ═══════════════════════════════════════════
    # آمار سرنخ‌ها
    # ═══════════════════════════════════════════
    def leads_total(self) -> int:
        return int(
            self.scalar("SELECT COUNT(*) FROM messages WHERE keywords!=''") or 0)

    def leads_today(self) -> int:
        return int(
            self.scalar(
                "SELECT COUNT(*) FROM messages WHERE keywords!='' "
                "AND date(timestamp, 'localtime')="
                "date('now', 'localtime')"
            )
            or 0
        )

    def leads_yesterday(self) -> int:
        return int(
            self.scalar(
                "SELECT COUNT(*) FROM messages WHERE keywords!='' "
                "AND date(timestamp, 'localtime')="
                "date('now', 'localtime', '-1 day')"
            )
            or 0
        )

    def leads_last_n_days(self, n: int) -> int:
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 7
        return int(
            self.scalar(
                "SELECT COUNT(*) FROM messages WHERE keywords!='' "
                "AND timestamp >= datetime('now', ?)",
                (f"-{n} days",),
            )
            or 0
        )

    def leads_by_platform(self) -> list[dict]:
        return self.sql(
            "SELECT platform, COUNT(*) as n FROM messages "
            "WHERE keywords!='' GROUP BY platform ORDER BY n DESC"
        )

    def leads_by_platform_last_n(self, n: int) -> list[dict]:
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 30
        return self.sql(
            "SELECT platform, COUNT(*) as n FROM messages "
            "WHERE keywords!='' "
            "AND timestamp >= datetime('now', ?) "
            "GROUP BY platform ORDER BY n DESC",
            (f"-{n} days",),
        )

    def hot_today(self) -> int:
        return int(
            self.scalar(
                "SELECT COUNT(*) FROM messages "
                "WHERE opportunity_type='داغ' "
                "AND date(timestamp)=date('now')"
            )
            or 0
        )

    def hot_total(self) -> int:
        return int(
            self.scalar("SELECT COUNT(*) FROM messages WHERE opportunity_type='داغ'")
            or 0
        )

    def top_keywords(self, limit: int = 10, days: int = 30) -> list[dict]:
        """پرتکرارترین کلیدواژه‌ها در N روز اخیر."""
        rows = self.sql(
            "SELECT keywords FROM messages WHERE keywords!='' "
            "AND timestamp >= datetime('now', ?)",
            (f"-{int(days)} days",),
        )
        counts: dict[str, int] = {}
        for r in rows:
            for kw in (r.get("keywords") or "").split(","):
                kw = kw.strip()
                if kw:
                    counts[kw] = counts.get(kw, 0) + 1
        return [
            {"keyword": k, "count": v}
            for k, v in sorted(counts.items(), key=lambda x: -x[1])[:limit]
        ]

    def top_groups(self, limit: int = 10, days: int = 30) -> list[dict]:
        return self.sql(
            """
            SELECT chat_name, platform, COUNT(*) AS n,
                   MAX(chat_link) AS link
            FROM messages
            WHERE keywords!=''
              AND timestamp >= datetime('now', ?)
            GROUP BY chat_name, platform
            ORDER BY n DESC LIMIT ?
            """,
            (f"-{int(days)} days", int(limit)),
        )

    def last_lead(self, platform: str | None = None) -> dict | None:
        """آخرین سرنخ — با فیلتر پلتفرم اختیاری."""
        if platform:
            return self.row(
                "SELECT platform, chat_name, keywords, "
                "opportunity_type, opportunity_score, "
                "text, timestamp, chat_link "
                "FROM messages WHERE keywords!='' "
                "AND platform=? "
                "ORDER BY timestamp DESC LIMIT 1",
                (platform,),
            )
        return self.row(
            "SELECT platform, chat_name, keywords, "
            "opportunity_type, opportunity_score, "
            "text, timestamp, chat_link "
            "FROM messages WHERE keywords!='' "
            "ORDER BY timestamp DESC LIMIT 1"
        )

    def first_lead(self, platform: str | None = None) -> dict | None:
        """اولین سرنخ — با فیلتر پلتفرم اختیاری."""
        if platform:
            return self.row(
                "SELECT platform, chat_name, keywords, "
                "opportunity_type, opportunity_score, "
                "text, timestamp, chat_link "
                "FROM messages WHERE keywords!='' "
                "AND platform=? "
                "ORDER BY timestamp ASC LIMIT 1",
                (platform,),
            )
        return self.row(
            "SELECT platform, chat_name, keywords, "
            "opportunity_type, opportunity_score, "
            "text, timestamp, chat_link "
            "FROM messages WHERE keywords!='' "
            "ORDER BY timestamp ASC LIMIT 1"
        )

    def search_leads(self, term: str, limit: int = 10) -> list[dict]:
        like = f"%{term}%"
        return self.sql(
            """
            SELECT platform, chat_name, keywords, text, timestamp
            FROM messages
            WHERE keywords!=''
              AND (text LIKE ? OR keywords LIKE ?
                   OR chat_name LIKE ?)
            ORDER BY timestamp DESC LIMIT ?
            """,
            (like, like, like, int(limit)),
        )

    # ═══════════════════════════════════════════
    # وضعیت پلتفرم‌ها
    # ═══════════════════════════════════════════
    def platform_last_poll(self, platform: str) -> datetime | None:
        """🎯 FIX: حالا tz-aware هم قبول می‌کند."""
        r = self.row(
            "SELECT last_poll FROM last_poll WHERE platform=?",
            (platform,),
        )
        if not r or not r.get("last_poll"):
            return None
        try:
            ts = r["last_poll"]
            when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            # اگه aware بود → naive کن (سازگاری)
            if when.tzinfo is not None:
                when = when.replace(tzinfo=None)
            return when
        except Exception as e:
            log_warning(
                f"⚠️ [DATAPI:platform_last_poll] تاریخ نامعتبر | "
                f"platform={platform} | "
                f"value={r.get('last_poll')!r} | {e}"
            )
            return None

    # ═══════════════════════════════════════════
    # کلیدواژه‌ها
    # ═══════════════════════════════════════════
    def keyword_count(self) -> int:
        """تعداد کل کلیدواژه‌ها (اصلی + سفارشی)."""
        try:
            from analyzers.keyword_finder import get_keyword_finder

            return len(get_keyword_finder().keywords)
        except Exception as e:
            log_warning(f"⚠️ [DATAPI:keyword_count] {type(e).__name__}: {e}")
            return 0

    def keyword_list(self, limit: int = 30) -> list[str]:
        """لیست کلیدواژه‌ها (limit=0 یا منفی → همه)."""
        try:
            from analyzers.keyword_finder import get_keyword_finder

            kws = list(get_keyword_finder().keywords)
            if limit and limit > 0:
                return kws[:limit]
            return kws
        except Exception as e:
            log_warning(f"⚠️ [DATAPI:keyword_list] {type(e).__name__}: {e}")
            return []

    # ─── keywords_custom.json ───
    @staticmethod
    def _read_custom_kw_raw() -> Any:
        """محتوای خام فایل (list یا dict).

        🎯 FIX: FileNotFoundError جدا مدیریت می‌شود.
        """
        if not os.path.exists(CUSTOM_KW):
            return []
        try:
            with open(CUSTOM_KW, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return []
        except json.JSONDecodeError as e:
            log_error(
                f"❌ [DATAPI:custom_kw] JSON نامعتبر | "
                f"{CUSTOM_KW} | line={e.lineno} col={e.colno} | "
                f"{e.msg}"
            )
            return []
        except Exception as e:
            log_error(
                f"❌ [DATAPI:custom_kw] خواندن | {CUSTOM_KW} | {
                    type(e).__name__}: {e}")
            return []

    def custom_keywords_with_tier(self) -> dict[str, str]:
        """همهٔ کلیدواژه‌های سفارشی → {name: tier}.

        🎯 FIX: T3 هم قبول می‌شود (قبلاً فقط T1/T2).
        """
        data = self._read_custom_kw_raw()

        # ─── فرمت قدیمی: list ───
        if isinstance(data, list):
            return {
                str(x).strip(): "T1" for x in data if isinstance(
                    x, str) and x.strip()}

        # ─── فرمت جدید: dict ───
        if isinstance(data, dict):
            kws = data.get("keywords")
            if kws is None:
                log_warning(
                    f"⚠️ [DATAPI:custom_kw] dict بدون "
                    f"«keywords» | کلیدها: "
                    f"{list(data.keys())[:5]}"
                )
                return {}

            if isinstance(kws, dict):
                out: dict[str, str] = {}
                for k, meta in kws.items():
                    name = str(k).strip()
                    if not name:
                        continue
                    if isinstance(meta, dict):
                        tier = str(meta.get("tier", "T1")).upper()
                    elif isinstance(meta, str):
                        tier = meta.upper()
                    else:
                        tier = "T1"
                    # 🎯 FIX: T3 هم قبول
                    if tier not in _VALID_TIERS:
                        tier = "T1"
                    out[name] = tier
                return out

            if isinstance(kws, list):
                return {
                    str(x).strip(): "T1"
                    for x in kws
                    if isinstance(x, str) and x.strip()
                }

        return {}

    def custom_keywords(self) -> list[str]:
        """لیست نام کلیدواژه‌های سفارشی (بدون tier)."""
        return list(self.custom_keywords_with_tier().keys())

    @staticmethod
    def _write_custom_kw(keywords_with_tier: dict[str, str]) -> bool:
        """🎯 FIX: ذخیرهٔ کلیدواژه‌ها با tier — atomic + fsync."""
        try:
            data = {
                "version": "2.0.0",
                "updated_at": datetime.now().isoformat(),  # noqa: DTZ005
                "keywords": {
                    k.strip(): {
                        "tier": (t.upper() if t and t.upper() in _VALID_TIERS else "T1")
                    }
                    for k, t in keywords_with_tier.items()
                    if k and k.strip()
                },
            }

            parent = os.path.dirname(CUSTOM_KW)
            if parent:
                os.makedirs(parent, exist_ok=True)

            tmp = CUSTOM_KW + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except (AttributeError, OSError) as e:
                    log_warning(
                        f"⚠️ [core.manual.data_api] {
                            type(e).__name__}: {e}")
            os.replace(tmp, CUSTOM_KW)

            # fsync دایرکتوری (POSIX)
            if parent:
                try:
                    dir_fd = os.open(parent, os.O_DIRECTORY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except (AttributeError, OSError) as e:
                    log_warning(
                        f"⚠️ [core.manual.data_api] {
                            type(e).__name__}: {e}")

            log_info(
                f"💾 [DATAPI:custom_kw] ذخیره شد | "
                f"count={len(data['keywords'])} | {CUSTOM_KW}"
            )
            return True
        except Exception as e:
            log_error(
                f"❌ [DATAPI:custom_kw] نوشتن | {CUSTOM_KW} | {
                    type(e).__name__}: {e}")
            return False

    def add_custom_keyword(self, kw: str, tier: str = "T1") -> bool:
        """افزودن کلیدواژهٔ سفارشی."""
        kw = (kw or "").strip()
        if not kw:
            log_warning("⚠️ [DATAPI:add_kw] کلیدواژهٔ خالی رد شد")
            return False

        current = self.custom_keywords_with_tier()
        if kw in current:
            log_info(
                f"ℹ️ [DATAPI:add_kw] «{kw}» از قبل هست (tier={
                    current[kw]}) — رد شد")
            return False

        # اعتبارسنجی tier
        tier_clean = (tier or "T1").upper()
        if tier_clean not in _VALID_TIERS:
            tier_clean = "T1"
        current[kw] = tier_clean

        ok = self._write_custom_kw(current)
        if ok:
            log_success(
                f"✅ [DATAPI:add_kw] «{kw}» اضافه شد | "
                f"tier={tier_clean} | total={len(current)}"
            )
        return ok

    def remove_custom_keyword(self, kw: str) -> bool:
        """حذف کلیدواژهٔ سفارشی — سایر tierها حفظ می‌شوند."""
        kw = (kw or "").strip()
        if not kw:
            log_warning("⚠️ [DATAPI:remove_kw] کلیدواژهٔ خالی رد شد")
            return False

        current = self.custom_keywords_with_tier()
        if kw not in current:
            log_info(f"ℹ️ [DATAPI:remove_kw] «{kw}» نبود — رد شد")
            return False

        current.pop(kw)
        ok = self._write_custom_kw(current)
        if ok:
            log_success(
                f"🗑️ [DATAPI:remove_kw] «{kw}» حذف شد | remaining={
                    len(current)}")
        return ok

    def custom_kw_count(self) -> int:
        """تعداد کلیدواژه‌های سفارشی."""
        return len(self.custom_keywords_with_tier())

    # ═══════════════════════════════════════════
    # گروه‌های هدف
    # ═══════════════════════════════════════════
    def target_groups(self) -> list[str]:
        """خواندن خام فایل (شامل prefix پلتفرم مثل `eitaa:name`)."""
        try:
            if os.path.exists(TARGET_GROUPS):
                with open(TARGET_GROUPS, encoding="utf-8") as f:
                    return [ln.strip() for ln in f if ln.strip()
                            and not ln.startswith("#")]
        except Exception as e:
            log_warning(f"⚠️ [DATAPI:target_groups] {type(e).__name__}: {e}")
        return []

    def add_target_group(self, name: str) -> bool:
        """🎯 FIX: افزودن گروه هدف — atomic + fsync."""
        if not name or not str(name).strip():
            log_warning("⚠️ [DATAPI:add_target] نام خالی رد شد")
            return False

        name_clean = str(name).strip()
        try:
            existing = self.target_groups()
            if name_clean in existing:
                log_info(
                    f"ℹ️ [DATAPI:add_target] «{name_clean}» از قبل هست — رد شد")
                return False

            # 🎯 FIX: atomic + fsync
            parent = os.path.dirname(TARGET_GROUPS)
            if parent:
                os.makedirs(parent, exist_ok=True)

            tmp = TARGET_GROUPS + ".tmp"

            # خواندن محتوای فعلی
            content = ""
            if os.path.exists(TARGET_GROUPS):
                with open(TARGET_GROUPS, encoding="utf-8") as f:
                    content = f.read()

            # اطمینان از newline در انتها
            if content and not content.endswith("\n"):
                content += "\n"
            content += f"{name_clean}\n"

            # نوشتن atomic
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(content)
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except (AttributeError, OSError) as e:
                    log_warning(
                        f"⚠️ [core.manual.data_api] {
                            type(e).__name__}: {e}")
            os.replace(tmp, TARGET_GROUPS)

            # fsync دایرکتوری
            if parent:
                try:
                    dir_fd = os.open(parent, os.O_DIRECTORY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except (AttributeError, OSError) as e:
                    log_warning(
                        f"⚠️ [core.manual.data_api] {
                            type(e).__name__}: {e}")

            log_success(
                f"✅ [DATAPI:add_target] «{name_clean}» "
                f"اضافه شد | total={len(existing) + 1}"
            )
            return True
        except Exception as e:
            log_error(f"❌ [DATAPI:add_target] {type(e).__name__}: {e}")
            # cleanup tmp
            try:
                tmp = TARGET_GROUPS + ".tmp"
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception as e:
                log_warning(
                    f"⚠️ [core.manual.data_api] {
                        type(e).__name__}: {e}")
            return False

    # ═══════════════════════════════════════════
    # لاگ‌ها
    # ═══════════════════════════════════════════
    @staticmethod
    def _tail_lines(path: str, max_bytes: int | None = None) -> list[str]:
        """آخرین خطوط فایل (seek از انتها)."""
        if max_bytes is None:
            max_bytes = _LOG_TAIL_BYTES
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                if size > max_bytes:
                    f.seek(-max_bytes, os.SEEK_END)
                    f.readline()  # خط ناقص اول
                data = f.read()
            return data.decode("utf-8", errors="ignore").splitlines()
        except FileNotFoundError:
            return []
        except Exception as e:
            log_warning(
                f"⚠️ [DATAPI:_tail_lines] {
                    type(e).__name__}: {e} | path={path}")
            return []

    def recent_logs(self, n: int = 15, level: str | None = None) -> list[dict]:
        """آخرین N خط JSON از app.jsonl."""
        # 🎯 P0: از data.paths
        path = str(LOG_FILE)
        if not os.path.exists(path):
            return []

        out: list[dict] = []
        for ln in reversed(self._tail_lines(path)):
            try:
                o = json.loads(ln)
            except Exception:
                # خط نامعتبر — بی‌صدا رد (خطوط ناقص در tail)
                continue
            if level and o.get("level") != level:
                continue
            out.append(o)
            if len(out) >= n:
                break
        return out

    def error_count_recent(self, n: int = 100) -> int:
        """تعداد خطاهای ERROR/CRITICAL در N خط اخیر."""
        return sum(1 for entry in self.recent_logs(
            n) if entry.get("level") in ("ERROR", "CRITICAL"))

    # ═══════════════════════════════════════════
    # گزارش‌ها
    # ═══════════════════════════════════════════
    def recent_reports(self, n: int = 10) -> list[dict]:
        """آخرین گزارش‌های تولیدشده."""
        out: list[dict] = []
        try:
            # 🎯 P0: از data.paths
            reports_dir = str(REPORTS_DIR)
            if not os.path.isdir(reports_dir):
                return []
            for name in os.listdir(reports_dir):
                p = os.path.join(reports_dir, name)
                if os.path.isfile(p):
                    try:
                        out.append(
                            {
                                "name": name,
                                "size_kb": (os.path.getsize(p) / 1024),
                                "mtime": os.path.getmtime(p),
                            }
                        )
                    except OSError:
                        # فایل بین listdir و stat حذف شده
                        continue
            out.sort(key=lambda x: -x["mtime"])
        except Exception as e:
            log_warning(f"⚠️ [DATAPI:recent_reports] {type(e).__name__}: {e}")
        return out[:n]

    # ═══ سرنخ به تفکیک ساعت ═══
    def leads_by_hour_today(self) -> list[dict]:
        return self.sql(
            """
            SELECT strftime('%H', timestamp) as hour,
                   COUNT(*) as n
            FROM messages
            WHERE keywords!=''
              AND date(timestamp)=date('now')
            GROUP BY hour ORDER BY hour
            """
        )

    # ═══ سرنخ داغ اخیر ═══
    def recent_hot_leads(self, limit: int = 5) -> list[dict]:
        return self.sql(
            """
            SELECT chat_name, platform, keywords,
                   opportunity_score, text
            FROM messages
            WHERE opportunity_type='داغ'
            ORDER BY timestamp DESC LIMIT ?
            """,
            (int(limit),),
        )

    # ═══ سرنخ یک گروه خاص ═══
    def leads_in_group(self, chat_name: str, limit: int = 20) -> list[dict]:
        return self.sql(
            """
            SELECT keywords, text, opportunity_type, timestamp
            FROM messages
            WHERE keywords!='' AND chat_name LIKE ?
            ORDER BY timestamp DESC LIMIT ?
            """,
            (f"%{chat_name}%", int(limit)),
        )

    # ═══ سرنخ یک کلیدواژه خاص ═══
    def leads_with_keyword(self, kw: str, limit: int = 20) -> list[dict]:
        return self.sql(
            """
            SELECT chat_name, platform, text, timestamp
            FROM messages WHERE keywords LIKE ?
            ORDER BY timestamp DESC LIMIT ?
            """,
            (f"%{kw}%", int(limit)),
        )

    # ═══ مقایسه با دیروز ═══
    def compare_today_yesterday(self) -> dict[str, int]:
        today = self.leads_today()
        yesterday = self.leads_yesterday()
        return {
            "today": today,
            "yesterday": yesterday,
            "diff": today - yesterday,
        }

    # ═══ سرنخ‌های امتیاز بالا ═══
    def high_score_leads(
            self,
            min_score: int = 50,
            limit: int = 10) -> list[dict]:
        return self.sql(
            """
            SELECT chat_name, platform, keywords,
                   opportunity_score, text
            FROM messages
            WHERE keywords!='' AND opportunity_score >= ?
            ORDER BY opportunity_score DESC LIMIT ?
            """,
            (int(min_score), int(limit)),
        )

    # ═══ گروه‌های غیرفعال ═══
    def silent_groups(self, days: int = 7) -> list[dict]:
        return self.sql(
            """
            SELECT chat_name, platform,
                   MAX(timestamp) as last_seen
            FROM messages
            WHERE keywords!=''
            GROUP BY chat_name, platform
            HAVING MAX(timestamp) < datetime('now', ?)
            ORDER BY last_seen DESC
            """,
            (f"-{int(days)} days",),
        )

    # ═══════════════════════════════════════════
    # آمار کلی (snapshot)
    # ═══════════════════════════════════════════
    def system_snapshot(self) -> dict[str, Any]:
        """خلاصهٔ کلی برای system_snapshot rule."""
        t0 = time.monotonic()
        snap = {
            "leads_total": self.leads_total(),
            "leads_today": self.leads_today(),
            "leads_yesterday": self.leads_yesterday(),
            "leads_last_7d": self.leads_last_n_days(7),
            "leads_last_30d": self.leads_last_n_days(30),
            "hot_total": self.hot_total(),
            "hot_today": self.hot_today(),
            "by_platform": self.leads_by_platform(),
            "keyword_count": self.keyword_count(),
            "custom_kw_count": self.custom_kw_count(),
            "target_group_count": len(self.target_groups()),
            "errors_recent": self.error_count_recent(),
        }
        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms > _SLOW_QUERY_MS:
            log_warning(
                f"🐌 [DATAPI:snapshot] کند | "
                f"{elapsed_ms:.0f}ms | "
                f"total={snap['leads_total']} "
                f"today={snap['leads_today']}"
            )
        return snap
