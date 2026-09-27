# tests/test_web_full.py
"""
تست کامل وب — Pipeline end-to-end.

اجرا:
  python tests/test_web_full.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import (
    TestReport, banner, check_env, safe_disconnect, section, C,
)
from tests.pipeline_helpers import TestPipeline


async def main():
    banner("تست کامل وب (Web) — Pipeline end-to-end")
    report = TestReport("web")

    # ═══ ۱) env ═══
    section("مرحله ۱: env و config")
    env_info = check_env("web")

    report.add("env.enabled",
               "ok" if env_info["enabled"] else "fail",
               f"WEB_ENABLED={env_info['enabled']}")
    if not env_info["enabled"]:
        print(f"{C.FAIL}❌ وب غیرفعال{C.END}")
        report.print_summary()
        report.save()
        return False

    # ═══ ۲) sources ═══
    section("مرحله ۲: بررسی فایل منابع")
    from config import BASE_DIR
    sources_file = os.path.join(
        BASE_DIR, "data", "sources", "web_sources.txt"
    )
    if os.path.exists(sources_file):
        with open(sources_file, encoding="utf-8") as f:
            lines = [
                l.strip() for l in f
                if l.strip() and not l.startswith("#")
            ]
        report.add("sources.file",
                   "ok" if lines else "warn",
                   f"lines={len(lines)}")
        for l in lines[:3]:
            print(f"     {l[:80]}")
    else:
        report.add("sources.file", "warn",
                   f"not found: {sources_file}")

    # ═══ ۳) pipeline ═══
    section("مرحله ۳: ساخت pipeline")
    pipeline = TestPipeline(
        keywords_only=False, require_t1=False,
        allowed_tiers=("T1", "T2", "T3"),
    )
    await pipeline.start()
    report.add("pipeline.init", "ok",
               f"db={os.path.basename(pipeline._db_path)}")

    # ═══ ۴) collector ═══
    section("مرحله ۴: ساخت collector")
    try:
        from collectors.web import WebCollector
        collector = WebCollector()
        report.add("collector.init", "ok",
                   f"platform={collector.platform_name}")
    except Exception as e:
        report.add("collector.init", "fail", str(e))
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    # ═══ ۵) connect ═══
    section("مرحله ۵: connect")
    try:
        ok = await collector.connect()
        report.add("connect",
                   "ok" if ok else "fail",
                   f"ok={ok} | sources={len(collector._sources)}")
        if not ok:
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

    # ═══ ۶) fetch_messages ═══
    section("مرحله ۶: fetch_messages")
    try:
        t0 = time.monotonic()
        msgs = await collector.fetch_messages()
        elapsed = (time.monotonic() - t0) * 1000
        report.add("fetch_messages", "ok",
                   f"count={len(msgs)} | {elapsed:.0f}ms", elapsed)
    except Exception as e:
        report.add("fetch_messages", "fail", str(e))
        msgs = []

    # ═══ ۷) ساختار ═══
    section("مرحله ۷: بررسی ساختار")
    if msgs:
        print(f"\n  📩 {min(3, len(msgs))} مطلب scrape‌شده:")
        for m in msgs[:3]:
            print(f"     [{m.get('chat_name', '?')[:40]}] "
                  f"id={m.get('id', '?')[:20]} | "
                  f"{(m.get('text') or '')[:50]}")
        report.add("msg.stats", "ok", f"count={len(msgs)}")
    else:
        report.add("msg.structure", "warn", "هیچ مطلبی نیامد")

    # ═══ ۸) pipeline.feed ═══
    section("مرحله ۸: عبور از pipeline")
    try:
        t0 = time.monotonic()
        stored = await pipeline.feed(msgs)
        elapsed = (time.monotonic() - t0) * 1000
        report.add("pipeline.feed", "ok",
                   f"fed={len(msgs)} | stored={stored} | "
                   f"{elapsed:.0f}ms", elapsed)
    except Exception as e:
        report.add("pipeline.feed", "fail", str(e))

    # ═══ ۹) چک DB ═══
    section("مرحله ۹: چک ذخیره‌سازی در DB")
    if msgs:
        from analyzers.keyword_finder import get_keyword_finder
        kw = get_keyword_finder()
        msgs_with_kw = [
            m for m in msgs
            if kw.has_match(m.get("text") or "")
        ]

        report.add("db.scrape_stats", "ok",
                   f"{len(msgs)} scrape شد، "
                   f"{len(msgs_with_kw)} کلیدواژه‌دار")

        if msgs_with_kw:
            found = await pipeline.find_in_db(
                msgs_with_kw, platform="web"
            )
            total = len(msgs_with_kw)
            in_db = len(found)

            if in_db == total:
                report.add("db.scrape_saved", "ok",
                           f"{in_db}/{total} کلیدواژه‌دار در DB ✅")
            elif in_db > 0:
                report.add("db.scrape_saved", "warn",
                           f"{in_db}/{total} کلیدواژه‌دار")
            else:
                report.add("db.scrape_saved", "fail",
                           f"0/{total} کلیدواژه‌دار در DB!")
        else:
            report.add("db.scrape_saved", "skip",
                       f"هیچ‌کدام از {len(msgs)} مطلب کلیدواژه ندارند")
    else:
        report.add("db.scrape_saved", "skip", "هیچ مطلبی برای چک نیست")

    # ═══ ۱۰) آمار DB ═══
    section("مرحله ۱۰: آمار DB")
    try:
        total_db = await pipeline.count_messages(platform="web")
        total_leads = await pipeline.count_leads(platform="web")
        report.add("db.total_messages", "ok",
                   f"web: {total_db} مطلب در DB تست")
        report.add("db.total_leads", "ok",
                   f"web: {total_leads} سرنخ در DB تست")
    except Exception as e:
        report.add("db.leads_check", "warn", str(e))

    # ═══ ۱۱) disconnect ═══
    section("مرحله ۱۱: disconnect")
    await safe_disconnect(collector, report)

    # ═══ ۱۲) cleanup ═══
    section("مرحله ۱۲: پاک‌سازی pipeline")
    try:
        await pipeline.stop()
        report.add("pipeline.cleanup", "ok", "DB تست پاک شد")
    except Exception as e:
        report.add("pipeline.cleanup", "warn", str(e))

    ok = report.print_summary()
    report.save()
    return ok


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)