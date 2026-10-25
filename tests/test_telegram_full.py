# tests/test_telegram_full.py
"""تست کامل تلگرام — Telegram Web k"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import (
    TestReport, banner, check_env, safe_disconnect, section, C,
)
from tests.pipeline_helpers import TestPipeline


SYSTEM_CHAT_NAMES = {
    "پیام‌های ذخیره شده", "پیامهای ذخیره شده", "ذخیره‌شده",
    "ذخیره شده", "پیش‌نویس", "پیش‌نویس‌ها", "شما",
    "Saved Messages", "Saved", "Drafts",
}

VALID_CHAT_TYPES = {"private", "group", "channel"}

REQUIRED_MSG_FIELDS = [
    "id", "platform", "text", "chat_id", "chat_name",
    "chat_type", "chat_username", "chat_link",
    "user_id", "sender_name", "timestamp",
    "media_type", "file_id", "local_file_path", "reply_to",
]


def _session_path() -> str:
    from config import STORAGE_DIR
    return os.path.join(STORAGE_DIR, "sessions", "telegram_web")


def _session_has_data(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    try:
        return len([e for e in os.listdir(path) if not e.startswith(".")]) > 0
    except Exception:
        return False


def _is_system_chat(name: str) -> bool:
    if not name:
        return False
    n = name.strip()
    if not n:
        return False
    if n in SYSTEM_CHAT_NAMES:
        return True
    nl = n.lower()
    return "saved" in nl or "draft" in nl


async def main(headless=False, max_chats=0, since_days=7):
    banner("تست دقیق تلگرام (Telegram Web) — v7.0")
    report = TestReport("telegram")

    section("A) پیش‌نیازها")
    env = check_env("telegram")
    if not env["enabled"]:
        report.add("A1.env", "skip", "TELEGRAM_ENABLED=False")
        report.print_summary(); report.save()
        return True
    report.add("A1.env", "ok", "TELEGRAM_ENABLED=True")

    sp = _session_path()
    if not _session_has_data(sp):
        report.add("A2.session", "fail", f"not found: {sp}")
        report.print_summary(); report.save()
        return False
    report.add("A2.session", "ok", sp)

    section("B) pipeline")
    pipeline = TestPipeline(
        keywords_only=False, require_t1=False,
        allowed_tiers=("T1", "T2", "T3"),
    )
    await pipeline.start()
    report.add("B1.pipeline", "ok", f"db={os.path.basename(pipeline._db_path)}")

    section("C) collector")
    from collectors.telegram import TelegramWebCollector
    mpc = int(os.getenv("TELEGRAM_MESSAGES_PER_CHAT", "500"))
    collector = TelegramWebCollector(
        headless=headless, poll_seconds=999,
        max_chats=max_chats, messages_per_chat=mpc,
        backfill_scrolls=6, chat_open_delay=1.5,
    )
    report.add("C1.collector", "ok", f"mpc={mpc} | max={max_chats or '∞'}")

    section("D) اتصال")
    t0 = time.monotonic()
    ok = await collector.connect()
    elapsed = (time.monotonic() - t0) * 1000
    report.add("D1.connect", "ok" if ok else "fail", f"ok={ok} | {elapsed:.0f}ms")
    if not ok:
        print(f"\n{C.FAIL}❌ اتصال نشد{C.END}")
        await safe_disconnect(collector, report)
        await pipeline.stop()
        report.print_summary(); report.save()
        return False

    section(f"E) fetch (since={since_days})")
    msgs = []
    fetch_elapsed = 0.0
    try:
        t0 = time.monotonic()
        if since_days == 0:
            msgs = await collector.fetch_messages(force_no_since=True)
        else:
            since_dt = datetime.now() - timedelta(days=since_days)
            msgs = await collector.fetch_messages(since=since_dt)
        fetch_elapsed = time.monotonic() - t0
        report.add("E1.fetch", "ok",
            f"count={len(msgs)} | {fetch_elapsed:.0f}s",
            fetch_elapsed * 1000)
    except Exception as e:
        report.add("E1.fetch", "fail", f"{type(e).__name__}: {e}")

    report.add("E2.count", "ok" if msgs else "warn", f"{len(msgs)} پیام")

    stats = getattr(collector, "_last_scrape_stats", {})
    if stats:
        report.add("E3.stats", "ok",
            f"total={stats.get('total_chats',0)} | "
            f"unique={stats.get('unique_chats',0)} | "
            f"processed={stats.get('processed',0)} | "
            f"system={stats.get('skipped_system',0)} | "
            f"failed={stats.get('failed',0)} | "
            f"found={stats.get('messages_found',0)} | "
            f"old={stats.get('messages_skipped_old',0)} | "
            f"kw={stats.get('messages_skipped_kw',0)}")

    section("F) ساختار پیام")
    if msgs:
        missing = [f for f in REQUIRED_MSG_FIELDS if f not in msgs[0]]
        report.add("F1.fields", "ok" if not missing else "fail", f"missing={missing}")

        ids = [str(m.get("id", "")) for m in msgs]
        dup = len(ids) - len(set(ids))
        report.add("F2.ids", "ok" if dup == 0 else "fail", f"{dup} تکراری از {len(ids)}")

        bad_ct = [m for m in msgs if m.get("chat_type") not in VALID_CHAT_TYPES]
        report.add("F3.chat_type", "ok" if not bad_ct else "warn", f"{len(bad_ct)} نامعتبر")

        empty_text = [m for m in msgs if not (m.get("text") or "").strip()]
        report.add("F4.text", "ok" if not empty_text else "fail", f"{len(empty_text)} خالی")

        empty_name = [m for m in msgs if not (m.get("chat_name") or "").strip()]
        report.add("F5.name", "ok" if not empty_name else "fail", f"{len(empty_name)} خالی")

        ts_ok = all(isinstance(m.get("timestamp"), datetime) for m in msgs)
        report.add("F6.timestamp", "ok" if ts_ok else "warn", f"datetime: {ts_ok}")

        print(f"\n  📩 {min(5, len(msgs))} نمونه:")
        for m in msgs[:5]:
            txt = (m.get("text") or "")[:50].replace("\n", " ")
            print(f"     [{m.get('chat_name','?')[:25]}] {txt}")

        try:
            from analyzers.keyword_finder import get_keyword_finder
            kw = get_keyword_finder()
            with_kw = [m for m in msgs if kw.has_match(m.get("text") or "")]
            report.add("F7.keywords", "ok", f"{len(with_kw)}/{len(msgs)} کلیدواژه‌دار")
        except Exception as e:
            report.add("F7.keywords", "warn", str(e))

    section("G) فیلتر سیستمی")
    if msgs:
        sys_msgs = [m for m in msgs if _is_system_chat(m.get("chat_name", ""))]
        report.add("G1.system",
            "ok" if not sys_msgs else "fail",
            f"{len(sys_msgs)} پیام از چت سیستمی")
    else:
        report.add("G1.system", "skip", "پیامی نیست")

    section("H) Pipeline → DB")
    stored = 0
    try:
        stored = await pipeline.feed(msgs)
        report.add("H1.feed", "ok", f"fed={len(msgs)} | stored={stored}")
    except Exception as e:
        report.add("H1.feed", "fail", f"{type(e).__name__}: {e}")

    try:
        total_db = await pipeline.count_messages(platform="telegram")
        report.add("H2.total", "ok", f"telegram: {total_db} پیام در DB")
    except Exception as e:
        report.add("H2.total", "warn", str(e))

    total_leads = 0
    try:
        total_leads = await pipeline.count_leads(platform="telegram")
        report.add("H3.leads", "ok", f"telegram: {total_leads} سرنخ")
    except Exception as e:
        report.add("H3.leads", "warn", str(e))

    section("I) گزارش نهایی")
    if total_leads > 0:
        try:
            leads = await pipeline.find_leads_in_db("telegram")
            print(f"\n  🎯 {min(3, len(leads))} lead نمونه:")
            for ld in leads[:3]:
                print(f"     [{ld.get('chat_name','?')[:25]}] kw={(ld.get('keywords') or '—')[:40]}")
            report.add("I1.sample", "ok", "نمایش داده شد")
        except Exception as e:
            report.add("I1.sample", "warn", str(e))
    else:
        report.add("I1.sample", "skip", "سرنخی نیست")

    section("J) پاک‌سازی")
    try:
        await collector.dump_dom()
        report.add("J1.dom", "ok", "DOM ذخیره شد")
    except Exception as e:
        report.add("J1.dom", "warn", str(e))

    await safe_disconnect(collector, report)
    try:
        await pipeline.stop()
        report.add("J2.cleanup", "ok", "DB پاک شد")
    except Exception as e:
        report.add("J2.cleanup", "warn", str(e))

    ok = report.print_summary()
    report.save()
    return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-chats", type=int, default=0)
    parser.add_argument("--since-days", type=int, default=7)
    args = parser.parse_args()
    ok = asyncio.run(main(
        headless=args.headless,
        max_chats=args.max_chats,
        since_days=args.since_days,
    ))
    sys.exit(0 if ok else 1)