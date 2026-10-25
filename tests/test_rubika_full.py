# tests/test_rubika_full.py
"""
تست کامل و فوق‌دقیق روبیکا (Rubika) — نسخهٔ ۶.۰ (Comprehensive)
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
    "پیام‌های ذخیره شده", "پیامهای ذخیره شده", "ذخیره‌شده",
    "ذخیره شده", "شما", "Saved Messages", "Drafts", "You",
    "روبیکا", "Rubika", "تیم روبیکا", "پشتیبانی روبیکا",
}

SYSTEM_PREFIXES = (
    "کانال ایجاد", "گروه ایجاد", "Channel created", "Group created",
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
    banner("تست دقیق روبیکا (Rubika) — نسخهٔ ۶.۰ (فوق‌العاده دقیق)")
    report = TestReport("rubika")

    # ═══════════════════════════════════════════════════
    # A) پیش‌نیازها
    # ═══════════════════════════════════════════════════
    section("A) پیش‌نیازها")

    try:
        import rubpy  # noqa: F401
        report.add(
            "A1.rubpy", "ok",
            f"version={getattr(rubpy, '__version__', '?')}",
        )
    except ImportError:
        report.add("A1.rubpy", "fail", "rubpy نصب نیست")
        report.print_summary()
        report.save()
        return False

    env_info = check_env("rubika")
    enabled = env_info["enabled"]
    report.add(
        "A2.env.enabled",
        "ok" if enabled else "skip",
        f"RUBIKA_ENABLED={enabled}",
    )
    if not enabled:
        print(f"\n{C.INFO}⏭️ روبیکا غیرفعال — تست متوقف شد{C.END}")
        report.print_summary()
        report.save()
        return True

    phone = env_info.get("phone", "")
    report.add(
        "A3.env.phone",
        "ok" if phone else "fail",
        f"phone={phone[:6]}..." if phone else "خالی",
    )

    session_path = env_info.get("session_path", "")
    report.add(
        "A4.env.session_path",
        "ok" if session_path else "fail",
        session_path or "خالی",
    )

    session_exists = check_session_exists(session_path) if session_path else False
    report.add(
        "A5.session.exists",
        "ok" if session_exists else "warn",
        f"exists={session_exists}",
    )

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
        report.add(
            "B1.pipeline.init", "ok",
            f"db={os.path.basename(pipeline._db_path or '?')}",
        )
        report.add("B2.pipeline.db", "ok", "DB تست ساخته شد")
    except Exception as e:
        report.add("B1.pipeline.init", "fail", f"{type(e).__name__}: {e}")
        report.print_summary()
        report.save()
        return False

    # ═══════════════════════════════════════════════════
    # C) ساخت کالکتور
    # ═══════════════════════════════════════════════════
    section("C) ساخت کالکتور")
    collector = None
    try:
        from collectors.rubika import RubikaCollector

        # 🎯 تنظیمات از .env
        history_limit = int(os.getenv("RUBIKA_HISTORY_LIMIT", "500"))
        max_chats_env = int(os.getenv("RUBIKA_MAX_CHATS", "0"))

        # اگر max_chats از CLI داده شده، اولویت داره
        actual_max_chats = max_chats if max_chats > 0 else max_chats_env

        collector = RubikaCollector(
            phone=phone or None,
            poll_seconds=999,
        )
        # تنظیم دستی (چون rubika collector args نداره)
        collector._max_chats = actual_max_chats

        report.add(
            "C1.collector.init", "ok",
            f"platform={collector.platform_name} | "
            f"history_limit={history_limit} | "
            f"max_chats={actual_max_chats}",
        )
    except Exception as e:
        report.add("C1.collector.init", "fail", f"{type(e).__name__}: {e}")
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    pname = getattr(collector, "platform_name", None)
    report.add(
        "C2.platform_name",
        "ok" if pname == "rubika" else "fail",
        f"platform_name={pname}",
    )

    poll = getattr(collector, "poll_interval_seconds", None)
    report.add(
        "C3.poll_interval",
        "ok" if isinstance(poll, (int, float)) else "warn",
        f"poll={poll}",
    )

    # ═══════════════════════════════════════════════════
    # D) اتصال
    # ═══════════════════════════════════════════════════
    section("D) اتصال")
    t0 = time.monotonic()
    connect_ok = False
    try:
        connect_ok = await collector.connect()
        elapsed = (time.monotonic() - t0) * 1000
        report.add(
            "D1.connect",
            "ok" if connect_ok else "fail",
            f"ok={connect_ok} | {elapsed:.0f}ms",
            elapsed,
        )
    except Exception as e:
        report.add("D1.connect", "fail", f"{type(e).__name__}: {e}")

    if not connect_ok:
        print(f"\n{C.FAIL}❌ اتصال روبیکا برقرار نشد{C.END}")
        print("   راه‌حل: python login.py → گزینهٔ ۱")
        await safe_disconnect(collector, report)
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    # ═══════════════════════════════════════════════════
    # E) صبر برای پیام‌های زنده
    # ═══════════════════════════════════════════════════
    section("E) صبر ۵ ثانیه برای رویدادهای زنده")
    await asyncio.sleep(5)
    report.add("E1.wait.live", "ok", "5s")

    # ═══════════════════════════════════════════════════
    # F) fetch_messages
    # ═══════════════════════════════════════════════════
    section(f"F) fetch_messages (since={since_days} روز)")
    since_dt = (
        datetime.now() - timedelta(days=since_days)
        if since_days > 0 else None
    )

    msgs: list = []
    fetch_elapsed = 0.0
    try:
        t0 = time.monotonic()
        msgs = await collector.fetch_messages(since=since_dt)
        fetch_elapsed = time.monotonic() - t0
        report.add(
            "F1.fetch",
            "ok" if isinstance(msgs, list) else "fail",
            f"count={len(msgs) if msgs else 0} | {fetch_elapsed:.0f}s",
            fetch_elapsed * 1000,
        )
    except Exception as e:
        report.add("F1.fetch", "fail", f"{type(e).__name__}: {e}")
        msgs = []

    if msgs:
        report.add("F2.fetch.count", "ok", f"{len(msgs)} پیام گرفته شد")
    else:
        report.add("F2.fetch.count", "warn", "هیچ پیامی گرفته نشد")

    if fetch_elapsed > MAX_FETCH_SECONDS:
        report.add(
            "F3.fetch.timeout", "warn",
            f"{fetch_elapsed:.0f}s > {MAX_FETCH_SECONDS}s",
        )
    else:
        report.add(
            "F3.fetch.timeout", "ok",
            f"{fetch_elapsed:.0f}s ≤ {MAX_FETCH_SECONDS}s",
        )

    # since honored
    if msgs and since_dt:
        too_old = [
            m for m in msgs
            if isinstance(m.get("timestamp"), datetime)
            and m["timestamp"] < since_dt
        ]
        if too_old:
            report.add(
                "F4.since.honored", "warn",
                f"{len(too_old)} پیام قدیمی‌تر از "
                f"{since_dt.strftime('%Y-%m-%d')}",
            )
        else:
            report.add(
                "F4.since.honored", "ok",
                f"همه {len(msgs)} پیام جدیدتر از since",
            )
    else:
        report.add("F4.since.honored", "skip", "پیامی نیست")

    # آمار تفکیکی
    stats = getattr(collector, "_last_scrape_stats", None)
    if isinstance(stats, dict):
        report.add(
            "F5.scrape.stats", "ok",
            f"total={stats.get('total_chats', '?')} | "
            f"processed={stats.get('processed', '?')} | "
            f"system={stats.get('skipped_system', '?')} | "
            f"failed={stats.get('failed', '?')} | "
            f"old={stats.get('messages_skipped_old', '?')} | "
            f"dup={stats.get('messages_skipped_dup', '?')}",
        )
    else:
        report.add(
            "F5.scrape.stats", "warn",
            "کالکتور _last_scrape_stats نمی‌دهد",
        )

    # ═══════════════════════════════════════════════════
    # G) ساختار پیام
    # ═══════════════════════════════════════════════════
    section("G) ساختار پیام")
    if not msgs:
        report.add("G.structure", "skip", "پیامی برای بررسی نیست")
    else:
        sample = msgs[0]

        # G1: فیلدها
        missing_fields = [
            f for f in REQUIRED_MSG_FIELDS if f not in sample
        ]
        report.add(
            "G1.msg.fields",
            "ok" if not missing_fields else "fail",
            f"missing={missing_fields}" if missing_fields
            else f"همه {len(REQUIRED_MSG_FIELDS)} فیلد موجود",
        )

        # G2: platform
        all_rubika = all(m.get("platform") == "rubika" for m in msgs)
        report.add(
            "G2.msg.platform",
            "ok" if all_rubika else "fail",
            f"همه پیام‌ها platform=rubika: {all_rubika}",
        )

        # G3: id یکتا
        ids = [str(m.get("id", "")) for m in msgs]
        unique_ids = set(ids)
        dup_count = len(ids) - len(unique_ids)
        report.add(
            "G3.msg.id_unique",
            "ok" if dup_count == 0 else "fail",
            f"{dup_count} id تکراری از {len(ids)}",
        )

        # G4: timestamp
        ts_ok = all(
            isinstance(m.get("timestamp"), datetime) for m in msgs
        )
        report.add(
            "G4.msg.timestamp",
            "ok" if ts_ok else "warn",
            f"همه datetime: {ts_ok}",
        )

        # G5: chat_type
        bad_ct = [
            m for m in msgs
            if m.get("chat_type") not in VALID_CHAT_TYPES
        ]
        report.add(
            "G5.msg.chat_type",
            "ok" if not bad_ct else "warn",
            f"{len(bad_ct)} chat_type نامعتبر" if bad_ct
            else "همه معتبر",
        )

        # G6: media_type
        bad_mt = [
            m for m in msgs
            if m.get("media_type") not in VALID_MEDIA_TYPES
        ]
        report.add(
            "G6.msg.media_type",
            "ok" if not bad_mt else "warn",
            f"{len(bad_mt)} media_type نامعتبر" if bad_mt
            else "همه معتبر",
        )

        # G7: text
        empty_text = [
            m for m in msgs
            if not _is_valid_text(m.get("text", ""))
        ]
        report.add(
            "G7.msg.text",
            "ok" if not empty_text else "fail",
            f"{len(empty_text)} پیام با متن خالی" if empty_text
            else "همه متن دارند",
        )

        # G8: chat_name
        empty_name = [
            m for m in msgs
            if not (m.get("chat_name") or "").strip()
        ]
        report.add(
            "G8.msg.chat_name",
            "ok" if not empty_name else "fail",
            f"{len(empty_name)} پیام بدون chat_name" if empty_name
            else "همه chat_name دارند",
        )

    # ═══════════════════════════════════════════════════
    # H) فیلتر چت سیستمی
    # ═══════════════════════════════════════════════════
    section("H) فیلتر چت سیستمی")
    if msgs:
        system_in_output = [
            m.get("chat_name", "")
            for m in msgs
            if _is_system_chat(m.get("chat_name", ""))
        ]
        unique_system = list(set(system_in_output))

        if unique_system:
            report.add(
                "H1.system.filter", "fail",
                f"{len(system_in_output)} پیام از "
                f"{len(unique_system)} چت سیستمی: "
                f"{unique_system[:5]}",
            )
        else:
            report.add(
                "H1.system.filter", "ok",
                "هیچ چت سیستمی در خروجی نیست",
            )

        # چت‌های واقعی
        real_chats = set()
        for m in msgs:
            cn = (m.get("chat_name") or "").strip()
            if cn and not _is_system_chat(cn):
                real_chats.add(cn)
        report.add(
            "H2.real_chats", "ok",
            f"{len(real_chats)} چت واقعی",
        )

        if real_chats:
            print(f"\n  📋 چت‌های واقعی ({len(real_chats)}):")
            for i, name in enumerate(sorted(real_chats)[:10], 1):
                print(f"     {i}. {name[:60]}")
            if len(real_chats) > 10:
                print(f"     ... و {len(real_chats) - 10} چت دیگر")
    else:
        report.add("H1.system.filter", "skip", "پیامی برای بررسی نیست")
        report.add("H2.real_chats", "skip", "پیامی نیست")

    # ═══════════════════════════════════════════════════
    # I) Pipeline → DB
    # ═══════════════════════════════════════════════════
    section("I) Pipeline → DB")
    stored = 0
    try:
        t0 = time.monotonic()
        stored = await pipeline.feed(msgs)
        elapsed = (time.monotonic() - t0) * 1000
        report.add(
            "I1.pipeline.feed",
            "ok" if stored >= 0 else "fail",
            f"fed={len(msgs)} | stored={stored} | {elapsed:.0f}ms",
            elapsed,
        )
    except Exception as e:
        report.add("I1.pipeline.feed", "fail", f"{type(e).__name__}: {e}")

    try:
        total_db = await pipeline.count_messages(platform="rubika")
        report.add(
            "I2.db.total", "ok",
            f"rubika: {total_db} پیام در DB",
        )
    except Exception as e:
        report.add("I2.db.total", "warn", f"{type(e).__name__}: {e}")
        total_db = 0

    try:
        total_leads = await pipeline.count_leads(platform="rubika")
        report.add(
            "I3.db.leads", "ok",
            f"rubika: {total_leads} سرنخ",
        )
    except Exception as e:
        report.add("I3.db.leads", "warn", f"{type(e).__name__}: {e}")
        total_leads = 0

    # verify content
    if msgs:
        try:
            found = await pipeline.find_in_db(
                msgs[:20], platform="rubika"
            )
            in_db = len(found)
            content_ok = 0
            content_bad = []
            for raw_id, row in found.items():
                original = next(
                    (m for m in msgs if str(m.get("id")) == raw_id),
                    None,
                )
                if not original:
                    continue
                orig_text = (original.get("text") or "").strip()
                db_text = (row.get("text") or "").strip()
                if orig_text[:100] != db_text[:100]:
                    content_bad.append(
                        f"id={raw_id[:20]} text mismatch"
                    )
                else:
                    content_ok += 1

            if not content_bad:
                report.add(
                    "I4.db.content", "ok",
                    f"{content_ok} پیام با محتوای درست",
                )
            else:
                report.add(
                    "I4.db.content", "warn",
                    f"{len(content_bad)} پیام ناهمخوان: "
                    f"{content_bad[:3]}",
                )
        except Exception as e:
            report.add("I4.db.content", "warn", f"{type(e).__name__}: {e}")

    # ═══════════════════════════════════════════════════
    # J) گزارش صریح
    # ═══════════════════════════════════════════════════
    section("J) گزارش صریح")
    if msgs and stored > 0 and total_leads == 0:
        report.add(
            "J1.lead.clarify", "info",
            f"{stored} پیام ذخیره شد ولی 0 کلیدواژه داشت",
        )
        print(f"\n  ℹ️ {stored} پیام در DB ذخیره شد ولی 0 کلیدواژه داشت")
    elif total_leads > 0:
        report.add(
            "J1.lead.clarify", "ok",
            f"{total_leads} Lead واقعی در DB",
        )
    else:
        report.add("J1.lead.clarify", "skip", "پیامی برای بررسی نبود")

    if total_leads > 0:
        try:
            leads = await pipeline.find_leads_in_db("rubika")
            print(f"\n  🎯 {min(3, len(leads))} lead نمونه:")
            for ld in leads[:3]:
                chat = (ld.get("chat_name") or "?")[:25]
                kw = (ld.get("keywords") or "—")[:40]
                print(f"     [{chat}] kw={kw}")
            report.add(
                "J2.lead.sample", "ok",
                f"{min(3, len(leads))} نمونه نمایش داده شد",
            )
        except Exception as e:
            report.add("J2.lead.sample", "warn", f"{type(e).__name__}: {e}")

    # ═══════════════════════════════════════════════════
    # K) پاک‌سازی
    # ═══════════════════════════════════════════════════
    section("K) پاک‌سازی")

    try:
        await safe_disconnect(collector, report)
    except Exception as e:
        report.add("K1.disconnect", "warn", f"{type(e).__name__}: {e}")

    try:
        await pipeline.stop()
        report.add("K2.pipeline.cleanup", "ok", "DB تست پاک شد")
    except Exception as e:
        report.add("K2.pipeline.cleanup", "warn", f"{type(e).__name__}: {e}")

    ok = report.print_summary()
    report.save()
    return ok


# ═══════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="تست دقیق روبیکا — Pipeline end-to-end (v6.0)",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="اجرای مرورگر بدون پنجره (برای روبیکا بی‌اثره)",
    )
    parser.add_argument(
        "--max-chats", type=int, default=0,
        help="حداکثر چت (0=همه)",
    )
    parser.add_argument(
        "--since-days", type=int, default=7,
        help="تاریخچه چند روز (0=بدون محدودیت)",
    )
    args = parser.parse_args()

    ok = asyncio.run(
        main(
            headless=args.headless,
            max_chats=args.max_chats,
            since_days=args.since_days,
        )
    )
    sys.exit(0 if ok else 1)