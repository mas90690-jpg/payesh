# core/daily_reporter.py
"""
گزارش‌ساز خودکار روزانه - نسخه یکپارچه
شامل: گزارش روزانه، هفتگی، مدیریتی، تخصصی، جستجوی سرنخ
"""

import asyncio
import os
import aiosqlite
import sqlite3
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from core.logger import log_info, log_success, log_error, log_warning
from config import REPORTS_DIR, DB_DIR, SQLITE_CONFIG

# اطمینان از وجود پوشه reports
os.makedirs(REPORTS_DIR, exist_ok=True)

# مسیر دیتابیس
DB_PATH = os.path.join(DB_DIR, "messages.db")

# ==================================================
# ترتیب و برچسبِ ثابتِ پلتفرم‌ها — منبعِ یکتای نظمِ گزارش‌ها
# همهٔ گزارش‌ها به همین ترتیب و با همین برچسب‌ها گروه‌بندی می‌شوند تا
# خروجی منسجم و مرتب باشد، نه پخش‌وپلا.
# ==================================================
PLATFORM_ORDER = ["eitaa", "bale", "rubika", "telegram", "whatsapp", "web", "linkedin"]
PLATFORM_LABELS = {
    "eitaa": "📱 ایتا",
    "bale": "📨 بله",
    "rubika": "🟣 روبیکا",
    "telegram": "✈️ تلگرام",
    "whatsapp": "🟢 واتساپ",
    "web": "🌐 وب",
    "linkedin": "💼 لینکدین",
}


def platform_label(platform: str) -> str:
    """برچسب فارسیِ پلتفرم (با ایموجی)؛ ناشناخته‌ها همان نام خودشان."""
    return PLATFORM_LABELS.get((platform or "").lower(), f"📍 {platform or 'نامشخص'}")


def platform_rank(platform: str) -> int:
    """رتبهٔ پلتفرم در ترتیب ثابت (برای مرتب‌سازی)؛ ناشناخته‌ها ته صف."""
    p = (platform or "").lower()
    return PLATFORM_ORDER.index(p) if p in PLATFORM_ORDER else len(PLATFORM_ORDER) + 1


def group_by_platform(rows):
    """ردیف‌ها را به‌ترتیبِ ثابتِ پلتفرم گروه‌بندی می‌کند.

    ورودی: لیستی از ردیف‌ها (Row یا dict یا tuple با کلید/ایندکس 'platform').
    خروجی: لیستِ (platform, [rows...]) فقط برای پلتفرم‌هایی که سرنخ دارند، مرتب.
    """
    buckets = {}
    for r in rows:
        try:
            p = (r["platform"] or "").lower()
        except (TypeError, KeyError, IndexError):
            p = ""
        buckets.setdefault(p, []).append(r)
    return [(p, buckets[p]) for p in sorted(buckets, key=platform_rank)]


def dedup_rows(rows):
    """حذفِ سرنخ‌های تقریباً یکسان (همان گروه + همان متن) — مثلاً فوروارد/بازنشر.
    اولین رخداد نگه داشته می‌شود (که چون مرتب بر امتیاز است، باکیفیت‌ترین می‌ماند)."""
    seen = set()
    out = []
    for r in rows:
        try:
            chat = (r["chat_name"] or "")
            txt = (r["text"] or "")[:200].strip()
        except (TypeError, KeyError, IndexError):
            chat, txt = "", ""
        key = (chat, txt)
        if txt and key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def platform_summary_line(rows) -> str:
    """خط خلاصهٔ یک‌خطی: «ایتا ۵، بله ۳، تلگرام ۲» به ترتیب ثابت."""
    parts = []
    for p, items in group_by_platform(rows):
        parts.append(f"{platform_label(p)} {len(items)}")
    return " | ".join(parts) if parts else "—"


# ==================================================
# کلاس اصلی گزارش‌ساز
# ==================================================

