# tests/test_rubika_full.py
"""
تست کامل روبیکا — Pipeline end-to-end.

Pipeline:
  Collector → EventBus → StorageHandler → DB (isolated)

نسخهٔ ۴.۰ (این نسخه):
  - 🎯 یکپارچگی کامل با run_all_tests.py (args استاندارد).
  - 🎯 افزودن --headless و --max-chats (سازگاری با بقیه تست‌ها).
  - 🎯 استفاده از keyword args در __main__ (خوانایی).
  - 🎯 انتقال headless و max_chats به RubikaCollector.
  - ✅ حفظ همهٔ منطق نسخهٔ ۳.۱ (skip منطقی + Saved Messages).

اجرا:
  python tests/test_rubika_full.py
  python tests/test_rubika_full.py --headless
  python tests/test_rubika_full.py --max-chats 10
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from tests.helpers import (
    TestReport, banner, check_env, check_session_exists,
    safe_disconnect, section, C,
)
from tests.pipeline_helpers import TestPipeline


# ═════════════════════════════════════════════════════════
# main
# ═════════════════════════════════════════════════════════
async def main(
    headless: bool = False,
    max_chats: int = 0,
):
    banner("تست کامل روبیکا (Rubika) — Pipeline end-to-end")
    report = TestReport("rubika")

    # ═══ ۱) env ═══
    section("مرحله ۱: env و config")
    env_info = check_env("rubika")

    report.add(
        "env.enabled",
        "ok" if env_info["enabled"] else "skip",
        f"RUBIKA_ENABLED={env_info['enabled']}",
    )
    if not env_info["enabled"]:
        print(
            f"{C.INFO}⏭️ روبیکا غیرفعال (skip) — "
            f"برای اجرا: RUBIKA_ENABLED=true{C.END}"
        )
        report.print_summary()
        report.save()
        return True

    report.add(
        "env.phone",
        "ok" if env_info["phone"] else "fail",
        f"RUBIKA_PHONE={env_info['phone'][:6]}...",
    )

    # ═══ ۲) rubpy ═══
    section("مرحله ۲: بررسی rubpy")
    try:
        import rubpy
        report.add(
            "rubpy.import", "ok",
            f"version={getattr(rubpy, '__version__', '?')}",
        )
    except ImportError as e:
        report.add("rubpy.import", "fail", str(e))
        report.print_summary()
        report.save()
        return False

    # ═══ ۳) نشست ═══
    section("مرحله ۳: بررسی نشست")
    sp = env_info["session_path"]
    exists = (
        os.path.exists(sp)
        or os.path.exists(sp + ".session")
        or os.path.isdir(sp)
    )
    report.add(
        "session.exists",
        "ok" if exists else "warn",
        f"exists={exists}",
    )

    # ═══ ۴) pipeline ═══
    section("مرحله ۴: ساخت pipeline")
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

    # ═══ ۵) collector ═══
    section("مرحله ۵: ساخت collector")
    try:
        from collectors.rubika import RubikaCollector

        # ─── انتقال args به collector (سازگار با kwargs) ───
        collector_kwargs = {
            "poll_seconds": 999,
        }
        # headless و max_chats اگه collector قبول کرد
        try:
            collector = RubikaCollector(
                headless=headless,
                max_chats=max_chats,
                **collector_kwargs,
            )
        except TypeError:
            # fallback: collector این args رو نمی‌پذیره
            collector = RubikaCollector(**collector_kwargs)

        report.add(
            "collector.init", "ok",
            f"platform={collector.platform_name}",
        )
    except Exception as e:
        report.add("collector.init", "fail", str(e))
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    # ═══ ۶) connect ═══
    section("مرحله ۶: connect")
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
            print(
                f"\n{C.WARN}⚠️ روبیکا وصل نشد — "
                f"`python login.py` گزینهٔ ۱{C.END}"
            )
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

    # ═══ ۷) صبر برای رویدادهای زنده ═══
    section("مرحله ۷: صبر ۵ ثانیه")
    await asyncio.sleep(5)
    report.add("wait.live", "ok", "5s")

    # ═══ ۸) fetch_messages ═══
    section("مرحله ۸: fetch_messages")
    try:
        t0 = time.monotonic()
        msgs = await collector.fetch_messages()
        elapsed = (time.monotonic() - t0) * 1000
        report.add(
            "fetch_messages", "ok",
            f"count={len(msgs)} | {elapsed:.0f}ms",
            elapsed,
        )
    except Exception as e:
        report.add("fetch_messages", "fail", str(e))
        msgs = []

    # ═══ ۹) ساختار ═══
    section("مرحله ۹: بررسی ساختار پیام‌ها")
    if msgs:
        sample = msgs[0]
        required = ["id", "platform", "text", "chat_name", "timestamp"]
        missing = [k for k in required if k not in sample]
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

        chats = {m.get("chat_name") for m in msgs}
        report.add(
            "msg.stats", "ok",
            f"chats={len(chats)}",
        )
    else:
        report.add("msg.structure", "warn", "هیچ پیامی نیامد")

    # ═══ ۱۰) pipeline.feed ═══
    section("مرحله ۱۰: عبور از pipeline")
    try:
        t0 = time.monotonic()
        stored = await pipeline.feed(msgs)
        elapsed = (time.monotonic() - t0) * 1000
        report.add(
            "pipeline.feed", "ok",
            f"fed={len(msgs)} | stored={stored} | {elapsed:.0f}ms",
            elapsed,
        )
    except Exception as e:
        report.add("pipeline.feed", "fail", str(e))

    # ═══ ۱۱) چک DB ═══
    section("مرحله ۱۱: چک ذخیره‌سازی در DB")
    if msgs:
        from analyzers.keyword_finder import get_keyword_finder
        kw = get_keyword_finder()
        msgs_with_kw = [
            m for m in msgs
            if kw.has_match(m.get("text") or "")
        ]

        report.add(
            "db.scrape_stats", "ok",
            f"{len(msgs)} scrape شد، "
            f"{len(msgs_with_kw)} کلیدواژه‌دار",
        )

        if msgs_with_kw:
            found = await pipeline.find_in_db(
                msgs_with_kw, platform="rubika"
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
                    f"{in_db}/{total} کلیدواژه‌دار",
                )
            else:
                report.add(
                    "db.scrape_saved", "fail",
                    f"0/{total} کلیدواژه‌دار در DB!",
                )

            if found:
                print(f"\n  📥 نمونهٔ leadهای ذخیره‌شده:")
                for rid, row in list(found.items())[:5]:
                    print(
                        f"     [{row.get('chat_name', '?')[:25]}] "
                        f"{(row.get('text') or '')[:50]}"
                    )
                    print(
                        f"        db_id={row.get('id', '')[:30]} | "
                        f"kw={row.get('keywords', '—')[:40]}"
                    )
        else:
            report.add(
                "db.scrape_saved", "skip",
                f"هیچ‌کدام از {len(msgs)} پیام کلیدواژه ندارند",
            )
    else:
        report.add(
            "db.scrape_saved", "skip",
            "هیچ پیامی برای چک نیست",
        )

    # ═══ ۱۲) آمار DB ═══
    section("مرحله ۱۲: آمار DB")
    try:
        total_db = await pipeline.count_messages(platform="rubika")
        total_leads = await pipeline.count_leads(platform="rubika")
        report.add(
            "db.total_messages", "ok",
            f"rubika: {total_db} پیام در DB تست",
        )
        report.add(
            "db.total_leads", "ok",
            f"rubika: {total_leads} سرنخ در DB تست",
        )
    except Exception as e:
        report.add("db.leads_check", "warn", str(e))

    # ═══ ۱۳) disconnect ═══
    section("مرحله ۱۳: disconnect")
    await safe_disconnect(collector, report)

    # ═══ ۱۴) cleanup ═══
    section("مرحله ۱۴: پاک‌سازی pipeline")
    try:
        await pipeline.stop()
        report.add("pipeline.cleanup", "ok", "DB تست پاک شد")
    except Exception as e:
        report.add("pipeline.cleanup", "warn", str(e))

    # ═══ نتیجه ═══
    ok = report.print_summary()
    report.save()
    return ok


# ═════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="تست کامل روبیکا — Pipeline end-to-end",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="اجرای مرورگر در حالت headless (بدون پنجره)",
    )
    parser.add_argument(
        "--max-chats", type=int, default=0,
        help="حداکثر تعداد چت برای scrape (0 = بدون محدودیت)",
    )
    args = parser.parse_args()

    ok = asyncio.run(
        main(
            headless=args.headless,
            max_chats=args.max_chats,
        )
    )
    sys.exit(0 if ok else 1)