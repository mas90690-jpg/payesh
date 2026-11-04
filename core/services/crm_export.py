"""خروجی سرنخ برای CRM.

سرنخ‌ها (پیام‌های کلیدواژه‌دار) را به فرمت آمادهٔ واردکردن در CRM
می‌دهد: منبع/کانال/فرد/تاریخ/علاقه‌مندی(کلیدواژه)/متن/لینک/رسانه،
مرتب به ترتیب ثابت پلتفرم. خروجی: CSV (سازگار با Excel) + JSON +
XLSX (اگر openpyxl).

حالت‌های خروجی:
  - عادی:              یک فایل با همه سرنخ‌ها (یا فیلتر platform)
  - platform="X":      فقط پلتفرم X
  - split=True:        یک فایل جدا برای هر پلتفرم
  - multi_sheet=True:  یک XLSX با شیت جدا per platform

نسخهٔ ۹ (این نسخه):
  - 🎯 `_HEADERS_LOADED_FROM_FILE` — فلگ جدید برای تشخیص منبع دقیق.
  - 🎯 `_load_and_track()` — لود + ثبت منبع (file/fallback).
  - 🎯 `get_stats()` حالا `headers_source` را **دقیق** نشان می‌دهد.
  - 🎯 `reload_crm_headers()` از تابع جدید استفاده می‌کند.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۸ (هدرها از data).

نسخهٔ ۸ (قبلی):
  - 🎯 هدرهای CRM حالا از `data/configs/crm_headers.json` می‌آیند.
  - 🎯 `_load_crm_headers_from_data()` — تابع جدید با fallback امن.
  - 🎯 `reload_crm_headers()` — برای hot-reload.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۷ (SEC FIX SQL).
"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import IO, Any

from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
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
# مسیرها (از data.paths — منبع یکتا)
# ═══════════════════════════════════════════
try:
    from data import paths as _data_paths
    DB_PATH: str = str(_data_paths.DB_FILE)
    REPORTS_DIR: str = str(_data_paths.REPORTS_DIR)
except Exception:
    from config import DB_DIR, REPORTS_DIR as _CFG_REPORTS
    DB_PATH = os.path.join(str(DB_DIR), "messages.db")
    REPORTS_DIR = str(_CFG_REPORTS)


# ─── import از daily_reporter (با fallback) ───
try:
    from core.services.daily_reporter import (
        PLATFORM_ORDER,
        platform_label,
        platform_rank,
    )
except Exception:
    # ─── fallback ───
    try:
        from config import PLATFORMS_ORDER as _PO
        PLATFORM_ORDER = list(_PO)
    except Exception:
        PLATFORM_ORDER = [
            "eitaa", "bale", "rubika", "telegram",
            "whatsapp", "web", "linkedin", "instagram",
        ]

    def platform_label(p: str) -> str:
        return f"📍 {p or 'نامشخص'}"

    def platform_rank(p: str) -> int:
        p_low = (p or "").lower()
        return (
            PLATFORM_ORDER.index(p_low)
            if p_low in PLATFORM_ORDER
            else len(PLATFORM_ORDER) + 1
        )


# ═══════════════════════════════════════════
# 🆕 هدرهای CRM (از data/configs/crm_headers.json)
# ═══════════════════════════════════════════

# ─── Fallback: اگه فایل نبود یا خراب بود ───
_FALLBACK_CRM_HEADERS: list[str] = [
    "منبع (پلتفرم)",
    "کانال/گروه",
    "فرد/فرستنده",
    "تاریخ",
    "سطح فرصت",
    "امتیاز",
    "علاقه‌مندی (کلیدواژه)",
    "برند",
    "محصولات",
    "متن پیام",
    "لینک",
    "رسانه",
    "شناسه",
]

# تعداد مورد انتظار (چون _row_cells دقیقاً ۱۳ سلول می‌دهد)
_EXPECTED_HEADERS_COUNT = 13

# 🎯 فلگ: آیا از فایل خونده شده یا fallback؟
_HEADERS_LOADED_FROM_FILE: bool = False


def _validate_headers(headers: Any) -> list[str] | None:
    """🎯 اعتبارسنجی لیست هدرها.

    Returns:
        لیست clean یا None اگه نامعتبر بود.
    """
    if not isinstance(headers, list) or not headers:
        return None

    if len(headers) != _EXPECTED_HEADERS_COUNT:
        log_warning(
            f"⚠️ [CRM:headers] تعداد ستون‌ها "
            f"{len(headers)} ≠ {_EXPECTED_HEADERS_COUNT}"
        )
        return None

    clean: list[str] = []
    for h in headers:
        if not isinstance(h, str) or not h.strip():
            log_warning(
                f"⚠️ [CRM:headers] ستون نامعتبر «{h!r}»"
            )
            return None
        clean.append(h.strip())

    return clean


def _load_crm_headers_from_data() -> list[str]:
    """🎯 خواندن هدرهای CRM از data — با fallback امن.

    Returns:
        لیست هدرها. اگه فایل نبود یا نامعتبر بود → fallback.
    """
    try:
        from data.loader import load_json
        data = load_json("crm_headers", default={})

        if not isinstance(data, dict):
            return list(_FALLBACK_CRM_HEADERS)

        headers = data.get("headers")
        clean = _validate_headers(headers)

        if clean is None:
            log_warning(
                f"⚠️ [CRM:headers] فایل نامعتبر — "
                f"fallback ({_EXPECTED_HEADERS_COUNT} ستون)"
            )
            return list(_FALLBACK_CRM_HEADERS)

        log_info(
            f"✅ [CRM:headers] {len(clean)} ستون از "
            f"crm_headers.json بارگذاری شد"
        )
        return clean

    except Exception as e:
        log_warning(
            f"⚠️ [CRM:headers] بارگذاری ناموفق | "
            f"{type(e).__name__}: {e} — fallback"
        )
        return list(_FALLBACK_CRM_HEADERS)


def _load_and_track() -> list[str]:
    """🎯 لود + ثبت منبع (file یا fallback).

    Returns:
        لیست هدرها.
    """
    global _HEADERS_LOADED_FROM_FILE

    try:
        from data.loader import load_json
        data = load_json("crm_headers", default={})

        if isinstance(data, dict):
            clean = _validate_headers(data.get("headers"))
            if clean is not None:
                _HEADERS_LOADED_FROM_FILE = True
                log_info(
                    f"✅ [CRM:headers] {len(clean)} ستون از "
                    f"crm_headers.json بارگذاری شد (source=file)"
                )
                return clean
    except Exception as e:
        log_warning(
            f"⚠️ [CRM:headers] لود | "
            f"{type(e).__name__}: {e}"
        )

    # ─── fallback ───
    _HEADERS_LOADED_FROM_FILE = False
    log_warning(
        f"⚠️ [CRM:headers] fallback "
        f"({len(_FALLBACK_CRM_HEADERS)} ستون)"
    )
    return list(_FALLBACK_CRM_HEADERS)


# ─── لود اولیه ───
CRM_HEADERS: list[str] = _load_and_track()


def reload_crm_headers() -> list[str]:
    """🎯 hot-reload — برای تست/دستی.

    Returns:
        لیست جدید هدرها.
    """
    global CRM_HEADERS
    try:
        from data.loader import clear_cache
        clear_cache()
    except Exception:
        pass
    CRM_HEADERS = _load_and_track()
    return CRM_HEADERS


def get_headers_source() -> str:
    """🎯 منبع هدرها: file یا fallback."""
    return "file" if _HEADERS_LOADED_FROM_FILE else "fallback"


# ═══════════════════════════════════════════
# ثابت‌ها (env-based)
# ═══════════════════════════════════════════
_SQL_TIMEOUT_S = _env_float(
    "CRM_SQL_TIMEOUT_S", 5.0,
    min_val=0.5, max_val=300.0,
)
_MAX_XLSX_SHEET_NAME = 31
_XLSX_SHEET_ALL = "all"
_TEXT_CELL_MAX = _env_int(
    "CRM_TEXT_CELL_MAX", 1000,
    min_val=100, max_val=100_000,
)
_MAX_XLSX_RECORDS = _env_int(
    "CRM_MAX_XLSX_RECORDS", 100_000,
    min_val=100, max_val=1_000_000,
)

# کاراکترهای غیرمجاز در نام فایل
_BAD_FILENAME_CHARS = re.compile(
    r'[<>:"/\\|?*\x00-\x1f]'
)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _safe_str(val: Any) -> str:
    """تبدیل به str امن (None → "")."""
    return "" if val is None else str(val)


def _safe_row_get(
    row: Any, key: str, default: Any = ""
) -> Any:
    """دسترسی امن به ستون sqlite3.Row / dict."""
    try:
        v = row[key]
        return v if v is not None else default
    except (KeyError, IndexError, TypeError):
        return default


def _sanitize_filename(s: str) -> str:
    """پاک‌سازی نام فایل از کاراکترهای غیرمجاز."""
    if not s:
        return "unknown"
    cleaned = _BAD_FILENAME_CHARS.sub("_", str(s)).strip()
    return cleaned[:64] or "unknown"


def _sqlite_connect(path: str) -> sqlite3.Connection:
    """اتصال SQLite با timeout."""
    conn = sqlite3.connect(path, timeout=_SQL_TIMEOUT_S)
    try:
        conn.execute(
            "PRAGMA busy_timeout = ?",
            (int(_SQL_TIMEOUT_S * 1000),),
        )
    except Exception:
        pass
    return conn


def _dedup_rows(rows: list[Any]) -> list[Any]:
    """حذف سرنخ‌های تکراری (همان گروه + متن یکسان)."""
    seen = set()
    out: list[Any] = []
    for r in rows:
        chat = _safe_row_get(r, "chat_name", "") or ""
        txt = str(
            _safe_row_get(r, "text", "") or ""
        )[:200].strip()
        key = (chat, txt)
        if txt and key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out

# ═══════════════════════════════════════════
# خواندن داده
# ═══════════════════════════════════════════


def _rows(
    days: int, platform: str | None = None
) -> list[Any]:
    """سرنخ‌ها را از جدول messages می‌خواند.

    🔒 SEC FIX: `where` فقط از placeholder های ثابت + `?`
    ساخته می‌شود. مقادیر از طریق params به SQLite می‌روند.
    """
    t0 = time.monotonic()

    # ─── days امن ───
    try:
        days = int(days)
    except (TypeError, ValueError):
        log_warning(
            f"⚠️ [CRM:rows] days نامعتبر «{days}» — "
            f"پیش‌فرض 0"
        )
        days = 0
    if days < 0:
        days = 0

    # ─── platform نرمال ───
    if platform:
        platform = str(platform).lower().strip() or None

    # 🔒 SEC FIX: ساخت where از رشته‌های ثابت + `?`
    where_parts: list[str] = ["keywords != ''"]
    params: list[Any] = []

    if days > 0:
        since = (
            datetime.now() - timedelta(days=days)
        ).strftime("%Y-%m-%d")
        where_parts.append("date(timestamp) >= ?")
        params.append(since)
    if platform:
        where_parts.append("platform = ?")
        params.append(platform)

    where_sql = " AND ".join(where_parts)

    # 🔒 SEC FIX: concatenation با + — نه f-string.
    query = (
        "SELECT platform, chat_name, chat_link, "
        "sender_name, user_id, text, keywords, "
        "media_type, timestamp, id, "
        "opportunity_score, opportunity_type, "
        "brands, matched_products "
        "FROM messages "
        "WHERE " + where_sql + " "
        "ORDER BY opportunity_score DESC, "
        "timestamp DESC"
    )

    rows: list[Any] = []
    try:
        conn = _sqlite_connect(DB_PATH)
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(query, params)
            rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as e:
        log_error(
            f"❌ [CRM:rows] query | {type(e).__name__}: {e} | "
            f"days={days} platform={platform or 'all'}"
        )
        return []

    before = len(rows)
    rows = _dedup_rows(rows)
    after = len(rows)
    elapsed_ms = (time.monotonic() - t0) * 1000

    log_info(
        f"📊 [CRM:rows] {after} سرنخ "
        f"(dedup: {before}→{after}) | "
        f"days={days} platform={platform or 'all'} | "
        f"{elapsed_ms:.0f}ms"
    )
    return sorted(
        rows, key=lambda r: platform_rank(r["platform"])
    )


def _record(r: Any) -> dict[str, Any]:
    """تبدیل ردیف دیتابیس به دیکشنری CRM."""
    platform = _safe_row_get(r, "platform", None)
    platform_str = (
        str(platform).lower() if platform else ""
    )

    media = _safe_row_get(r, "media_type", "text")
    score = _safe_row_get(r, "opportunity_score", 0)
    try:
        score_int = int(score or 0)
    except (TypeError, ValueError):
        score_int = 0

    return {
        "source": (
            platform_label(platform_str)
            if platform_str else "📍 نامشخص"
        ),
        "platform": platform_str,
        "channel": _safe_str(
            _safe_row_get(r, "chat_name", "")
        ),
        "contact": (
            _safe_str(_safe_row_get(r, "sender_name", ""))
            or _safe_str(_safe_row_get(r, "user_id", ""))
        ),
        "date": _safe_str(
            _safe_row_get(r, "timestamp", "")
        ),
        "level": _safe_str(
            _safe_row_get(r, "opportunity_type", "")
        ),
        "score": score_int,
        "keywords": _safe_str(
            _safe_row_get(r, "keywords", "")
        ),
        "brands": _safe_str(
            _safe_row_get(r, "brands", "")
        ),
        "matched_products": _safe_str(
            _safe_row_get(r, "matched_products", "")
        ),
        "text": _safe_str(
            _safe_row_get(r, "text", "")
        ).strip(),
        "link": _safe_str(
            _safe_row_get(r, "chat_link", "")
        ),
        "media": (
            "" if (media == "text" or not media)
            else str(media)
        ),
        "id": _safe_str(_safe_row_get(r, "id", "")),
    }


def _compute_platforms_in_order(
    by_platform: dict[str, Any],
) -> list[str]:
    """🎯 لیست پلتفرم‌ها به ترتیب ثابت + ناشناخته‌ها در انتها."""
    known = list(PLATFORM_ORDER)
    unknown = sorted(
        p for p in by_platform.keys()
        if p not in PLATFORM_ORDER
    )
    return known + unknown


# ═══════════════════════════════════════════
# نوشتن فایل‌ها (atomic)
# ═══════════════════════════════════════════
def _row_cells(rec: dict[str, Any]) -> list[Any]:
    """سلول‌های یک ردیف CRM."""
    return [
        rec["source"],
        rec["channel"],
        rec["contact"],
        rec["date"],
        rec["level"],
        rec["score"],
        rec["keywords"],
        rec.get("brands", ""),
        rec.get("matched_products", ""),
        (rec["text"] or "")[:_TEXT_CELL_MAX],
        rec["link"],
        rec["media"],
        rec["id"],
    ]


def _atomic_write_text_with_encoding(
    path: str,
    write_fn: Callable[[IO], None],
    encoding: str = "utf-8",
    newline: str | None = None,
) -> None:
    """🎯 FIX: نوشتن atomic با encoding + fsync."""
    tmp = path + ".tmp"

    try:
        parent = os.path.dirname(path)
        if parent:
            try:
                os.makedirs(parent, exist_ok=True)
            except Exception:
                pass

        # ─── باز کردن فایل ───
        if newline is not None:
            f = open(
                tmp, "w", newline=newline, encoding=encoding
            )
        else:
            f = open(tmp, "w", encoding=encoding)

        try:
            write_fn(f)
            # 🎯 FIX: fsync
            try:
                f.flush()
                os.fsync(f.fileno())
            except (AttributeError, OSError):
                pass
        finally:
            f.close()

        os.replace(tmp, path)

        # ─── dir fsync (POSIX) ───
        if parent:
            try:
                dir_fd = os.open(parent, os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except (AttributeError, OSError):
                pass
    except Exception:
        # ─── cleanup tmp ───
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise


def _write_csv(
    path: str, records: list[dict[str, Any]]
) -> bool:
    """نوشتن CSV با utf-8-sig (BOM) — atomic."""
    t0 = time.monotonic()
    try:
        def _writer(f):
            w = csv.writer(f)
            w.writerow(CRM_HEADERS)
            for rec in records:
                w.writerow(_row_cells(rec))

        _atomic_write_text_with_encoding(
            path, _writer,
            encoding="utf-8-sig", newline="",
        )

        size = os.path.getsize(path)
        elapsed_ms = (time.monotonic() - t0) * 1000
        log_info(
            f"📄 [CRM:csv] {os.path.basename(path)} | "
            f"{len(records)} ردیف | {size / 1024:.1f}KB | "
            f"encoding=utf-8-sig (BOM) | "
            f"{elapsed_ms:.0f}ms"
        )
        return True
    except Exception as e:
        log_error(
            f"❌ [CRM:csv] {type(e).__name__}: {e} | "
            f"path={path}"
        )
        return False


def _write_json(
    path: str, records: list[dict[str, Any]]
) -> bool:
    """نوشتن JSON — atomic (utf-8)."""
    t0 = time.monotonic()
    try:
        def _writer(f):
            json.dump(
                records, f,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

        _atomic_write_text_with_encoding(
            path, _writer, encoding="utf-8"
        )
        size = os.path.getsize(path)
        elapsed_ms = (time.monotonic() - t0) * 1000
        log_info(
            f"📄 [CRM:json] {os.path.basename(path)} | "
            f"{len(records)} رکورد | {size / 1024:.1f}KB | "
            f"{elapsed_ms:.0f}ms"
        )
        return True
    except Exception as e:
        log_error(
            f"❌ [CRM:json] {type(e).__name__}: {e} | "
            f"path={path}"
        )
        return False


# ═══════════════════════════════════════════
# Excel (اختیاری)
# ═══════════════════════════════════════════
def _style_sheet(ws) -> None:
    """استایل sheet — امن روی sheet خالی."""
    try:
        from openpyxl.styles import (
            Alignment, Font, PatternFill,
        )

        ws.sheet_view.rightToLeft = True
        head_fill = PatternFill(
            "solid", fgColor="27AE60"
        )
        try:
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = head_fill
                c.alignment = Alignment(
                    horizontal="center"
                )
        except Exception:
            pass
        ws.freeze_panes = "A2"
        widths = [
            12, 22, 16, 20, 10, 8, 24,
            20, 30, 60, 30, 10, 18,
        ]
        for i, wd in enumerate(widths, 1):
            try:
                col_letter = ws.cell(
                    row=1, column=i
                ).column_letter
                ws.column_dimensions[
                    col_letter
                ].width = wd
            except Exception:
                continue
    except ImportError:
        pass


def _export_xlsx_single(
    path: str, records: list[dict[str, Any]]
) -> str | None:
    """یک XLSX تک‌شیئی."""
    try:
        from openpyxl import Workbook
    except ImportError as e:
        log_warning(
            f"⚠️ [CRM:xlsx] openpyxl نصب نیست | {e} — "
            f"xlsx تولید نمی‌شود"
        )
        return None

    if len(records) > _MAX_XLSX_RECORDS:
        log_warning(
            f"⚠️ [CRM:xlsx] records={len(records)} > "
            f"max={_MAX_XLSX_RECORDS} — truncated"
        )
        records = records[:_MAX_XLSX_RECORDS]

    t0 = time.monotonic()
    try:
        wb = Workbook()
        ws = wb.active
        ws.title = "Leads"
        ws.append(CRM_HEADERS)
        for rec in records:
            ws.append(_row_cells(rec))
        _style_sheet(ws)
        wb.save(path)
        size = os.path.getsize(path)
        elapsed_ms = (time.monotonic() - t0) * 1000
        log_info(
            f"📊 [CRM:xlsx:single] "
            f"{os.path.basename(path)} | "
            f"{len(records)} ردیف | {size / 1024:.1f}KB | "
            f"{elapsed_ms:.0f}ms"
        )
        return path
    except Exception as e:
        log_error(
            f"❌ [CRM:xlsx:single] "
            f"{type(e).__name__}: {e} | path={path}"
        )
        return None


def _safe_sheet_name(name: str, existing: set) -> str:
    """نام sheet امن و یکتا (≤ 31 char)."""
    base = _sanitize_filename(name)[
        :_MAX_XLSX_SHEET_NAME
    ]
    if not base:
        base = "sheet"
    candidate = base
    i = 2
    while candidate.lower() in existing:
        suffix = f"_{i}"
        candidate = (
            base[: _MAX_XLSX_SHEET_NAME - len(suffix)]
            + suffix
        )
        i += 1
        if i > 100:
            break
    return candidate


def _export_xlsx_multi_sheet(
    path: str,
    by_platform: dict[str, list[dict[str, Any]]],
) -> str | None:
    """یک XLSX با شیت per-platform + یک شیت «all»."""
    try:
        from openpyxl import Workbook
    except ImportError as e:
        log_warning(
            f"⚠️ [CRM:xlsx] openpyxl نصب نیست | {e} — "
            f"xlsx تولید نمی‌شود"
        )
        return None

    t0 = time.monotonic()
    try:
        wb = Workbook()

        # ─── شیت «همه» ───
        ws_all = wb.active
        ws_all.title = _XLSX_SHEET_ALL
        ws_all.append(CRM_HEADERS)
        total_records = 0
        all_platforms = _compute_platforms_in_order(
            by_platform
        )

        for p in all_platforms:
            for rec in by_platform.get(p, []):
                if total_records >= _MAX_XLSX_RECORDS:
                    break
                ws_all.append(_row_cells(rec))
                total_records += 1
            if total_records >= _MAX_XLSX_RECORDS:
                break
        _style_sheet(ws_all)

        # ─── شیت‌های per-platform ───
        existing_names: set = {_XLSX_SHEET_ALL.lower()}
        sheets_created = 1

        for p in all_platforms:
            records = by_platform.get(p, [])
            if not records:
                continue
            sheet_name = _safe_sheet_name(
                p, existing_names
            )
            existing_names.add(sheet_name.lower())
            ws = wb.create_sheet(title=sheet_name)
            ws.append(CRM_HEADERS)
            for rec in records[:_MAX_XLSX_RECORDS]:
                ws.append(_row_cells(rec))
            _style_sheet(ws)
            sheets_created += 1

        wb.save(path)
        size = os.path.getsize(path)
        elapsed_ms = (time.monotonic() - t0) * 1000
        log_info(
            f"📊 [CRM:xlsx:multi] "
            f"{os.path.basename(path)} | "
            f"sheets={sheets_created} "
            f"records={total_records} | "
            f"{size / 1024:.1f}KB | {elapsed_ms:.0f}ms"
        )
        return path
    except Exception as e:
        log_error(
            f"❌ [CRM:xlsx:multi] "
            f"{type(e).__name__}: {e} | path={path}"
        )
        return None


# ═══════════════════════════════════════════
# گروه‌بندی
# ═══════════════════════════════════════════
def _group_by_platform(
    records: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    by: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        p = (rec.get("platform") or "").lower()
        by.setdefault(p, []).append(rec)
    return by


# ═══════════════════════════════════════════
# API اصلی
# ═══════════════════════════════════════════
def export_crm(
    days: int = 30,
    platform: str | None = None,
    split: bool = False,
    multi_sheet: bool = False,
) -> dict[str, Any] | None:
    """خروجی CRM."""
    t_all = time.monotonic()

    if platform:
        platform = str(platform).lower().strip() or None

    mode = (
        "split"
        if split
        else (
            "multi_sheet"
            if multi_sheet and not platform
            else "single"
        )
    )
    log_info(
        f"▶️ [CRM:export] شروع | mode={mode} | "
        f"days={days} | platform={platform or 'all'}"
    )

    if not os.path.exists(DB_PATH):
        log_error(f"❌ [CRM:export] DB پیدا نشد | {DB_PATH}")
        return None

    try:
        os.makedirs(REPORTS_DIR, exist_ok=True)
    except Exception as e:
        log_error(
            f"❌ [CRM:export] mkdir | "
            f"{type(e).__name__}: {e}"
        )
        return None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ─── حالت split ───
    if split:
        return _export_split(days, stamp)

    # ─── حالت عادی ───
    rows = _rows(days, platform=platform)
    if not rows:
        log_warning(
            f"⚠️ [CRM:export] سرنخی پیدا نشد | "
            f"days={days} | platform={platform or 'all'}"
        )
        return None

    suffix = (
        f"_{_sanitize_filename(platform)}"
        if platform else ""
    )
    csv_path = os.path.join(
        REPORTS_DIR, f"crm_leads{suffix}_{stamp}.csv"
    )
    json_path = os.path.join(
        REPORTS_DIR, f"crm_leads{suffix}_{stamp}.json"
    )
    xlsx_path = os.path.join(
        REPORTS_DIR, f"crm_leads{suffix}_{stamp}.xlsx"
    )
    records = [_record(r) for r in rows]

    csv_ok = _write_csv(csv_path, records)
    json_ok = _write_json(json_path, records)

    # ─── XLSX ───
    if multi_sheet and not platform:
        by_platform = _group_by_platform(records)
        xlsx_out = _export_xlsx_multi_sheet(
            xlsx_path, by_platform
        )
    else:
        xlsx_out = _export_xlsx_single(
            xlsx_path, records
        )
    if xlsx_out is None:
        xlsx_path = None

    elapsed_ms = (time.monotonic() - t_all) * 1000
    label = platform or "همه پلتفرم‌ها"
    log_success(
        f"✅ [CRM:export] {len(records)} سرنخ | "
        f"{label} | "
        f"csv={'✅' if csv_ok else '❌'} "
        f"json={'✅' if json_ok else '❌'} "
        f"xlsx={'✅' if xlsx_path else '—'} | "
        f"{elapsed_ms:.0f}ms"
    )
    return {
        "csv": csv_path if csv_ok else None,
        "json": json_path if json_ok else None,
        "xlsx": xlsx_path,
        "count": len(records),
        "platform": platform,
        "mode": (
            "multi_sheet"
            if (multi_sheet and not platform)
            else "single"
        ),
    }


# ═══════════════════════════════════════════
# _export_split
# ═══════════════════════════════════════════
def _export_split(
    days: int, stamp: str
) -> dict[str, Any]:
    """برای هر پلتفرم، یک فایل جدا می‌سازد."""
    t0 = time.monotonic()
    all_rows = _rows(days, platform=None)

    if not all_rows:
        log_warning(
            "⚠️ [CRM:split] سرنخی برای خروجی پیدا نشد."
        )
        return {
            "count": 0,
            "by_platform": {},
            "mode": "split",
            "failed_platforms": [],
        }

    # ─── گروه‌بندی ───
    grouped: dict[str, list[Any]] = {}
    for r in all_rows:
        p = (
            _safe_row_get(r, "platform", "") or ""
        ).lower()
        grouped.setdefault(p, []).append(r)

    all_platforms = _compute_platforms_in_order(grouped)

    by_platform: dict[str, dict[str, Any]] = {}
    total_success = 0
    total_rows = 0
    failed_platforms: list[str] = []

    for p in all_platforms:
        rows = grouped.get(p, [])
        if not rows:
            continue

        records = [_record(r) for r in rows]
        p_safe = _sanitize_filename(p) or "unknown"

        csv_path = os.path.join(
            REPORTS_DIR, f"crm_leads_{p_safe}_{stamp}.csv"
        )
        json_path = os.path.join(
            REPORTS_DIR, f"crm_leads_{p_safe}_{stamp}.json"
        )
        xlsx_path = os.path.join(
            REPORTS_DIR, f"crm_leads_{p_safe}_{stamp}.xlsx"
        )

        csv_ok = _write_csv(csv_path, records)
        json_ok = _write_json(json_path, records)
        xlsx_out = _export_xlsx_single(xlsx_path, records)

        if csv_ok or json_ok:
            total_success += len(records)
        else:
            failed_platforms.append(p)
        total_rows += len(records)

        by_platform[p] = {
            "csv": csv_path if csv_ok else None,
            "json": json_path if json_ok else None,
            "xlsx": xlsx_out,
            "count": len(records),
        }

    elapsed_ms = (time.monotonic() - t0) * 1000
    log_success(
        f"✅ [CRM:split] {total_success}/{total_rows} سرنخ "
        f"در {len(by_platform)} پلتفرم | "
        f"failed={len(failed_platforms)} | "
        f"{elapsed_ms:.0f}ms"
    )
    for p, info in by_platform.items():
        log_info(
            f"   • {platform_label(p)}: "
            f"{info['count']} سرنخ"
        )
    if failed_platforms:
        log_warning(
            f"⚠️ [CRM:split] پلتفرم‌های ناموفق: "
            f"{failed_platforms}"
        )

    return {
        "count": total_success,
        "by_platform": by_platform,
        "mode": "split",
        "failed_platforms": failed_platforms,
    }


# ═══════════════════════════════════════════
# debug
# ═══════════════════════════════════════════
def get_stats() -> dict[str, Any]:
    """🎯 خلاصهٔ وضعیت — برای debug."""
    return {
        "db_path": DB_PATH,
        "reports_dir": REPORTS_DIR,
        "sql_timeout_s": _SQL_TIMEOUT_S,
        "text_cell_max": _TEXT_CELL_MAX,
        "max_xlsx_records": _MAX_XLSX_RECORDS,
        "platforms": list(PLATFORM_ORDER),
        "csv_headers_count": len(CRM_HEADERS),
        "headers_source": get_headers_source(),
    }


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════
def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="crm_export",
        description="خروجی CRM از سرنخ‌ها",
    )
    parser.add_argument(
        "days", nargs="?", type=int, default=30,
        help="بازه (روز). ۰ = همه. پیش‌فرض ۳۰.",
    )
    parser.add_argument(
        "platform", nargs="?", default=None,
        help="فیلتر پلتفرم (eitaa/bale/...). "
             "پیش‌فرض: همه.",
    )
    parser.add_argument(
        "--split", "-s", action="store_true",
        help="یک فایل جدا برای هر پلتفرم",
    )
    parser.add_argument(
        "--multi-sheet", "-m", action="store_true",
        help="یک XLSX با شیت جدا per platform",
    )
    args = parser.parse_args()

    try:
        result = export_crm(
            days=args.days,
            platform=(args.platform or "").lower() or None,
            split=args.split,
            multi_sheet=args.multi_sheet,
        )
    except Exception as e:
        log_error(
            f"❌ [CRM:cli] {type(e).__name__}: {e}"
        )
        return 1

    if not result:
        log_warning("⚠️ [CRM:cli] خروجی ساخته نشد.")
        return 1

    log_success(
        f"🏁 [CRM:cli] کامل | "
        f"{result.get('count', 0)} سرنخ | "
        f"mode={result.get('mode', 'single')}"
    )
    failed = result.get("failed_platforms") or []
    if failed:
        log_warning(f"⚠️ [CRM:cli] failed: {failed}")
        return 2
    return 0


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "export_crm",
    "CRM_HEADERS",
    "DB_PATH",
    "REPORTS_DIR",
    "get_stats",
    "reload_crm_headers",
    "get_headers_source",
    "_FALLBACK_CRM_HEADERS",
]


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
