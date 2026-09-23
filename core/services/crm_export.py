# core/services/crm_export.py
"""
خروجی سرنخ برای CRM.

سرنخ‌ها (پیام‌های کلیدواژه‌دار) را به فرمت آمادهٔ واردکردن در CRM می‌دهد:
منبع/کانال/فرد/تاریخ/علاقه‌مندی(کلیدواژه)/متن/لینک/رسانه، مرتب به ترتیب
ثابت پلتفرم. خروجی: CSV (سازگار با Excel) + JSON + XLSX (اگر openpyxl).

حالت‌های خروجی:
  - عادی:              یک فایل با همه سرنخ‌ها (یا فیلتر platform)
  - platform="X":      فقط پلتفرم X
  - split=True:        یک فایل جدا برای هر پلتفرم
  - multi_sheet=True:  یک XLSX با شیت جدا per platform

اجرا:
  python -m core.services.crm_export                    # ۳۰ روز، همه
  python -m core.services.crm_export 7                  # ۷ روز
  python -m core.services.crm_export 0                  # همه
  python -m core.services.crm_export 30 eitaa           # فقط ایتا
  python -m core.services.crm_export 30 --split         # فایل جدا per platform
  python -m core.services.crm_export 30 --multi-sheet   # یک اکسل، شیت‌جدا

نسخهٔ ۵ (این نسخه):
  - 🟠 FIX: _atomic_write_text حالا encoding پارامتری دارد
    (هم CSV با utf-8-sig و هم JSON با utf-8 از آن استفاده می‌کنند).
  - 🟠 FIX: _write_csv حالا از _atomic_write_text_with_encoding
    استفاده می‌کند (DRY violation رفع شد).
  - 🟠 FIX: _export_split حالا total را درست می‌شمارد
    (فقط سرنخ‌های پلتفرم‌های موفق).
  - 🎯 لاگ دقیق‌تر در _write_csv (BOM ذکر می‌شود).
  - 🧹 _LOG_PREVIEW_LEN حذف شد (استفاده نمی‌شد).
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۴.
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

from config import DB_DIR, REPORTS_DIR
from core.kernel.logger import log_error, log_info, log_success, log_warning
from core.services.daily_reporter import (
    PLATFORM_ORDER,
    platform_label,
    platform_rank,
)

DB_PATH = os.path.join(DB_DIR, "messages.db")

# ─── ستون‌های CRM ───
CRM_HEADERS = [
    "منبع (پلتفرم)",
    "کانال/گروه",
    "فرد/فرستنده",
    "تاریخ",
    "سطح فرصت",
    "امتیاز",
    "علاقه‌مندی (کلیدواژه)",
    "متن پیام",
    "لینک",
    "رسانه",
    "شناسه",
]

# ─── ثابت‌ها ───
_SQL_TIMEOUT_S = float(os.getenv("CRM_SQL_TIMEOUT_S", "5"))
_MAX_XLSX_SHEET_NAME = 31
_XLSX_SHEET_ALL = "all"
_TEXT_CELL_MAX = 1000

# کاراکترهای غیرمجاز در نام فایل (defensively)
_BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _safe_str(val: Any) -> str:
    """تبدیل به str امن (None → "")."""
    return "" if val is None else str(val)


def _safe_row_get(row: Any, key: str, default: Any = "") -> Any:
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
    return _BAD_FILENAME_CHARS.sub("_", str(s)).strip()[:64] or "unknown"


def _sqlite_connect(path: str) -> sqlite3.Connection:
    """اتصال SQLite با timeout (جلوگیری از قفل دائمی)."""
    conn = sqlite3.connect(path, timeout=_SQL_TIMEOUT_S)
    try:
        conn.execute(
            "PRAGMA busy_timeout = ?", (int(_SQL_TIMEOUT_S * 1000),)
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
        txt = str(_safe_row_get(r, "text", "") or "")[:200].strip()
        key = (chat, txt)
        if txt and key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# ═══════════════════════════════════════════
# خواندن داده
# ═══════════════════════════════════════════
def _rows(days: int, platform: str | None = None) -> list[Any]:
    """سرنخ‌ها را از جدول messages می‌خواند (فقط کلیدواژه‌دار).

    Args:
        days: بازه (۰ یا منفی = همه).
        platform: اگر داده شد، فقط همان پلتفرم (case-insensitive).

    Returns:
        لیست ردیف‌های sqlite3.Row، مرتب به ترتیب ثابت پلتفرم.
    """
    t0 = time.monotonic()

    # days امن
    try:
        days = int(days)
    except (TypeError, ValueError):
        log_warning(
            f"⚠️ [CRM:rows] days نامعتبر «{days}» — پیش‌فرض 0"
        )
        days = 0
    if days < 0:
        days = 0

    # platform نرمال
    if platform:
        platform = str(platform).lower().strip() or None

    where = "keywords != ''"
    params: list[Any] = []
    if days > 0:
        since = (
            datetime.now() - timedelta(days=days)
        ).strftime("%Y-%m-%d")
        where += " AND date(timestamp) >= ?"
        params.append(since)
    if platform:
        where += " AND platform = ?"
        params.append(platform)

    rows: list[Any] = []
    try:
        conn = _sqlite_connect(DB_PATH)
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                f"""
                SELECT platform, chat_name, chat_link, sender_name, user_id,
                       text, keywords, media_type, timestamp, id,
                       opportunity_score, opportunity_type
                FROM messages
                WHERE {where}
                ORDER BY opportunity_score DESC, timestamp DESC
                """,
                params,
            )
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
        f"📊 [CRM:rows] {after} سرنخ (dedup: {before}→{after}) | "
        f"days={days} platform={platform or 'all'} | {elapsed_ms:.0f}ms"
    )
    return sorted(rows, key=lambda r: platform_rank(r["platform"]))


def _record(r) -> dict[str, Any]:
    """تبدیل ردیف دیتابیس به دیکشنری CRM — مقاوم به None/KeyError."""
    platform = _safe_row_get(r, "platform", None)
    platform_str = str(platform).lower() if platform else ""

    media = _safe_row_get(r, "media_type", "text")
    score = _safe_row_get(r, "opportunity_score", 0)
    try:
        score_int = int(score or 0)
    except (TypeError, ValueError):
        score_int = 0

    return {
        "source": (
            platform_label(platform_str)
            if platform_str
            else "📍 نامشخص"
        ),
        "platform": platform_str,
        "channel": _safe_str(_safe_row_get(r, "chat_name", "")),
        "contact": (
            _safe_str(_safe_row_get(r, "sender_name", ""))
            or _safe_str(_safe_row_get(r, "user_id", ""))
        ),
        "date": _safe_str(_safe_row_get(r, "timestamp", "")),
        "level": _safe_str(_safe_row_get(r, "opportunity_type", "")),
        "score": score_int,
        "keywords": _safe_str(_safe_row_get(r, "keywords", "")),
        "text": _safe_str(_safe_row_get(r, "text", "")).strip(),
        "link": _safe_str(_safe_row_get(r, "chat_link", "")),
        "media": (
            "" if (media == "text" or not media) else str(media)
        ),
        "id": _safe_str(_safe_row_get(r, "id", "")),
    }


# ═══════════════════════════════════════════
# نوشتن فایل‌ها (atomic)
# ═══════════════════════════════════════════
def _row_cells(rec: dict[str, Any]) -> list[Any]:
    """سلول‌های یک ردیف CRM (متن بریده‌شده)."""
    return [
        rec["source"],
        rec["channel"],
        rec["contact"],
        rec["date"],
        rec["level"],
        rec["score"],
        rec["keywords"],
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
    """
    🟠 FIX: نوشتن atomic با encoding پارامتری.

    Args:
        path: مسیر نهایی.
        write_fn: callback که فایل handle باز را می‌گیرد.
        encoding: انکودینگ فایل (پیش‌فرض utf-8).
        newline: حالت newline (None یا "" برای CSV).

    Raises:
        Exception: در صورت شکست در نوشتن (فایل موقت پاک می‌شود).
    """
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # باز کردن فایل با kwarg جدید مطابق نیاز
        if newline is not None:
            f = open(tmp, "w", newline=newline, encoding=encoding)
        else:
            f = open(tmp, "w", encoding=encoding)
        try:
            write_fn(f)
        finally:
            f.close()
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise


def _write_csv(path: str, records: list[dict[str, Any]]) -> bool:
    """
    نوشتن CSV با utf-8-sig (سازگار با Excel فارسی) — atomic.

    🟠 FIX: حالا از _atomic_write_text_with_encoding استفاده می‌کند.
    """
    t0 = time.monotonic()
    try:
        def _writer(f):
            w = csv.writer(f)
            w.writerow(CRM_HEADERS)
            for rec in records:
                w.writerow(_row_cells(rec))

        # utf-8-sig: با BOM برای Excel فارسی
        _atomic_write_text_with_encoding(
            path, _writer, encoding="utf-8-sig", newline=""
        )

        size = os.path.getsize(path)
        elapsed_ms = (time.monotonic() - t0) * 1000
        log_info(
            f"📄 [CRM:csv] {os.path.basename(path)} | "
            f"{len(records)} ردیف | {size/1024:.1f}KB | "
            f"encoding=utf-8-sig (BOM) | "
            f"{elapsed_ms:.0f}ms"
        )
        return True
    except Exception as e:
        log_error(
            f"❌ [CRM:csv] {type(e).__name__}: {e} | path={path}"
        )
        return False


def _write_json(path: str, records: list[dict[str, Any]]) -> bool:
    """نوشتن JSON — atomic (utf-8 بدون BOM)."""
    t0 = time.monotonic()
    try:
        def _writer(f):
            json.dump(records, f, ensure_ascii=False, indent=2)

        _atomic_write_text_with_encoding(path, _writer, encoding="utf-8")
        size = os.path.getsize(path)
        elapsed_ms = (time.monotonic() - t0) * 1000
        log_info(
            f"📄 [CRM:json] {os.path.basename(path)} | "
            f"{len(records)} رکورد | {size/1024:.1f}KB | "
            f"encoding=utf-8 | "
            f"{elapsed_ms:.0f}ms"
        )
        return True
    except Exception as e:
        log_error(
            f"❌ [CRM:json] {type(e).__name__}: {e} | path={path}"
        )
        return False


# ═══════════════════════════════════════════
# Excel (اختیاری، با openpyxl)
# ═══════════════════════════════════════════
def _style_sheet(ws) -> None:
    """استایل sheet — امن روی sheet خالی."""
    try:
        from openpyxl.styles import Alignment, Font, PatternFill

        ws.sheet_view.rightToLeft = True
        head_fill = PatternFill("solid", fgColor="27AE60")
        try:
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = head_fill
                c.alignment = Alignment(horizontal="center")
        except Exception:
            pass
        ws.freeze_panes = "A2"
        widths = [12, 22, 16, 20, 10, 8, 24, 60, 30, 10, 18]
        for i, wd in enumerate(widths, 1):
            try:
                col_letter = ws.cell(row=1, column=i).column_letter
                ws.column_dimensions[col_letter].width = wd
            except Exception:
                continue
    except ImportError:
        pass


def _export_xlsx_single(
    path: str, records: list[dict[str, Any]]
) -> str | None:
    """یک XLSX تک‌شیئی. Returns path یا None."""
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
            f"📊 [CRM:xlsx:single] {os.path.basename(path)} | "
            f"{len(records)} ردیف | {size/1024:.1f}KB | "
            f"{elapsed_ms:.0f}ms"
        )
        return path
    except Exception as e:
        log_error(
            f"❌ [CRM:xlsx:single] {type(e).__name__}: {e} | "
            f"path={path}"
        )
        return None


def _safe_sheet_name(name: str, existing: set) -> str:
    """نام sheet امن و یکتا (≤ 31 char، بدون تکرار)."""
    base = _sanitize_filename(name)[:_MAX_XLSX_SHEET_NAME]
    if not base:
        base = "sheet"
    candidate = base
    i = 2
    while candidate.lower() in existing:
        suffix = f"_{i}"
        candidate = base[: _MAX_XLSX_SHEET_NAME - len(suffix)] + suffix
        i += 1
        if i > 100:
            break
    return candidate


def _export_xlsx_multi_sheet(
    path: str, by_platform: dict[str, list[dict[str, Any]]]
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

        # شیت «همه»
        ws_all = wb.active
        ws_all.title = _XLSX_SHEET_ALL
        ws_all.append(CRM_HEADERS)
        total_records = 0
        for p in PLATFORM_ORDER:
            for rec in by_platform.get(p, []):
                ws_all.append(_row_cells(rec))
                total_records += 1
        # platformهای ناشناخته
        for p, recs in by_platform.items():
            if p in PLATFORM_ORDER:
                continue
            for rec in recs:
                ws_all.append(_row_cells(rec))
                total_records += 1
        _style_sheet(ws_all)

        # شیت‌های per-platform
        existing_names: set = {_XLSX_SHEET_ALL.lower()}
        sheets_created = 1

        all_platforms = list(PLATFORM_ORDER) + sorted(
            p for p in by_platform.keys() if p not in PLATFORM_ORDER
        )
        for p in all_platforms:
            records = by_platform.get(p, [])
            if not records:
                continue
            sheet_name = _safe_sheet_name(p, existing_names)
            existing_names.add(sheet_name.lower())
            ws = wb.create_sheet(title=sheet_name)
            ws.append(CRM_HEADERS)
            for rec in records:
                ws.append(_row_cells(rec))
            _style_sheet(ws)
            sheets_created += 1

        wb.save(path)
        size = os.path.getsize(path)
        elapsed_ms = (time.monotonic() - t0) * 1000
        log_info(
            f"📊 [CRM:xlsx:multi] {os.path.basename(path)} | "
            f"sheets={sheets_created} records={total_records} | "
            f"{size/1024:.1f}KB | {elapsed_ms:.0f}ms"
        )
        return path
    except Exception as e:
        log_error(
            f"❌ [CRM:xlsx:multi] {type(e).__name__}: {e} | "
            f"path={path}"
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
    """خروجی CRM.

    Args:
        days: بازه (۰ = همه). منفی → ۰.
        platform: فیلتر پلتفرم (None = همه).
        split: True → یک فایل جدا per platform.
        multi_sheet: True → یک XLSX با شیت جدا per platform.
                     (فقط وقتی platform=None و split=False).

    Returns:
        dict | None:
          - حالت عادی: {"csv", "json", "xlsx", "count", "platform", "mode"}
          - حالت split: {"count", "by_platform", "mode", "failed_platforms"}
          - None اگر دیتابیس نبود / خطا / سرنخی نبود.
    """
    t_all = time.monotonic()

    # نرمال‌سازی platform
    if platform:
        platform = str(platform).lower().strip() or None

    mode = (
        "split" if split
        else ("multi_sheet" if multi_sheet and not platform else "single")
    )
    log_info(
        f"▶️ [CRM:export] شروع | mode={mode} | days={days} | "
        f"platform={platform or 'all'}"
    )

    if not os.path.exists(DB_PATH):
        log_error(f"❌ [CRM:export] DB پیدا نشد | {DB_PATH}")
        return None

    try:
        os.makedirs(REPORTS_DIR, exist_ok=True)
    except Exception as e:
        log_error(
            f"❌ [CRM:export] mkdir | {type(e).__name__}: {e}"
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
            f"⚠️ [CRM:export] سرنخی پیدا نشد | days={days} | "
            f"platform={platform or 'all'}"
        )
        return None

    suffix = f"_{_sanitize_filename(platform)}" if platform else ""
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

    # XLSX
    if multi_sheet and not platform:
        by_platform = _group_by_platform(records)
        xlsx_out = _export_xlsx_multi_sheet(xlsx_path, by_platform)
    else:
        xlsx_out = _export_xlsx_single(xlsx_path, records)
    if xlsx_out is None:
        xlsx_path = None

    elapsed_ms = (time.monotonic() - t_all) * 1000
    label = platform or "همه پلتفرم‌ها"
    log_success(
        f"✅ [CRM:export] {len(records)} سرنخ | {label} | "
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
            "multi_sheet" if (multi_sheet and not platform) else "single"
        ),
    }


# ═══════════════════════════════════════════
# _export_split
# ═══════════════════════════════════════════
def _export_split(days: int, stamp: str) -> dict[str, Any]:
    """برای هر پلتفرم، یک فایل جدا CSV/JSON/XLSX می‌سازد."""
    t0 = time.monotonic()
    all_rows = _rows(days, platform=None)
    if not all_rows:
        log_warning("⚠️ [CRM:split] سرنخی برای خروجی پیدا نشد.")
        return {
            "count": 0,
            "by_platform": {},
            "mode": "split",
            "failed_platforms": [],
        }

    # گروه‌بندی یک‌بار
    grouped: dict[str, list[Any]] = {}
    for r in all_rows:
        p = (_safe_row_get(r, "platform", "") or "").lower()
        grouped.setdefault(p, []).append(r)

    # ترتیب ثابت + پلتفرم‌های ناشناخته در انتها
    all_platforms = list(PLATFORM_ORDER) + sorted(
        p for p in grouped.keys() if p not in PLATFORM_ORDER
    )

    by_platform: dict[str, dict[str, Any]] = {}
    total_success = 0  # 🟠 FIX: فقط سرنخ‌های پلتفرم‌های موفق
    total_rows = 0     # برای لاگ
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
            # 🟠 FIX: فقط سرنخ‌های پلتفرم موفق را می‌شماریم
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
        f"✅ [CRM:split] {total_success}/{total_rows} سرنخ در "
        f"{len(by_platform)} پلتفرم | "
        f"failed={len(failed_platforms)} | {elapsed_ms:.0f}ms"
    )
    for p, info in by_platform.items():
        log_info(f"   • {platform_label(p)}: {info['count']} سرنخ")
    if failed_platforms:
        log_warning(
            f"⚠️ [CRM:split] پلتفرم‌های ناموفق: {failed_platforms}"
        )

    return {
        "count": total_success,
        "by_platform": by_platform,
        "mode": "split",
        "failed_platforms": failed_platforms,
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
        help="فیلتر پلتفرم (eitaa/bale/...). پیش‌فرض: همه.",
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
        f"🏁 [CRM:cli] کامل | {result.get('count', 0)} سرنخ | "
        f"mode={result.get('mode', 'single')}"
    )
    # اگه split بود، failedها را هم لاگ کن
    failed = result.get("failed_platforms") or []
    if failed:
        log_warning(f"⚠️ [CRM:cli] failed: {failed}")
        return 2
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
