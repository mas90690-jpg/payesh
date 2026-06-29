# core/crm_export.py
"""
خروجیِ سرنخ برای CRM.

سرنخ‌ها (پیام‌های کلیدواژه‌دار) را به فرمتِ آمادهٔ واردکردن در CRM می‌دهد:
ستون‌های استانداردِ CRM (منبع/کانال/فرد/تاریخ/علاقه‌مندی(کلیدواژه)/متن/لینک/رسانه)،
مرتب به ترتیبِ ثابتِ پلتفرم. خروجی هم CSV (سازگار با Excel) و هم JSON.

اجرا:
    python -m core.crm_export            # ۳۰ روز اخیر
    python -m core.crm_export 7          # ۷ روز اخیر
    python -m core.crm_export 0          # همهٔ سرنخ‌ها
"""
import os
import csv
import json
import sqlite3
from datetime import datetime, timedelta

from config import REPORTS_DIR, DB_DIR
from core.daily_reporter import platform_label, platform_rank, dedup_rows

DB_PATH = os.path.join(DB_DIR, "messages.db")

# ستون‌های CRM (به فارسی؛ ترتیب مناسب واردکردن در اکثر CRMها)
CRM_HEADERS = [
    "منبع (پلتفرم)", "کانال/گروه", "فرد/فرستنده", "تاریخ",
    "سطح فرصت", "امتیاز", "علاقه‌مندی (کلیدواژه)", "متن پیام", "لینک", "رسانه", "شناسه",
]


def _rows(days: int):
    """سرنخ‌ها را از جدول messages می‌خواند (فقط کلیدواژه‌دار)، مرتب به‌ترتیبِ پلتفرم
    و درونِ هر پلتفرم داغ‌ترین (امتیاز بالاتر) اول؛ سرنخ‌های تکراری حذف می‌شوند."""
    where = "keywords != ''"
    params = []
    if days and days > 0:
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        where += " AND date(timestamp) >= ?"
        params.append(since)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(f"""
        SELECT platform, chat_name, chat_link, sender_name, user_id,
               text, keywords, media_type, timestamp, id,
               opportunity_score, opportunity_type
        FROM messages
        WHERE {where}
        ORDER BY opportunity_score DESC, timestamp DESC
    """, params)
    rows = dedup_rows(cur.fetchall())
    conn.close()
    # مرتب‌سازی پایدار: ترتیبِ ثابتِ پلتفرم (امتیازِ نزولی درونِ هر پلتفرم حفظ می‌شود)
    return sorted(rows, key=lambda r: platform_rank(r["platform"]))


def _record(r):
    return {
        "source": platform_label(r["platform"]),
        "platform": r["platform"],
        "channel": r["chat_name"] or "",
        "contact": r["sender_name"] or r["user_id"] or "",
        "date": r["timestamp"] or "",
        "level": r["opportunity_type"] or "",
        "score": r["opportunity_score"] or 0,
        "keywords": r["keywords"] or "",
        "text": (r["text"] or "").strip(),
        "link": r["chat_link"] or "",
        "media": "" if (r["media_type"] or "text") == "text" else r["media_type"],
        "id": r["id"],
    }


def export_crm(days: int = 30):
    """خروجیِ CSV و JSONِ سرنخ‌ها برای CRM. مسیر فایل‌ها را برمی‌گرداند."""
    if not os.path.exists(DB_PATH):
        print(f"⚠️ دیتابیس پیدا نشد: {DB_PATH}")
        return None

    rows = _rows(days)
    if not rows:
        print("⚠️ سرنخی برای خروجی پیدا نشد.")
        return None

    os.makedirs(REPORTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(REPORTS_DIR, f"crm_leads_{stamp}.csv")
    json_path = os.path.join(REPORTS_DIR, f"crm_leads_{stamp}.json")
    records = [_record(r) for r in rows]

    def _row_cells(rec):
        return [rec["source"], rec["channel"], rec["contact"], rec["date"],
                rec["level"], rec["score"], rec["keywords"], rec["text"][:1000],
                rec["link"], rec["media"], rec["id"]]

    # CSV (utf-8-sig تا اکسل فارسی را درست نشان دهد)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(CRM_HEADERS)
        for rec in records:
            w.writerow(_row_cells(rec))

    # JSON (برای واردکردن برنامه‌ای در CRM/API)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    # Excel (.xlsx) — اگر openpyxl نصب باشد (هدرِ راست‌به‌چپ و فریزِ ردیفِ اول)
    xlsx_path = _export_xlsx(records, stamp)

    print(f"✅ خروجی CRM آماده شد ({len(records)} سرنخ):")
    print(f"   • CSV : {csv_path}")
    print(f"   • JSON: {json_path}")
    if xlsx_path:
        print(f"   • XLSX: {xlsx_path}")
    return {"csv": csv_path, "json": json_path, "xlsx": xlsx_path, "count": len(records)}


def _export_xlsx(records, stamp):
    """خروجیِ اکسلِ حرفه‌ای (اختیاری؛ نیاز به openpyxl)."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return None
    try:
        wb = Workbook()
        ws = wb.active
        ws.title = "Leads"
        ws.sheet_view.rightToLeft = True
        ws.append(CRM_HEADERS)
        head_fill = PatternFill("solid", fgColor="27AE60")
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = head_fill
            c.alignment = Alignment(horizontal="center")
        for rec in records:
            ws.append([rec["source"], rec["channel"], rec["contact"], str(rec["date"]),
                       rec["level"], rec["score"], rec["keywords"], rec["text"][:1000],
                       rec["link"], rec["media"], rec["id"]])
        ws.freeze_panes = "A2"
        widths = [12, 22, 16, 20, 10, 8, 24, 60, 30, 10, 18]
        for i, wd in enumerate(widths, 1):
            ws.column_dimensions[chr(64 + i)].width = wd
        path = os.path.join(REPORTS_DIR, f"crm_leads_{stamp}.xlsx")
        wb.save(path)
        return path
    except Exception as e:
        print(f"⚠️ خروجی اکسل ناموفق بود: {e}")
        return None


if __name__ == "__main__":
    import sys
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    export_crm(d)
