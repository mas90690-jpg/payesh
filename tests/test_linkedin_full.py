# tests/test_linkedin_full.py
"""
تست کامل لینکدین — Pipeline end-to-end.

اجرا:
  python tests/test_linkedin_full.py
  python tests/test_linkedin_full.py --headless
"""
from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import (
    TestReport, banner, check_env, check_session_exists,
    safe_disconnect, section, C,
)
from tests.pipeline_helpers import TestPipeline


async def main(headless: bool = False):
    banner("تست کامل لینکدین (LinkedIn) — Pipeline end-to-end")
    report = TestReport("linkedin")

    # ═══ ۱) DNS ═══
    section("مرحله ۱: DNS")
    try:
        ip = socket.gethostbyname("www.linkedin.com")
        report.add("dns.resolve", "ok", f"→ {ip}")
    except Exception as e:
        report.add("dns.resolve", "fail",
                   f"{type(e).__name__}: {e}")
        report.print_summary()
        report.save()
        return False

    # ═══ ۲) HTTP ═══
    section("مرحله ۲: HTTP")
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://www.linkedin.com/",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            report.add("http.connect", "ok",
                       f"status={resp.status}")
    except Exception as e:
        report.add("http.connect", "warn", str(e))

    # ═══ ۳) env ═══
    section("مرحله ۳: env و config")
    env_info = check_env("linkedin")

    report.add("env.enabled",
               "ok" if env_info["enabled"] else "fail",
               f"LINKEDIN_ENABLED={env_info['enabled']}")
    if not env_info["enabled"]:
        print(f"{C.FAIL}❌ لینکدین غیرفعال{C.END}")
        report.print_summary()
        report.save()
        return False

    # ═══ ۴) نشست ═══
    section("مرحله ۴: بررسی نشست")
    exists = check_session_exists(env_info["session_path"])
    report.add("session.exists",
               "ok" if exists else "warn",
               f"exists={exists}")

    # ═══ ۵) pipeline ═══
    section("مرحله ۵: ساخت pipeline")
    pipeline = TestPipeline(
        keywords_only=False, require_t1=False,
        allowed_tiers=("T1", "T2", "T3"),
    )
    await pipeline.start()
    report.add("pipeline.init", "ok",
               f"db={os.path.basename(pipeline._db_path)}")

    # ═══ ۶) collector ═══
    section("مرحله ۶: ساخت collector")
    try:
        from collectors.linkedin import LinkedInCollector
        collector = LinkedInCollector(
            headless=headless, poll_seconds=999,
            max_results=5, scrolls=1,
            monitor_feed=True, max_feed_posts=5,
        )
        report.add("collector.init", "ok",
                   f"platform={collector.platform_name}")
    except Exception as e:
        report.add("collector.init", "fail", str(e))
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    # ═══ ۷) connect ═══
    section("مرحله ۷: connect")
    t0 = time.monotonic()
    try:
        ok = await collector.connect()
        elapsed = (time.monotonic() - t0) * 1000
        report.add("connect",
                   "ok" if ok else "fail",
                   f"ok={ok} | {elapsed:.0f}ms", elapsed)
        if not ok:
            print(f"\n{C.WARN}⚠️ لینکدین وصل نشد — "
                  f"`python login.py` گزینهٔ ۶{C.END}")
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

    # ═══ ۸) چک صفحه ═══
    section("مرحله ۸: چک صفحه")
    try:
        url = collector._page.url or ""
        title = await collector._page.title()
        report.add("page.url", "ok", f"{url[:80]}")
        report.add("page.title", "ok", f"{title[:80]}")
        logged = await collector._is_logged_in()
        report.add("is_logged_in",
                   "ok" if logged else "fail",
                   f"logged={logged}")
    except Exception as e:
        report.add("page.check", "warn", str(e))

    # ═══ ۹) fetch_messages ═══
    section("مرحله ۹: fetch_messages (فید + جستجو)")
    try:
        t0 = time.monotonic()
        msgs = await collector.fetch_messages()
        elapsed = (time.monotonic() - t0) * 1000
        report.add("fetch_messages", "ok",
                   f"count={len(msgs)} | {elapsed:.0f}ms", elapsed)
    except Exception as e:
        report.add("fetch_messages", "fail", str(e))
        msgs = []

    # ═══ ۱۰) ساختار ═══
    section("مرحله ۱۰: بررسی ساختار پیام‌ها")
    if msgs:
        print(f"\n  📩 {min(3, len(msgs))} پست scrape‌شده:")
        for m in msgs[:3]:
            txt = (m.get("text") or "")[:80].replace("\n", " ")
            print(f"     [{m.get('sender_name', '?')[:20]}] "
                  f"id={m.get('id', '?')[:20]} | {txt}")

        platforms = {m.get("platform") for m in msgs}
        report.add("msg.stats", "ok",
                   f"platforms={platforms}")
    else:
        report.add("msg.structure", "warn", "هیچ پستی نیامد")

    # ═══ ۱۱) pipeline.feed ═══
    section("مرحله ۱۱: عبور از pipeline")
    try:
        t0 = time.monotonic()
        stored = await pipeline.feed(msgs)
        elapsed = (time.monotonic() - t0) * 1000
        report.add("pipeline.feed", "ok",
                   f"fed={len(msgs)} | stored={stored} | "
                   f"{elapsed:.0f}ms", elapsed)
    except Exception as e:
        report.add("pipeline.feed", "fail", str(e))

    # ═══ ۱۲) چک DB ═══
    section("مرحله ۱۲: چک ذخیره‌سازی در DB")
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
                msgs_with_kw, platform="linkedin"
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

            if found:
                print(f"\n  📥 نمونهٔ leadهای ذخیره‌شده:")
                for rid, row in list(found.items())[:5]:
                    print(f"     [{row.get('sender_name', '?')[:25]}] "
                          f"{(row.get('text') or '')[:50]}")
                    print(f"        db_id={row.get('id', '')[:30]} | "
                          f"kw={row.get('keywords', '—')[:40]}")
        else:
            report.add("db.scrape_saved", "skip",
                       f"هیچ‌کدام از {len(msgs)} پست کلیدواژه ندارند")
    else:
        report.add("db.scrape_saved", "skip", "هیچ پستی برای چک نیست")

    # ═══ ۱۳) آمار DB ═══
    section("مرحله ۱۳: آمار DB")
    try:
        total_db = await pipeline.count_messages(platform="linkedin")
        total_leads = await pipeline.count_leads(platform="linkedin")
        report.add("db.total_messages", "ok",
                   f"linkedin: {total_db} پست در DB تست")
        report.add("db.total_leads", "ok",
                   f"linkedin: {total_leads} سرنخ در DB تست")
    except Exception as e:
        report.add("db.leads_check", "warn", str(e))

    # ═══ ۱۴) disconnect ═══
    section("مرحله ۱۴: disconnect")
    await safe_disconnect(collector, report)

    # ═══ ۱۵) cleanup ═══
    section("مرحله ۱۵: پاک‌سازی pipeline")
    try:
        await pipeline.stop()
        report.add("pipeline.cleanup", "ok", "DB تست پاک شد")
    except Exception as e:
        report.add("pipeline.cleanup", "warn", str(e))

    ok = report.print_summary()
    report.save()
    return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    ok = asyncio.run(main(args.headless))
    sys.exit(0 if ok else 1)