class DailyReporter:
    """گزارش‌ساز خودکار روزانه - نسخه یکپارچه"""

    def __init__(self, db=None):
        self.db = db
        self.scheduler = AsyncIOScheduler()
        self.last_report_date = None
        self.db_path = DB_PATH

    # ========== گزارش خودکار روزانه ==========

    async def generate_report(self, target_date: str = None):
        """تولید گزارش برای تاریخ مشخص (پیش‌فرض: دیروز)"""
        if target_date:
            report_date = target_date
        else:
            today = datetime.now().strftime("%Y-%m-%d")
            report_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        if self.last_report_date == report_date:
            log_info(f"گزارش {report_date} قبلاً تولید شده است")
            return

        log_info(f"📊 شروع تولید گزارش روزانه - {report_date}")

        leads_data = await self._get_leads_by_date(report_date)

        if not leads_data or leads_data['total'] == 0:
            log_info(f"هیچ سرنخی برای تاریخ {report_date} وجود ندارد")
            return

        txt_file = await self._save_text_report(leads_data, report_date)
        html_file = await self._save_html_report(leads_data, report_date)
        csv_file = await self._save_csv_report(leads_data, report_date)
        json_file = await self._save_json_report(leads_data, report_date)

        self.last_report_date = report_date
        log_success(f"✅ گزارش روزانه ذخیره شد: {txt_file}")
        log_success(f"   📄 HTML: {html_file}")
        log_success(f"   📊 CSV: {csv_file}")
        log_success(f"   📋 JSON: {json_file}")

        return {"txt": txt_file, "html": html_file, "csv": csv_file, "json": json_file,
                "date": report_date, "total": leads_data['total']}

    async def generate_today_report(self):
        """تولید گزارش برای امروز"""
        return await self.generate_report(datetime.now().strftime("%Y-%m-%d"))

    # ========== گزارش هفتگی ==========

    async def generate_weekly_report(self):
        """تولید گزارش هفتگی"""
        end_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        log_info(f"📊 شروع تولید گزارش هفتگی - {start_date} تا {end_date}")

        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("""
                SELECT id, platform, chat_name, text, keywords, timestamp
                FROM messages 
                WHERE date(timestamp) BETWEEN ? AND ? AND keywords != ''
                ORDER BY timestamp DESC
            """, (start_date, end_date))
            messages = await cursor.fetchall()

            if not messages:
                log_info("هیچ سرنخی در این بازه وجود ندارد")
                return None

            filename = os.path.join(REPORTS_DIR, f"weekly_report_{start_date}_to_{end_date}.txt")
            with open(filename, "w", encoding="utf-8") as f:
                f.write("="*70 + "\n")
                f.write(f"📊 گزارش هفتگی سرنخ‌های فروش\n")
                f.write(f"بازه: {start_date} تا {end_date}\n")
                f.write("="*70 + "\n")
                f.write(f"📈 کل سرنخ‌ها: {len(messages)}\n")
                f.write(f"🧭 به تفکیک پلتفرم: {platform_summary_line(messages)}\n")

                # بخش‌بندی به ترتیب ثابتِ پلتفرم
                for platform, rows in group_by_platform(messages):
                    f.write("\n" + "═"*70 + "\n")
                    f.write(f"{platform_label(platform)} — {len(rows)} سرنخ\n")
                    f.write("═"*70 + "\n")
                    for i, msg in enumerate(rows, 1):
                        f.write(f"[{i}] گروه: {msg['chat_name']}  |  🔑 {msg['keywords']}\n")
                        f.write(f"    🕒 {msg['timestamp']}\n")
                        f.write(f"    💬 {msg['text'][:300] if msg['text'] else ''}\n")

            log_success(f"✅ گزارش هفتگی ذخیره شد: {filename}")
            return filename

    # ========== گزارش مدیریتی (برای مدیرعامل) ==========

    def generate_executive_report(self, date_from=None, date_to=None):
        """
        گزارش خلاصه برای مدیرعامل (همزمان - برای استفاده در ترمینال)
        شامل: تعداد کل سرنخ، تفکیک پلتفرم، تفکیک گروه‌های برتر
        """
        if date_from is None:
            date_from = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        if date_to is None:
            date_to = datetime.now().strftime('%Y-%m-%d')

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        cur.execute("""
            SELECT COUNT(*) FROM messages 
            WHERE keywords != '' AND date(timestamp) BETWEEN ? AND ?
        """, (date_from, date_to))
        total_leads = cur.fetchone()[0]

        cur.execute("""
            SELECT platform, COUNT(*) FROM messages 
            WHERE keywords != '' AND date(timestamp) BETWEEN ? AND ?
            GROUP BY platform
        """, (date_from, date_to))
        platform_stats = cur.fetchall()

        cur.execute("""
            SELECT chat_name, platform, COUNT(*) as cnt, MAX(chat_link) as link FROM messages
            WHERE keywords != '' AND date(timestamp) BETWEEN ? AND ?
            GROUP BY chat_name, platform
            ORDER BY cnt DESC LIMIT 5
        """, (date_from, date_to))
        top_groups = cur.fetchall()

        cur.execute("""
            SELECT keywords, COUNT(*) as cnt FROM messages 
            WHERE keywords != '' AND date(timestamp) BETWEEN ? AND ?
            GROUP BY keywords
            ORDER BY cnt DESC LIMIT 10
        """, (date_from, date_to))
        top_keywords = cur.fetchall()

        conn.close()

        report_file = os.path.join(REPORTS_DIR, f"executive_{date_from}_to_{date_to}.txt")
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(f"📊 گزارش مدیریتی (مدیرعامل)\n")
            f.write(f"بازه: {date_from} تا {date_to}\n")
            f.write("="*60 + "\n")
            f.write(f"🔹 تعداد کل فرصت‌ها (سرنخ): {total_leads}\n\n")

            f.write("🔸 تفکیک بر اساس پلتفرم:\n")
            for platform, cnt in sorted(platform_stats, key=lambda x: platform_rank(x[0])):
                f.write(f"   • {platform_label(platform)}: {cnt} سرنخ\n")
            f.write("\n")

            f.write("🔸 پرسرنخ‌ترین گروه‌ها:\n")
            for chat, platform, cnt, link in sorted(
                top_groups, key=lambda x: (platform_rank(x[1]), -x[2])
            ):
                line = f"   • {chat} ({platform_label(platform)}): {cnt} سرنخ"
                if link:
                    line += f" — 🔗 {link}"
                f.write(line + "\n")
            f.write("\n")

            f.write("🔸 کلیدواژه‌های داغ:\n")
            for kw, cnt in top_keywords:
                f.write(f"   • {kw}: {cnt} بار\n")
            f.write("\n")

            f.write("📌 توصیه: برای مشاهده جزئیات کامل، گزارش تخصصی را درخواست کنید.\n")

        print(f"✅ گزارش مدیریتی ذخیره شد: {report_file}")
        return report_file

    # ========== گزارش تخصصی (جزئیات کامل) ==========

    def generate_detailed_report(self, platform=None, chat_name=None, keyword=None, 
                                  date_from=None, date_to=None, limit=200):
        """
        گزارش کامل برای کارشناس (همزمان - برای استفاده در ترمینال)
        امکان فیلتر بر اساس پلتفرم، نام گروه، کلیدواژه، بازه زمانی
        خروجی: CSV، TXT، JSON
        """
        conn = sqlite3.connect(self.db_path)
        query = """
            SELECT id, platform, chat_name, chat_link, chat_username, chat_type,
                   text, timestamp, keywords, media_type, file_id, caption, reply_to
            FROM messages
            WHERE keywords != ''
        """
        params = []
        conditions = []

        if platform:
            conditions.append("platform = ?")
            params.append(platform)
        if chat_name:
            conditions.append("chat_name LIKE ?")
            params.append(f"%{chat_name}%")
        if keyword:
            conditions.append("keywords LIKE ?")
            params.append(f"%{keyword}%")
        if date_from:
            conditions.append("date(timestamp) >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("date(timestamp) <= ?")
            params.append(date_to)

        if conditions:
            query += " AND " + " AND ".join(conditions)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cur = conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()
        conn.close()

        # مرتب‌سازی به ترتیب ثابتِ پلتفرم (sort پایدار → ترتیب زمانیِ SQL درون هر پلتفرم حفظ می‌شود)
        rows = sorted(rows, key=lambda r: platform_rank(r[1]))

        if not rows:
            print("⚠️ هیچ سرنخی با فیلترهای انتخاب شده یافت نشد.")
            return None

        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_file = os.path.join(REPORTS_DIR, f"detailed_{timestamp_str}.csv")
        txt_file = os.path.join(REPORTS_DIR, f"detailed_{timestamp_str}.txt")
        json_file = os.path.join(REPORTS_DIR, f"detailed_{timestamp_str}.json")

        # CSV
        with open(csv_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['شناسه پیام', 'پلتفرم', 'نام گروه', 'لینک گروه', 'یوزرنیم گروه',
                            'نوع گروه', 'متن پیام', 'زمان', 'کلیدواژه‌ها', 'نوع رسانه',
                            'فایل ID', 'توضیحات', 'پاسخ به'])
            for row in rows:
                writer.writerow(row)

        # TXT
        with open(txt_file, 'w', encoding='utf-8') as f:
            f.write(f"📄 گزارش تخصصی - جزئیات سرنخ‌ها\n")
            f.write(f"تاریخ تولید: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"تعداد سرنخ‌ها: {len(rows)}\n")
            if platform:
                f.write(f"فیلتر پلتفرم: {platform}\n")
            if chat_name:
                f.write(f"فیلتر گروه: {chat_name}\n")
            if keyword:
                f.write(f"فیلتر کلیدواژه: {keyword}\n")
            f.write("="*80 + "\n\n")

            for idx, row in enumerate(rows, 1):
                (msg_id, platform, chat, chat_link, chat_username, chat_type,
                 text, ts, keywords, media_type, file_id, caption, reply_to) = row
                f.write(f"[{idx}] ───────────────────────────────────────\n")
                f.write(f"🆔 شناسه: {msg_id}\n")
                f.write(f"📱 پلتفرم: {platform}\n")
                f.write(f"👥 گروه: {chat}\n")
                if chat_type:
                    f.write(f"🏷️ نوع: {chat_type}\n")
                if chat_username:
                    f.write(f"🔖 یوزرنیم: @{chat_username}\n")
                if chat_link:
                    f.write(f"🔗 لینک گروه: {chat_link}\n")
                f.write(f"🕒 زمان: {ts}\n")
                f.write(f"🔑 کلیدواژه‌ها: {keywords}\n")
                if media_type:
                    f.write(f"📎 نوع پیام: {media_type}\n")
                if file_id:
                    f.write(f"📁 فایل: {file_id}\n")
                if caption:
                    f.write(f"📝 توضیحات: {caption}\n")
                f.write(f"💬 متن پیام:\n{text}\n\n")

        # JSON
        data = []
        for row in rows:
            (msg_id, platform, chat, chat_link, chat_username, chat_type,
             text, ts, keywords, media_type, file_id, caption, reply_to) = row
            data.append({
                "id": msg_id,
                "platform": platform,
                "chat_name": chat,
                "chat_link": chat_link,
                "chat_username": chat_username,
                "chat_type": chat_type,
                "text": text,
                "timestamp": ts,
                "keywords": keywords.split(',') if keywords else [],
                "media_type": media_type,
                "file_id": file_id,
                "caption": caption,
                "reply_to": reply_to
            })
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"✅ گزارش تخصصی ذخیره شد: {txt_file}")
        print(f"   • CSV: {csv_file}")
        print(f"   • JSON: {json_file}")
        return txt_file

    # ========== جستجوی سرنخ خاص ==========

    def search_lead_by_message_id(self, message_id: str, platform: str = None):
        """جستجوی یک سرنخ با شناسه پیام و پلتفرم"""
        conn = sqlite3.connect(self.db_path)
        if platform:
            cur = conn.execute("""
                SELECT id, platform, chat_name, text, timestamp, keywords
                FROM messages
                WHERE id = ? AND platform = ?
            """, (message_id, platform))
        else:
            cur = conn.execute("""
                SELECT id, platform, chat_name, text, timestamp, keywords
                FROM messages
                WHERE id = ?
            """, (message_id,))
        
        row = cur.fetchone()
        conn.close()
        
        if row:
            print(f"\n🔍 سرنخ یافت شد:")
            print(f"   پلتفرم: {row[1]}")
            print(f"   گروه: {row[2]}")
            print(f"   زمان: {row[4]}")
            print(f"   متن: {row[3]}")
            print(f"   کلیدواژه: {row[5]}")
            return row
        else:
            print("❌ سرنخی با این مشخصات یافت نشد.")
            return None

    # ========== متدهای داخلی (async) ==========

    async def _get_leads_by_date(self, date: str):
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("""
                SELECT id, platform, chat_name, user_id, text, keywords,
                       media_type, opportunity_score, opportunity_type, timestamp
                FROM messages
                WHERE date(timestamp) = ? AND keywords != ''
                ORDER BY opportunity_score DESC, timestamp DESC
            """, (date,))
            messages = await cursor.fetchall()

            cursor = await conn.execute("""
                SELECT chat_name, platform, COUNT(*) as count 
                FROM messages 
                WHERE date(timestamp) = ? AND keywords != ''
                GROUP BY chat_name, platform 
                ORDER BY count DESC
            """, (date,))
            group_stats = await cursor.fetchall()

            cursor = await conn.execute("""
                SELECT keywords, COUNT(*) as count 
                FROM messages 
                WHERE date(timestamp) = ? AND keywords != ''
                GROUP BY keywords 
                ORDER BY count DESC 
                LIMIT 15
            """, (date,))
            keyword_stats = await cursor.fetchall()

            return {
                "date": date,
                "total": len(messages),
                "messages": messages,
                "group_stats": group_stats,
                "keyword_stats": keyword_stats
            }

    @staticmethod
    def _count_top(items, key, limit=8):
        """شمارش و مرتب‌سازیِ نزولیِ یک ستون (برای گروه‌ها/کلیدواژه‌ها)."""
        counts = {}
        for it in items:
            val = it[key]
            if not val:
                continue
            # کلیدواژه‌ها ممکن است با کاما چندتایی باشند
            parts = [p.strip() for p in str(val).split(",")] if key == "keywords" else [str(val)]
            for p in parts:
                if p:
                    counts[p] = counts.get(p, 0) + 1
        return sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]

    async def _save_text_report(self, data, date):
        filename = os.path.join(REPORTS_DIR, f"daily_report_{date}.txt")
        with open(filename, "w", encoding="utf-8") as f:
            f.write("="*70 + "\n")
            f.write(f"📊 گزارش روزانه سرنخ‌های فروش — {date}\n")
            f.write("="*70 + "\n")
            f.write(f"📈 کل سرنخ‌ها: {data['total']}\n")
            f.write(f"🧭 به تفکیک پلتفرم: {platform_summary_line(data['messages'])}\n")

            # بدنهٔ گزارش: پلتفرم به پلتفرم، به ترتیب ثابت
            for platform, rows in group_by_platform(data['messages']):
                f.write("\n" + "═"*70 + "\n")
                f.write(f"{platform_label(platform)} — {len(rows)} سرنخ\n")
                f.write("═"*70 + "\n")

                top_groups = self._count_top(rows, "chat_name")
                if top_groups:
                    f.write("🏆 پرسرنخ‌ترین گروه‌ها: "
                            + "، ".join(f"{g} ({c})" for g, c in top_groups) + "\n")
                top_kw = self._count_top(rows, "keywords")
                if top_kw:
                    f.write("🔑 کلیدواژه‌های داغ: "
                            + "، ".join(f"{k} ({c})" for k, c in top_kw) + "\n")
                f.write("-"*70 + "\n")

                for i, msg in enumerate(dedup_rows(rows), 1):
                    f.write(f"[{i}] گروه: {msg['chat_name']}  |  🔑 {msg['keywords']}")
                    score = msg['opportunity_score'] if 'opportunity_score' in msg.keys() else 0
                    lvl = msg['opportunity_type'] if 'opportunity_type' in msg.keys() else None
                    if score:
                        f.write(f"  |  🎯 {lvl or ''} (امتیاز {score})")
                    f.write(f"\n    🕒 {msg['timestamp']}\n")
                    f.write(f"    💬 {msg['text'][:300] if msg['text'] else ''}\n")
        return filename

    async def _save_html_report(self, data, date):
        import html as _html
        filename = os.path.join(REPORTS_DIR, f"daily_report_{date}.html")
        head = f"""<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <title>گزارش سرنخ‌ها - {date}</title>
    <style>
        body {{ font-family: Tahoma, Arial, sans-serif; margin: 20px; background: #f0f2f5; }}
        .container {{ max-width: 900px; margin: auto; background: white; padding: 25px; border-radius: 12px; }}
        h1 {{ color: #2c3e50; border-bottom: 3px solid #27ae60; padding-bottom: 10px; }}
        h2 {{ color: #fff; background: #34495e; padding: 8px 12px; border-radius: 8px; margin-top: 25px; }}
        .stat-box {{ background: #e8f5e9; padding: 15px; border-radius: 10px; margin: 15px 0; text-align: center; }}
        .stat-number {{ font-size: 36px; font-weight: bold; color: #27ae60; }}
        .summary {{ background: #eef3f8; padding: 10px 14px; border-radius: 8px; margin: 10px 0; }}
        .message-card {{ background: #fafafa; padding: 12px; margin: 12px 0; border-radius: 8px; border-right: 3px solid #27ae60; }}
        .kw {{ color: #c0392b; font-weight: bold; }}
        small {{ color: #888; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 گزارش روزانه سرنخ‌های فروش</h1>
        <p><strong>تاریخ:</strong> {date}</p>
        <div class="stat-box">
            <div class="stat-number">{data['total']}</div>
            <div>تعداد کل سرنخ‌ها</div>
        </div>
        <div class="summary">🧭 به تفکیک پلتفرم: {_html.escape(platform_summary_line(data['messages']))}</div>
"""
        body = ""
        # بخش‌بندی به ترتیب ثابتِ پلتفرم
        for platform, rows in group_by_platform(data['messages']):
            body += f'\n        <h2>{platform_label(platform)} — {len(rows)} سرنخ</h2>'
            for msg in rows:
                body += f"""
        <div class="message-card">
            <strong>{_html.escape(str(msg['chat_name'] or ''))}</strong> | <span class="kw">🔑 {_html.escape(str(msg['keywords'] or ''))}</span><br>
            <p>{_html.escape((msg['text'] or '')[:300])}</p>
            <small>{_html.escape(str(msg['timestamp'] or ''))}</small>
        </div>"""
        tail = """
    </div>
</body>
</html>"""
        with open(filename, "w", encoding="utf-8") as f:
            f.write(head + body + tail)
        return filename

    async def _save_csv_report(self, data, date):
        filename = os.path.join(REPORTS_DIR, f"daily_report_{date}.csv")
        with open(filename, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(['پلتفرم', 'شناسه', 'گروه', 'متن', 'کلیدواژه', 'زمان'])
            # مرتب به ترتیب ثابتِ پلتفرم (ستون پلتفرم اول، تا فایل منسجم باشد)
            for platform, rows in group_by_platform(data['messages']):
                for msg in rows:
                    writer.writerow([
                        platform, msg['id'], msg['chat_name'],
                        msg['text'][:500] if msg['text'] else '',
                        msg['keywords'], msg['timestamp']
                    ])
        return filename

    async def _save_json_report(self, data, date):
        filename = os.path.join(REPORTS_DIR, f"daily_report_{date}.json")
        # خروجی گروه‌بندی‌شده بر اساس پلتفرم (به ترتیب ثابت) — منسجم و ماشین‌خوان
        by_platform = []
        for platform, rows in group_by_platform(data['messages']):
            by_platform.append({
                "platform": platform,
                "label": PLATFORM_LABELS.get(platform, platform),
                "count": len(rows),
                "leads": [
                    {
                        "id": msg['id'],
                        "chat_name": msg['chat_name'],
                        "text": msg['text'][:500] if msg['text'] else '',
                        "keywords": msg['keywords'],
                        "timestamp": msg['timestamp'],
                    }
                    for msg in rows
                ],
            })
        output = {
            "date": date,
            "total": data['total'],
            "summary": platform_summary_line(data['messages']),
            "platforms": by_platform,
        }
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        return filename

    # ========== زمان‌بندی ==========

    def start(self, report_hour=8, report_minute=0,
              weekly_day="sunday", weekly_hour=9, weekly_minute=0):
        """شروع زمان‌بندی گزارش روزانه و هفتگی (ساعت‌ها از .env می‌آیند)."""
        self.scheduler.add_job(
            lambda: asyncio.create_task(self.generate_report()),
            'cron', hour=report_hour, minute=report_minute, id='daily_report'
        )
        day_map = {"saturday": "sat", "sunday": "sun", "monday": "mon", "tuesday": "tue",
                   "wednesday": "wed", "thursday": "thu", "friday": "fri"}
        dow = day_map.get(str(weekly_day).lower(), "sun")
        self.scheduler.add_job(
            lambda: asyncio.create_task(self.generate_weekly_report()),
            'cron', day_of_week=dow, hour=weekly_hour, minute=weekly_minute, id='weekly_report'
        )
        # گزارشِ ماهانه: اولِ هر ماه
        self.scheduler.add_job(
            lambda: asyncio.create_task(self.generate_monthly_report()),
            'cron', day=1, hour=report_hour, minute=(report_minute + 5) % 60, id='monthly_report'
        )
        self.scheduler.start()
        log_success(
            f"📅 گزارش روزانه ساعت {report_hour}:{report_minute:02d}، "
            f"هفتگی ({dow}) ساعت {weekly_hour}:{weekly_minute:02d}، و ماهانه (اولِ ماه) زمان‌بندی شد"
        )

    async def generate_monthly_report(self):
        """گزارشِ ماهانه (۳۰ روزِ اخیر) — مدیریتی + تخصصی، به‌ترتیبِ پلتفرم."""
        date_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        date_to = datetime.now().strftime("%Y-%m-%d")
        log_info(f"📊 گزارش ماهانه {date_from} تا {date_to}")
        try:
            path = self.generate_executive_report(date_from, date_to)
            self.generate_detailed_report(date_from=date_from, date_to=date_to, limit=500)
            log_success(f"✅ گزارش ماهانه ذخیره شد: {path}")
            return path
        except Exception as e:
            log_error(f"خطا در گزارش ماهانه: {e}")
            return None


# ==================================================
# توابع سطح بالا برای استفاده آسان
# ==================================================

async def start_daily_reporter(db):
    """راه‌اندازی گزارش‌ساز خودکار (تنظیمات از .env)."""
    from config import REPORT
    reporter = DailyReporter(db)
    if not REPORT.get("enabled", True):
        log_info("📅 گزارش خودکار غیرفعال است (REPORT_ENABLED=false)")
        return reporter

    def _parse(t, dh, dm):
        try:
            h, m = str(t).split(":")
            return int(h), int(m)
        except Exception:
            return dh, dm

    dh, dm = _parse(REPORT.get("daily_time", "08:00"), 8, 0)
    wh, wm = _parse(REPORT.get("weekly_time", "09:00"), 9, 0)
    reporter.start(report_hour=dh, report_minute=dm,
                   weekly_day=REPORT.get("weekly_day", "sunday"),
                   weekly_hour=wh, weekly_minute=wm)
    return reporter


def generate_executive_report(date_from=None, date_to=None):
    """گزارش مدیریتی (همزمان) - برای استفاده در ترمینال"""
    reporter = DailyReporter()
    return reporter.generate_executive_report(date_from, date_to)


def generate_detailed_report(platform=None, chat_name=None, keyword=None, 
                              date_from=None, date_to=None, limit=200):
    """گزارش تخصصی (همزمان) - برای استفاده در ترمینال"""
    reporter = DailyReporter()
    return reporter.generate_detailed_report(platform, chat_name, keyword, 
                                              date_from, date_to, limit)


def search_lead(message_id: str, platform: str = None):
    """جستجوی سرنخ با شناسه پیام"""
    reporter = DailyReporter()
    return reporter.search_lead_by_message_id(message_id, platform)


def generate_daily_report():
    """گزارش روزانه (همزمان) - برای سازگاری با کد قدیمی"""
    reporter = DailyReporter()
    import asyncio
    return asyncio.run(reporter.generate_report())


def generate_weekly_report():
    """گزارش هفتگی (همزمان) - برای سازگاری با کد قدیمی"""
    reporter = DailyReporter()
    import asyncio
    return asyncio.run(reporter.generate_weekly_report())


# ==================================================
# اجرای مستقیم برای تست
# ==================================================

if __name__ == "__main__":
    print("📊 در حال تولید گزارش‌های نمونه...\n")
    generate_executive_report()
    generate_detailed_report(limit=10)