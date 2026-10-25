# tests/test_bale_full.py
"""
تست کامل و فوق‌دقیق بله (Bale) — نسخهٔ ۶.۰ (Comprehensive)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from tests.helpers import (
    TestReport, banner, check_env, check_session_exists,
    safe_disconnect, section, C,
)
from tests.pipeline_helpers import TestPipeline


# ═══════════════════════════════════════════════════════════
# ثابت‌ها
# ═══════════════════════════════════════════════════════════
SYSTEM_CHAT_NAMES = {
    "فضای شخصی", "پیش‌نویس", "شما", "تصویر", "پیام",
    "ذخیره‌شده", "ذخیره شده", "پیام‌های ذخیره‌شده",
    "بایگانی", "آرشیو",
    "Saved Messages", "Drafts", "You", "Images", "Messages",
    "Archive", "Archived",
    "بله", "Bale", "Bale Messenger", "پیام‌های بله",
    "Bale Team", "تیم بله", "پشتیبانی بله",
    "فایل", "فایل‌ها", "Files",
    "استیکر", "استیکرها", "Stickers",
    "گیف", "GIFs", "ویدیو", "Videos", "ویس", "Voice",
    "عکس", "Photos", "لینک", "Links",
    "مخاطبین", "مخاطبین من", "Contacts",
    "پوشه‌ها", "Folders", "گروه‌ها", "Groups",
    "کانال‌ها", "Channels", "فایل‌های من",
    "کانال ایجاد شد", "گروه ایجاد شد",
    "Channel created", "Group created",
    "آلبوم", "Albums",
}

SYSTEM_PREFIXES = (
    "کانال ایجاد", "گروه ایجاد", "Channel created",
    "Group created", "کانال جدید", "گروه جدید",
)

VALID_CHAT_TYPES = {"private", "group", "channel"}
VALID_MEDIA_TYPES = {
    "text", "photo", "video", "voice", "audio",
    "document", "sticker", "file", "gif",
}

REQUIRED_MSG_FIELDS = [
    "id", "platform", "text", "chat_id", "chat_name",
    "chat_type", "chat_username", "chat_link",
    "user_id", "sender_name", "timestamp",
    "media_type", "file_id", "local_file_path", "reply_to",
]

MAX_FETCH_SECONDS = 400


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════
def _is_api_based(collector: Any) -> bool:
    has_page = hasattr(collector, "_page") and collector._page is not None
    has_client = (
        hasattr(collector, "_client")
        and collector._client is not None
    )
    return has_client and not has_page


def _is_system_chat(chat_name: str) -> bool:
    if not chat_name:
        return False
    n = chat_name.strip()
    if not n:
        return False
    if n in SYSTEM_CHAT_NAMES:
        return True
    for prefix in SYSTEM_PREFIXES:
        if n.startswith(prefix):
            return True
    return False


def _is_valid_text(text: str) -> bool:
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    if len(stripped) < 1:
        return False
    return True


# ═══════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════
async def main(
    headless: bool = False,
    max_chats: int = 0,
    since_days: int = 7,
):
    banner("تست دقیق بله (Bale) — نسخهٔ ۶.۰ (فوق‌العاده دقیق)")
    report = TestReport("bale")

    # ═══════════════════════════════════════════════════
    # A) پیش‌نیازها
    # ═══════════════════════════════════════════════════
    section("A) پیش‌نیازها")
    try:
        import aiobale  # noqa: F401
        report.add("A1.aiobale", "ok", f"version={getattr(aiobale, '__version__', '?')}")
    except ImportError:
        report.add("A1.aiobale", "info", "aiobale نصب نیست (DOM)")

    env_info = check_env("bale")
    enabled = env_info["enabled"]
    report.add("A2.env.enabled", "ok" if enabled else "skip", f"BALE_ENABLED={enabled}")
    if not enabled:
        print(f"\n{C.INFO}⏭️ بله غیرفعال — تست متوقف شد{C.END}")
        report.print_summary()
        report.save()
        return True

    phone = env_info.get("phone", "")
    report.add("A3.env.phone", "ok" if phone else "fail", f"phone={phone[:6]}..." if phone else "خالی")

    session_path = env_info.get("session_path", "")
    report.add("A4.env.session_path", "ok" if session_path else "fail", session_path or "خالی")

    session_exists = check_session_exists(session_path) if session_path else False
    report.add("A5.session.exists", "ok" if session_exists else "warn", f"exists={session_exists}")

    # ═══════════════════════════════════════════════════
    # B) ساخت pipeline
    # ═══════════════════════════════════════════════════
    section("B) ساخت pipeline")
    pipeline: TestPipeline | None = None
    try:
        pipeline = TestPipeline(
            keywords_only=False,
            require_t1=False,
            allowed_tiers=("T1", "T2", "T3"),
        )
        await pipeline.start()
        report.add("B1.pipeline.init", "ok", f"db={os.path.basename(pipeline._db_path or '?')}")
        report.add("B2.pipeline.db", "ok", "DB تست ساخته شد")
    except Exception as e:
        report.add("B1.pipeline.init", "fail", f"{type(e).__name__}: {e}")
        report.print_summary()
        report.save()
        return False

    # ═══════════════════════════════════════════════════
    # C) ساخت کالکتور (از .env)
    # ═══════════════════════════════════════════════════
    section("C) ساخت کالکتور")
    collector = None
    is_api = False
    try:
        from collectors.bale import BaleCollector

        mpc = int(os.getenv("BALE_MESSAGES_PER_CHAT", "500"))
        bf = int(os.getenv("BALE_BACKFILL_SCROLLS", "10"))
        delay = float(os.getenv("BALE_CHAT_DELAY", "1.5"))

        collector = BaleCollector(
            phone=phone or None,
            headless=headless,
            poll_seconds=999,
            max_chats=max_chats,
            messages_per_chat=mpc,
            backfill_scrolls=bf,
            chat_open_delay=delay,
        )
        is_api = _is_api_based(collector)
        report.add("C1.collector.init", "ok", f"mode={'API' if is_api else 'DOM'} | mpc={mpc} | bf={bf} | delay={delay}")
    except Exception as e:
        report.add("C1.collector.init", "fail", f"{type(e).__name__}: {e}")
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    pname = getattr(collector, "platform_name", None)
    report.add("C2.platform_name", "ok" if pname == "bale" else "fail", f"platform_name={pname}")

    poll = getattr(collector, "poll_interval_seconds", None)
    report.add("C3.poll_interval", "ok" if isinstance(poll, (int, float)) else "warn", f"poll={poll}")

    # ═══════════════════════════════════════════════════
    # D) اتصال
    # ═══════════════════════════════════════════════════
    section("D) اتصال")
    t0 = time.monotonic()
    connect_ok = False
    try:
        connect_ok = await collector.connect()
        elapsed = (time.monotonic() - t0) * 1000
        report.add("D1.connect", "ok" if connect_ok else "fail", f"ok={connect_ok} | mode={'API' if is_api else 'DOM'} | {elapsed:.0f}ms", elapsed)
    except Exception as e:
        report.add("D1.connect", "fail", f"{type(e).__name__}: {e}")

    if not connect_ok:
        print(f"\n{C.FAIL}❌ اتصال بله برقرار نشد{C.END}")
        await safe_disconnect(collector, report)
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    logged_in = False
    try:
        if is_api and hasattr(collector, "check_health"):
            logged_in = await collector.check_health()
            report.add("D2.is_logged_in", "ok" if logged_in else "fail", f"check_health={logged_in} | mode=API")
        else:
            logged_in = await collector._is_logged_in()
            report.add("D2.is_logged_in", "ok" if logged_in else "fail", f"logged_in={logged_in} | mode=DOM")
    except Exception as e:
        report.add("D2.is_logged_in", "fail", f"{type(e).__name__}: {e}")

    try:
        health = await collector.check_health()
        report.add("D3.check_health", "ok" if health else "warn", f"health={health}")
    except Exception as e:
        report.add("D3.check_health", "warn", f"{type(e).__name__}: {e}")

    try:
        url = ""
        if hasattr(collector, "_page") and collector._page:
            url = collector._page.url or ""
        if url:
            in_bale = "bale.ai" in url.lower()
            report.add("D4.page_url", "ok" if in_bale else "warn", f"url={url[:80]}")
        else:
            report.add("D4.page_url", "info", "URL در دسترس نیست (mode=API)")
    except Exception as e:
        report.add("D4.page_url", "warn", f"{type(e).__name__}: {e}")

    # ═══════════════════════════════════════════════════
    # E) fetch_messages
    # ═══════════════════════════════════════════════════
    section(f"E) fetch_messages (since={since_days} روز)")
    since_dt = (
        datetime.now() - timedelta(days=since_days)
        if since_days > 0 else None
    )

    msgs: list = []
    fetch_elapsed = 0.0
    try:
        t0 = time.monotonic()
        try:
            msgs = await collector.fetch_messages(since=since_dt)
        except TypeError:
            msgs = await collector.fetch_messages()
        fetch_elapsed = time.monotonic() - t0
        report.add("E1.fetch", "ok" if isinstance(msgs, list) else "fail", f"count={len(msgs) if msgs else 0} | {fetch_elapsed:.0f}s", fetch_elapsed * 1000)
    except Exception as e:
        report.add("E1.fetch", "fail", f"{type(e).__name__}: {e}")
        msgs = []

    if msgs:
        report.add("E2.fetch.count", "ok", f"{len(msgs)} پیام گرفته شد")
    else:
        report.add("E2.fetch.count", "warn", "هیچ پیامی گرفته نشد (شاید نشست تازه است)")

    if fetch_elapsed > MAX_FETCH_SECONDS:
        report.add("E3.fetch.timeout", "warn", f"{fetch_elapsed:.0f}s > {MAX_FETCH_SECONDS}s")
    else:
        report.add("E3.fetch.timeout", "ok", f"{fetch_elapsed:.0f}s ≤ {MAX_FETCH_SECONDS}s")

    if msgs and since_dt:
        too_old = [m for m in msgs if isinstance(m.get("timestamp"), datetime) and m["timestamp"] < since_dt]
        if too_old:
            report.add("E4.since.honored", "warn", f"{len(too_old)} پیام قدیمی‌تر از {since_dt.strftime('%Y-%m-%d')}")
        else:
            report.add("E4.since.honored", "ok", f"همه {len(msgs)} پیام جدیدتر از since")
    else:
        report.add("E4.since.honored", "skip", "پیامی نیست یا since داده نشد")

    stats = getattr(collector, "_last_scrape_stats", None)
    if isinstance(stats, dict):
        report.add("E5.scrape.stats", "ok", f"total={stats.get('total_chats', '?')} | processed={stats.get('processed', '?')} | system={stats.get('skipped_system', '?')} | failed={stats.get('failed', '?')}")
    else:
        report.add("E5.scrape.stats", "warn", "کالکتور _last_scrape_stats نمی‌دهد")

    # ═══════════════════════════════════════════════════
    # F) ساختار پیام
    # ═══════════════════════════════════════════════════
    section("F) ساختار پیام")
    if not msgs:
        report.add("F.structure", "skip", "پیامی برای بررسی نیست")
    else:
        sample = msgs[0]

        missing_fields = [f for f in REQUIRED_MSG_FIELDS if f not in sample]
        report.add("F1.msg.fields", "ok" if not missing_fields else "fail", f"missing={missing_fields}" if missing_fields else f"همه {len(REQUIRED_MSG_FIELDS)} فیلد موجود")

        all_bale = all(m.get("platform") == "bale" for m in msgs)
        report.add("F2.msg.platform", "ok" if all_bale else "fail", f"همه پیام‌ها platform=bale: {all_bale}")

        ids = [str(m.get("id", "")) for m in msgs]
        unique_ids = set(ids)
        dup_count = len(ids) - len(unique_ids)
        report.add("F3.msg.id_unique", "ok" if dup_count == 0 else "fail", f"{dup_count} id تکراری از {len(ids)}")

        ts_ok = all(isinstance(m.get("timestamp"), datetime) for m in msgs)
        report.add("F4.msg.timestamp", "ok" if ts_ok else "warn", f"همه datetime: {ts_ok}")

        bad_ct = [m for m in msgs if m.get("chat_type") not in VALID_CHAT_TYPES]
        report.add("F5.msg.chat_type", "ok" if not bad_ct else "warn", f"{len(bad_ct)} chat_type نامعتبر" if bad_ct else f"همه معتبر")

        bad_mt = [m for m in msgs if m.get("media_type") not in VALID_MEDIA_TYPES]
        report.add("F6.msg.media_type", "ok" if not bad_mt else "warn", f"{len(bad_mt)} media_type نامعتبر" if bad_mt else f"همه معتبر")

        empty_text = [m for m in msgs if not _is_valid_text(m.get("text", ""))]
        report.add("F7.msg.text", "ok" if not empty_text else "fail", f"{len(empty_text)} پیام با متن خالی/whitespace" if empty_text else f"همه متن دارند")

        empty_name = [m for m in msgs if not (m.get("chat_name") or "").strip()]
        report.add("F8.msg.chat_name", "ok" if not empty_name else "fail", f"{len(empty_name)} پیام بدون chat_name" if empty_name else f"همه chat_name دارند")

    # ═══════════════════════════════════════════════════
    # G) فیلتر چت سیستمی
    # ═══════════════════════════════════════════════════
    section("G) فیلتر چت سیستمی")
    if msgs:
        system_in_output = [m.get("chat_name", "") for m in msgs if _is_system_chat(m.get("chat_name", ""))]
        unique_system = list(set(system_in_output))

        if unique_system:
            report.add("G1.system.filter", "fail", f"{len(system_in_output)} پیام از {len(unique_system)} چت سیستمی: {unique_system[:5]}")
        else:
            report.add("G1.system.filter", "ok", "هیچ چت سیستمی در خروجی نیست")

        real_chats = set()
        for m in msgs:
            cn = (m.get("chat_name") or "").strip()
            if cn and not _is_system_chat(cn):
                real_chats.add(cn)
        report.add("G2.real_chats", "ok", f"{len(real_chats)} چت واقعی")

        if real_chats:
            print(f"\n  📋 چت‌های واقعی ({len(real_chats)}):")
            for i, name in enumerate(sorted(real_chats)[:10], 1):
                print(f"     {i}. {name[:60]}")
            if len(real_chats) > 10:
                print(f"     ... و {len(real_chats) - 10} چت دیگر")
    else:
        report.add("G1.system.filter", "skip", "پیامی برای بررسی نیست")
        report.add("G2.real_chats", "skip", "پیامی نیست")

    # ═══════════════════════════════════════════════════
    # H) Pipeline → DB
    # ═══════════════════════════════════════════════════
    section("H) Pipeline → DB")
    stored = 0
    try:
        t0 = time.monotonic()
        stored = await pipeline.feed(msgs)
        elapsed = (time.monotonic() - t0) * 1000
        report.add("H1.pipeline.feed", "ok" if stored >= 0 else "fail", f"fed={len(msgs)} | stored={stored} | {elapsed:.0f}ms", elapsed)
    except Exception as e:
        report.add("H1.pipeline.feed", "fail", f"{type(e).__name__}: {e}")

    try:
        total_db = await pipeline.count_messages(platform="bale")
        report.add("H2.db.total", "ok", f"bale: {total_db} پیام در DB")
    except Exception as e:
        report.add("H2.db.total", "warn", f"{type(e).__name__}: {e}")
        total_db = 0

    try:
        total_leads = await pipeline.count_leads(platform="bale")
        report.add("H3.db.leads", "ok", f"bale: {total_leads} سرنخ")
    except Exception as e:
        report.add("H3.db.leads", "warn", f"{type(e).__name__}: {e}")
        total_leads = 0

    if msgs:
        try:
            found = await pipeline.find_in_db(msgs[:20], platform="bale")
            in_db = len(found)
            content_ok = 0
            content_bad = []
            for raw_id, row in found.items():
                original = next((m for m in msgs if str(m.get("id")) == raw_id), None)
                if not original:
                    continue
                orig_text = (original.get("text") or "").strip()
                db_text = (row.get("text") or "").strip()
                if orig_text[:100] != db_text[:100]:
                    content_bad.append(f"id={raw_id[:20]} text mismatch")
                else:
                    content_ok += 1

            if not content_bad:
                report.add("H4.db.content", "ok", f"{content_ok} پیام با محتوای درست")
            else:
                report.add("H4.db.content", "warn", f"{len(content_bad)} پیام با محتوای ناهمخوان: {content_bad[:3]}")
        except Exception as e:
            report.add("H4.db.content", "warn", f"{type(e).__name__}: {e}")

    # ═══════════════════════════════════════════════════
    # I) گزارش صریح
    # ═══════════════════════════════════════════════════
    section("I) گزارش صریح")
    if msgs and stored > 0 and total_leads == 0:
        report.add("I1.lead.clarify", "info", f"{stored} پیام ذخیره شد ولی 0 کلیدواژه داشت → Lead واقعی = 0")
        print(f"\n  ℹ️ {stored} پیام در DB ذخیره شد ولی 0 کلیدواژه داشت")
    elif total_leads > 0:
        report.add("I1.lead.clarify", "ok", f"{total_leads} Lead واقعی در DB")
    else:
        report.add("I1.lead.clarify", "skip", "پیامی برای بررسی نبود")

    if total_leads > 0:
        try:
            leads = await pipeline.find_leads_in_db("bale")
            print(f"\n  🎯 {min(3, len(leads))} lead نمونه:")
            for ld in leads[:3]:
                chat = (ld.get("chat_name") or "?")[:25]
                kw = (ld.get("keywords") or "—")[:40]
                print(f"     [{chat}] kw={kw}")
            report.add("I2.lead.sample", "ok", f"{min(3, len(leads))} نمونه نمایش داده شد")
        except Exception as e:
            report.add("I2.lead.sample", "warn", f"{type(e).__name__}: {e}")

    # ═══════════════════════════════════════════════════
    # J) پاک‌سازی
    # ═══════════════════════════════════════════════════
    section("J) پاک‌سازی")
    try:
        await safe_disconnect(collector, report)
    except Exception as e:
        report.add("J1.disconnect", "warn", f"{type(e).__name__}: {e}")

    try:
        await pipeline.stop()
        report.add("J2.pipeline.cleanup", "ok", "DB تست پاک شد")
    except Exception as e:
        report.add("J2.pipeline.cleanup", "warn", f"{type(e).__name__}: {e}")

    ok = report.print_summary()
    report.save()
    return ok


# ═══════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="تست دقیق بله — Pipeline end-to-end (v6.0)",
    )
    parser.add_argument("--headless", action="store_true", help="اجرای مرورگر بدون پنجره")
    parser.add_argument("--max-chats", type=int, default=0, help="حداکثر چت (0=همه)")
    parser.add_argument("--since-days", type=int, default=7, help="تاریخچه چند روز (0=بدون محدودیت)")
    args = parser.parse_args()

    ok = asyncio.run(
        main(
            headless=args.headless,
            max_chats=args.max_chats,
            since_days=args.since_days,
        )
    )
    sys.exit(0 if ok else 1)