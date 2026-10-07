# tests/test_instagram_full.py
"""
تست کامل اینستاگرام — Pipeline end-to-end (فقط فالوورها).

نسخهٔ ۱.۱ (این نسخه):
  - ⏭️ skip منطقی: اگر پلتفرم خاموش بود → skip (نه fail) ← v1.1
  - ✅ حفظ همهٔ مراحل نسخهٔ ۱.

پیش‌نیازها:
  - INSTAGRAM_ENABLED=true
  - نشست ذخیره‌شده در storage/sessions/instagram/
  - BROWSER_HEADLESS=false (مهم — Instagram headless رو تشخیص می‌ده)

اجرا:
  python tests/test_instagram_full.py
  python tests/test_instagram_full.py --headless
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import (
    TestReport, banner, check_env, safe_disconnect, section, C,
)
from tests.pipeline_helpers import TestPipeline


def _session_has_data(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    try:
        entries = [e for e in os.listdir(path) if not e.startswith(".")]
        return len(entries) > 0
    except Exception:
        return False


async def main(headless: bool = False, max_posts: int = 5):
    banner("تست کامل اینستاگرام (Instagram) — Pipeline end-to-end")
    report = TestReport("instagram")

    # ═══ ۱) env ═══
    section("مرحله ۱: بررسی env و config")
    env_info = check_env("instagram")
    raw = env_info.get("raw", {})

    report.add(
        "env.enabled",
        "ok" if env_info["enabled"] else "skip",
        f"INSTAGRAM_ENABLED={env_info['enabled']} (پیش‌فرض خاموش)",
    )

    if not env_info["enabled"]:
        print(
            f"{C.INFO}⏭️ اینستاگرام غیرفعال (skip) — "
            f"برای اجرا: INSTAGRAM_ENABLED=true{C.END}"
        )
        report.print_summary()
        report.save()
        return True

    report.add(
        "env.session_path", "ok",
        env_info["session_path"],
    )

    # ═══ ۲) نشست ═══
    section("مرحله ۲: بررسی نشست")
    session_exists = _session_has_data(env_info["session_path"])
    report.add(
        "session.exists",
        "ok" if session_exists else "fail",
        f"exists={session_exists}",
    )
    if not session_exists:
        print(f"\n{C.FAIL}❌ نشست Instagram وجود ندارد{C.END}")
        print(f"   راه‌حل:")
        print(f"     ۱) BROWSER_HEADLESS=false در .env")
        print(f"     ۲) python login.py → گزینهٔ ۷")
        print(f"     ۳) در مرورگر لاگین کن")
        report.print_summary()
        report.save()
        return False

    # ═══ ۳) pipeline ═══
    section("مرحله ۳: ساخت pipeline end-to-end")
    pipeline = TestPipeline(
        keywords_only=False, require_t1=False,
        allowed_tiers=("T1", "T2", "T3"),
    )
    await pipeline.start()
    report.add(
        "pipeline.init", "ok",
        f"db={os.path.basename(pipeline._db_path)}",
    )

    # ═══ ۴) collector ═══
    section("مرحله ۴: ساخت collector")
    try:
        from collectors.instagram import InstagramCollector
        collector = InstagramCollector(
            headless=headless,
            poll_seconds=999,
            max_posts=max_posts,
            max_stories=5,
            story_delay_min=2.0,
            story_delay_max=4.0,
            read_stories=False,   # تست سریع‌تر — فقط پست
            read_posts=True,
        )
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
            print(f"\n{C.WARN}⚠️ اتصال Instagram شکست خورد{C.END}")
            print(f"   راه‌حل: python login.py → گزینهٔ ۷")
            print(f"   ⚠️ در ایران VPN لازم است")
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

    # ═══ ۶) is_logged_in ═══
    section("مرحله ۶: چک لاگین")
    try:
        logged = await collector._is_logged_in()
        url = ""
        try:
            url = collector._page.url[:80] if collector._page else ""
        except Exception:
            pass
        report.add(
            "is_logged_in",
            "ok" if logged else "fail",
            f"logged={logged} | url={url}",
        )
    except Exception as e:
        report.add("is_logged_in", "warn", str(e))

    # ═══ ۷) fetch_messages ═══
    section("مرحله ۷: fetch_messages (فقط فالوورها)")
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

    # ═══ ۸) ساختار ═══
    section("مرحله ۸: بررسی ساختار پیام‌ها")
    if msgs:
        sample = msgs[0]
        required = ["id", "platform", "text", "chat_id", "chat_name", "timestamp"]
        missing = [k for k in required if k not in sample]
        report.add(
            "msg.structure",
            "ok" if not missing else "warn",
            f"missing={missing}",
        )

        print(f"\n  📩 {min(5, len(msgs))} آیتم scrape‌شده:")
        for m in msgs[:5]:
            txt = (m.get("text") or "")[:60].replace("\n", " ")
            ctype = m.get("chat_type", "?")
            print(
                f"     [{m.get('chat_name', '?')[:30]}] "
                f"({ctype}) id={m.get('id', '?')[:20]} | {txt}"
            )

        types = {m.get("chat_type") for m in msgs}
        report.add(
            "msg.stats", "ok",
            f"types={types}",
        )
    else:
        report.add(
            "msg.structure", "warn",
            "هیچ آیتمی fetch نشد (طبیعی اگر فالووری پستی نداشت)",
        )

    # ═══ ۹) feed pipeline ═══
    section("مرحله ۹: عبور از pipeline")
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

    # ═══ ۱۰) چک DB ═══
    section("مرحله ۱۰: چک ذخیره‌سازی در DB")
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
            f"{len(msgs)} scrape | {len(msgs_with_kw)} کلیدواژه‌دار",
        )

        if msgs_with_kw:
            found = await pipeline.find_in_db(
                msgs_with_kw, platform="instagram"
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
        else:
            report.add(
                "db.scrape_saved", "skip",
                f"هیچ‌کدام از {len(msgs)} آیتم کلیدواژه ندارند",
            )
            print(f"\n  ℹ️ هیچ کلیدواژه‌ای در feed فالوورها نبود")
            print(f"     💡 برای تست کامل: پیامی با برند "
                  f"در یکی از پیج‌های فالوو بشه")
    else:
        report.add(
            "db.scrape_saved", "skip",
            "هیچ آیتمی برای چک نیست",
        )

    # ═══ ۱۱) آمار DB ═══
    section("مرحله ۱۱: آمار DB")
    try:
        total_db = await pipeline.count_messages(platform="instagram")
        total_leads = await pipeline.count_leads(platform="instagram")
        report.add(
            "db.total_messages", "ok",
            f"instagram: {total_db} آیتم در DB تست",
        )
        report.add(
            "db.total_leads", "ok",
            f"instagram: {total_leads} سرنخ در DB تست",
        )
    except Exception as e:
        report.add("db.leads_check", "warn", str(e))

    # ═══ ۱۲) dump_dom ═══
    section("مرحله ۱۲: dump_dom")
    try:
        dom_path = await collector.dump_dom()
        size = (
            os.path.getsize(dom_path)
            if dom_path and os.path.exists(dom_path)
            else 0
        )
        report.add(
            "dump_dom",
            "ok" if dom_path and size > 0 else "warn",
            f"size={size}B",
        )
    except Exception as e:
        report.add("dump_dom", "warn", str(e))

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

    ok = report.print_summary()
    report.save()
    return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-posts", type=int, default=5)
    args = parser.parse_args()

    ok = asyncio.run(
        main(headless=args.headless, max_posts=args.max_posts)
    )
    sys.exit(0 if ok else 1)