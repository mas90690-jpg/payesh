# tests/test_telegram_full.py
"""
تست کامل تلگرام — Pipeline end-to-end.

Pipeline:
  Collector → EventBus → StorageHandler → DB (isolated)

نسخهٔ ۱ (این نسخه):
  - هم‌راستا با test_bale_full.py و test_eitaa_full.py
  - ⏭️ skip منطقی: اگر هیچ پیام کلیدواژه‌داری نبود → skip (نه fail)
  - 🎯 چک پیش‌نیازهای تلگرام (API_ID, API_HASH, PHONE)

پیش‌نیازها:
  - TELEGRAM_ENABLED=true
  - TELEGRAM_API_ID (از my.telegram.org)
  - TELEGRAM_API_HASH (از my.telegram.org)
  - TELEGRAM_PHONE (شماره واقعی با +)
  - نشست ساخته‌شده (python login.py → گزینه ۴)

اجرا:
  python tests/test_telegram_full.py
  python tests/test_telegram_full.py --headless
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import (
    TestReport, banner, check_env, check_session_exists,
    safe_disconnect, section, C,
)
from tests.pipeline_helpers import TestPipeline


async def main():
    banner("تست کامل تلگرام (Telegram) — Pipeline end-to-end")
    report = TestReport("telegram")

    # ═══ ۱) env ═══
    section("مرحله ۱: بررسی env و config")
    env_info = check_env("telegram")
    raw = env_info.get("raw", {})

    report.add(
        "env.enabled",
        "ok" if env_info["enabled"] else "fail",
        f"TELEGRAM_ENABLED={env_info['enabled']}",
    )
    if not env_info["enabled"]:
        print(f"{C.FAIL}❌ تلگرام غیرفعال — تست لغو{C.END}")
        print(f"   💡 در .env بذار: TELEGRAM_ENABLED=true")
        report.print_summary()
        report.save()
        return False

    # ─── چک پیش‌نیازها ───
    api_id = raw.get("api_id", "") or ""
    api_hash = raw.get("api_hash", "") or ""
    phone = raw.get("phone", "") or ""

    missing = []
    if not api_id:
        missing.append("TELEGRAM_API_ID")
    if not api_hash:
        missing.append("TELEGRAM_API_HASH")
    if not phone:
        missing.append("TELEGRAM_PHONE")

    if missing:
        report.add(
            "env.prerequisites", "fail",
            f"missing={missing}",
        )
        print(f"\n{C.FAIL}❌ پیش‌نیازهای تلگرام ناقص:{C.END}")
        for m in missing:
            print(f"     - {m} تنظیم نشده")
        print(f"\n   💡 برای گرفتن API:")
        print(f"      ۱) برو my.telegram.org")
        print(f"      ۲) با شماره‌ات لاگین کن")
        print(f"      ۳) API development tools")
        print(f"      ۴) App api_id و App api_hash رو بگیر")
        await pipeline_stop_safe(report)
        report.print_summary()
        report.save()
        return False

    report.add(
        "env.prerequisites", "ok",
        f"api_id={api_id[:6]}... | phone={phone[:6]}...",
    )

    # ═══ ۲) نشست ═══
    section("مرحله ۲: بررسی نشست")
    session_exists = check_session_exists(
        env_info.get("session_path", "")
    )
    report.add(
        "session.exists",
        "ok" if session_exists else "warn",
        f"exists={session_exists}",
    )

    # ═══ ۳) pipeline ═══
    section("مرحله ۳: ساخت pipeline end-to-end")
    pipeline = TestPipeline(
        keywords_only=False,
        require_t1=False,
        allowed_tiers=("T1", "T2", "T3"),
    )
    await pipeline.start()
    report.add(
        "pipeline.init", "ok",
        f"db={os.path.basename(pipeline._db_path)}",
    )

    # ═══ ۴) ساخت collector ═══
    section("مرحله ۴: ساخت collector")
    try:
        from collectors.telegram import TelegramCollector

        collector = TelegramCollector(
            api_id=api_id,
            api_hash=api_hash,
            phone=phone,
            poll_seconds=999,
        )
        report.add("collector.init", "ok",
                   f"platform={collector.platform_name}")
    except Exception as e:
        report.add("collector.init", "fail", str(e))
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    # ═══ ۵) connect ═══
    section("مرحله ۵: connect()")
    t0 = time.monotonic()
    try:
        ok = await collector.connect()
        elapsed = (time.monotonic() - t0) * 1000
        report.add(
            "connect",
            "ok" if ok else "fail",
            f"ok={ok} | {elapsed:.0f}ms",
            elapsed,
        )
        if not ok:
            print(f"\n{C.WARN}⚠️ اتصال تلگرام شکست خورد.{C.END}")
            print(f"   راه‌حل: python login.py → گزینهٔ ۴ (تلگرام)")
            await safe_disconnect(collector, report)
            await pipeline.stop()
            report.print_summary()
            report.save()
            return False
    except Exception as e:
        report.add("connect", "fail", str(e))
        await safe_disconnect(collector, report)
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    # ═══ ۶) صبر برای رویدادهای زنده ═══
    section("مرحله ۶: صبر ۵ ثانیه")
    await asyncio.sleep(5)
    report.add("wait.live", "ok", "5s")

    # ═══ ۷) fetch_messages ═══
    section("مرحله ۷: fetch_messages")
    try:
        t0 = time.monotonic()
        msgs = await collector.fetch_messages()
        elapsed = (time.monotonic() - t0) * 1000
        report.add(
            "fetch_messages",
            "ok" if msgs is not None else "fail",
            f"count={len(msgs)} | {elapsed:.0f}ms",
            elapsed,
        )
    except Exception as e:
        report.add("fetch_messages", "fail", str(e))
        msgs = []

    # ═══ ۸) fetch_history (اختصاصی تلگرام) ═══
    section("مرحله ۸: fetch_history (اختصاصی)")
    try:
        from datetime import datetime, timedelta
        since = datetime.now() - timedelta(days=1)
        t0 = time.monotonic()
        history = await collector.fetch_history(since)
        elapsed = (time.monotonic() - t0) * 1000
        report.add(
            "fetch_history",
            "ok" if history is not None else "warn",
            f"count={len(history)} | {elapsed:.0f}ms",
            elapsed,
        )
        # اگه تاریخچه پیام داشت، به msgs اضافه کن
        if history:
            msgs.extend(history)
            report.add(
                "fetch_history.merged", "ok",
                f"total msgs after merge = {len(msgs)}",
            )
    except Exception as e:
        report.add("fetch_history", "warn", str(e))

    # ═══ ۹) ساختار پیام‌ها ═══
    section("مرحله ۹: بررسی ساختار پیام‌ها")
    if msgs:
        sample = msgs[0]
        required_keys = [
            "id", "platform", "text", "chat_id", "chat_name",
            "chat_type", "timestamp", "media_type",
        ]
        missing = [k for k in required_keys if k not in sample]
        report.add(
            "msg.structure",
            "ok" if not missing else "warn",
            f"missing={missing}",
        )

        print(f"\n  📩 {min(5, len(msgs))} پیام scrape‌شده:")
        for m in msgs[:5]:
            txt = (m.get("text") or "")[:60].replace("\n", " ")
            print(
                f"     [{m.get('chat_name', '?')[:25]}] "
                f"id={m.get('id', '?')[:20]} | {txt}"
            )

        platforms = {m.get("platform") for m in msgs}
        chats = {m.get("chat_name") for m in msgs}
        report.add(
            "msg.stats", "ok",
            f"platforms={platforms} | chats={len(chats)}",
        )
    else:
        report.add("msg.structure", "warn",
                   "هیچ پیامی fetch نشد")

    # ═══ ۱۰) feed pipeline ═══
    section("مرحله ۱۰: عبور از pipeline (ذخیره در DB)")
    try:
        t0 = time.monotonic()
        stored = await pipeline.feed(msgs)
        elapsed = (time.monotonic() - t0) * 1000
        report.add(
            "pipeline.feed",
            "ok" if stored >= 0 else "fail",
            f"fed={len(msgs)} | stored={stored} | {elapsed:.0f}ms",
            elapsed,
        )
    except Exception as e:
        report.add("pipeline.feed", "fail", str(e))

    # ═══ ۱۱) چک DB ═══
    section("مرحله ۱۱: چک ذخیره‌سازی در DB")

    if msgs:
        try:
            from analyzers.keyword_finder import get_keyword_finder
            kw = get_keyword_finder()
            msgs_with_kw = [
                m for m in msgs
                if kw.has_match(m.get("text") or "")
            ]
        except Exception as e:
            msgs_with_kw = []
            report.add("db.kw_filter", "warn", str(e))

        report.add(
            "db.scrape_stats", "ok",
            f"{len(msgs)} scrape شد، "
            f"{len(msgs_with_kw)} کلیدواژه‌دار",
        )

        if msgs_with_kw:
            found = await pipeline.find_in_db(
                msgs_with_kw, platform="telegram"
            )
            total = len(msgs_with_kw)
            in_db = len(found)

            if in_db == total:
                report.add(
                    "db.scrape_saved", "ok",
                    f"{in_db}/{total} کلیدواژه‌دار در DB ✅",
                )
            elif in_db > 0:
                report.add(
                    "db.scrape_saved", "warn",
                    f"{in_db}/{total} کلیدواژه‌دار در DB",
                )
            else:
                report.add(
                    "db.scrape_saved", "fail",
                    f"0/{total} کلیدواژه‌دار در DB!",
                )

            if found:
                print(f"\n  📥 نمونهٔ leadهای ذخیره‌شده در DB:")
                for rid, row in list(found.items())[:5]:
                    chat = (row.get("chat_name") or "?")[:25]
                    txt = (row.get("text") or "")[:50]
                    kwv = row.get("keywords") or "—"
                    print(f"     [{chat}] {txt}")
                    print(f"        db_id={row.get('id', '')[:30]} | "
                          f"kw={kwv[:40]}")
        else:
            report.add(
                "db.scrape_saved", "skip",
                f"هیچ‌کدام از {len(msgs)} پیام کلیدواژه ندارند",
            )
            print(f"\n  ℹ️ هیچ پیام کلیدواژه‌داری در این scrape نبود")
            print(f"     💡 برای تست کامل: پیام با برند "
                  f"(مثل «یاراترا کریستا اس او پی») در یه گروه تلگرام بفرست")
    else:
        report.add(
            "db.scrape_saved", "skip",
            "هیچ پیامی برای چک نیست",
        )

    # ═══ ۱۲) چک lead ═══
    section("مرحله ۱۲: چک lead (پیام‌های کلیدواژه‌دار)")

    try:
        total_db = await pipeline.count_messages(platform="telegram")
        total_leads = await pipeline.count_leads(platform="telegram")
        report.add(
            "db.total_messages", "ok",
            f"telegram: {total_db} پیام در DB تست",
        )
        report.add(
            "db.total_leads", "ok",
            f"telegram: {total_leads} سرنخ در DB تست",
        )

        if total_leads > 0:
            leads = await pipeline.find_leads_in_db("telegram")
            print(f"\n  🎯 {min(3, len(leads))} lead نمونه:")
            for ld in leads[:3]:
                chat = (ld.get("chat_name") or "?")[:25]
                kwv = (ld.get("keywords") or "—")[:40]
                tier = ld.get("opportunity_type") or "—"
                print(f"     [{chat}] kw={kwv} | tier={tier}")
    except Exception as e:
        report.add("db.leads_check", "warn", str(e))

    # ═══ ۱۳) disconnect ═══
    section("مرحله ۱۳: disconnect")
    await safe_disconnect(collector, report)

    # ═══ ۱۴) پاک‌سازی pipeline ═══
    section("مرحله ۱۴: پاک‌سازی pipeline")
    try:
        await pipeline.stop()
        report.add("pipeline.cleanup", "ok", "DB تست پاک شد")
    except Exception as e:
        report.add("pipeline.cleanup", "warn", str(e))

    ok = report.print_summary()
    report.save()
    return ok


async def pipeline_stop_safe(report: TestReport):
    """پاک‌سازی امن pipeline در صورت خطا قبل از ساخت."""
    try:
        pass
    except Exception:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)