# tests/test_youtube_full.py
"""
تست کامل یوتیوب — Pipeline end-to-end.

Pipeline:
  Collector → EventBus → StorageHandler → DB (isolated)

نسخهٔ ۱.۰ (این نسخه):
  - 🎯 تست کامل کلکتور یوتیوب (روش جدید: player response)
  - ✅ استخراج description کامل از window.ytInitialPlayerResponse
  - ✅ استخراج لینک ویدیو + لینک کانال
  - ✅ ضدتکراری با seen_ids
  - ✅ فیلتر کلیدواژه روی title + description + channel
  - ⏭️ skip منطقی: اگر پلتفرم خاموش بود → skip (نه fail)
  - ⏭️ skip منطقی: اگر هیچ ویدیویی کلیدواژه نداشت → skip
  - ✅ هم‌راستا با test_bale_full.py و test_eitaa_full.py

پیش‌نیازها:
  - YOUTUBE_ENABLED=true در .env
  - نشست ذخیره‌شده در storage/sessions/youtube_web/
  - Chrome روی سیستم (CHROME_PATH در config)

اجرا:
  python tests/test_youtube_full.py
  python tests/test_youtube_full.py --headless
  python tests/test_youtube_full.py --max-videos 10
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
    TestReport,
    banner,
    check_env,
    safe_disconnect,
    section,
    C,
)
from tests.pipeline_helpers import TestPipeline


# ═════════════════════════════════════════════════════════
# helpers
# ═════════════════════════════════════════════════════════
def _session_dir() -> str:
    """مسیر پوشهٔ نشست یوتیوب."""
    try:
        from config import STORAGE_DIR
        return os.path.join(
            STORAGE_DIR, "sessions", "youtube_web"
        )
    except Exception:
        root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        return os.path.join(
            root, "storage", "sessions", "youtube_web"
        )


def _session_has_data(path: str) -> bool:
    """آیا پوشهٔ نشست محتوای واقعی دارد؟"""
    if not path or not os.path.isdir(path):
        return False
    try:
        entries = [
            e for e in os.listdir(path)
            if not e.startswith(".")
        ]
        return (
            "Default" in entries
            or "Local State" in entries
            or len(entries) > 3
        )
    except Exception:
        return False


# ═════════════════════════════════════════════════════════
# main
# ═════════════════════════════════════════════════════════
async def main(
    headless: bool = False,
    max_videos: int = 10,
):
    banner("تست کامل یوتیوب (YouTube) — Pipeline end-to-end")
    report = TestReport("youtube")

    # ═══ ۱) env ═══
    section("مرحله ۱: بررسی env و config")
    env_info = check_env("youtube")
    raw = env_info.get("raw", {})

    # یوتیوب ممکنه در PLUGINS_CONFIG نباشه → env مستقیم
    enabled = raw.get("enabled", None)
    if enabled is None:
        enabled_str = os.getenv(
            "YOUTUBE_ENABLED", "false"
        ).strip().lower()
        enabled = enabled_str in ("true", "1", "yes", "on")

    report.add(
        "env.enabled",
        "ok" if enabled else "skip",
        f"YOUTUBE_ENABLED={enabled}",
    )
    if not enabled:
        print(
            f"{C.INFO}⏭️ یوتیوب غیرفعال (skip) — "
            f"برای اجرا: YOUTUBE_ENABLED=true{C.END}"
        )
        report.print_summary()
        report.save()
        return True

    # ═══ ۲) Chrome ═══
    section("مرحله ۲: بررسی Chrome")
    chrome_path = None
    try:
        from config import CHROME_PATH
        if CHROME_PATH and os.path.exists(CHROME_PATH):
            chrome_path = CHROME_PATH
            report.add(
                "chrome.config", "ok",
                f"{CHROME_PATH}",
            )
    except Exception:
        pass

    if not chrome_path:
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(
                r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
            ),
        ]
        for p in candidates:
            if os.path.exists(p):
                chrome_path = p
                report.add("chrome.system", "ok", f"{p}")
                break

    if not chrome_path:
        report.add(
            "chrome.fallback", "warn",
            "از channel=chrome استفاده می‌شود",
        )

    # ═══ ۳) نشست ═══
    section("مرحله ۳: بررسی نشست یوتیوب")
    profile_dir = _session_dir()
    report.add("session.dir", "ok", profile_dir)

    if not _session_has_data(profile_dir):
        report.add(
            "session.has_data", "fail",
            "نشست یوتیوب پیدا نشد",
        )
        print(f"\n{C.FAIL}❌ نشست یوتیوب وجود ندارد{C.END}")
        print("   راه‌حل:")
        print("     ۱) python login.py → گزینهٔ ۸ (YouTube)")
        print("     ۲) در Chrome باز شده Sign in کن")
        print("     ۳) بعد از دیدن آواتار، Chrome را نبند")
        print("     ۴) دوباره این تست را اجرا کن")
        report.print_summary()
        report.save()
        return False

    report.add(
        "session.has_data", "ok",
        f"has_data=True",
    )

    # ═══ ۴) pipeline ═══
    section("مرحله ۴: ساخت pipeline end-to-end")
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

    # ═══ ۵) ساخت collector ═══
    section("مرحله ۵: ساخت collector")
    try:
        from collectors.youtube import YouTubeCollector

        collector = YouTubeCollector(
            headless=headless,
            poll_seconds=999,
            max_videos=max_videos,
            delay_between=3.0,
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

    # ═══ ۶) connect ═══
    section("مرحله ۶: connect()")
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
            print(f"\n{C.WARN}⚠️ اتصال یوتیوب شکست خورد{C.END}")
            print("   راه‌حل: python login.py → گزینهٔ ۸")
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

    # ═══ ۷) is_logged_in ═══
    section("مرحله ۷: چک لاگین")
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

    # ═══ ۸) fetch_messages ═══
    section("مرحله ۸: fetch_messages")
    print(f"\n  {C.INFO}⏳ این مرحله ممکن است ۱-۳ دقیقه طول بکشد{C.END}")

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

    # ═══ ۹) ساختار پیام‌ها ═══
    section("مرحله ۹: بررسی ساختار پیام‌ها")
    if msgs:
        sample = msgs[0]
        required_keys = [
            "id", "platform", "text", "chat_id", "chat_name",
            "chat_type", "timestamp", "media_type", "chat_link",
        ]
        missing = [k for k in required_keys if k not in sample]
        report.add(
            "msg.structure",
            "ok" if not missing else "warn",
            f"missing={missing}",
        )

        print(f"\n  📩 {min(5, len(msgs))} ویدیوی scrape‌شده:")
        for m in msgs[:5]:
            txt = (m.get("text") or "")[:70].replace("\n", " ")
            chat = (m.get("chat_name") or "?")[:30]
            link = m.get("chat_link", "")
            print(f"     [{chat}]")
            print(f"        {txt}")
            print(f"        🔗 {link}")

        platforms = {m.get("platform") for m in msgs}
        channels = {m.get("chat_name") for m in msgs}
        report.add(
            "msg.stats", "ok",
            f"platforms={platforms} | channels={len(channels)}",
        )

        # ─── چک لینک ویدیو ───
        with_link = [
            m for m in msgs
            if m.get("chat_link", "").startswith(
                "https://www.youtube.com/watch?v="
            )
        ]
        report.add(
            "msg.links",
            "ok" if len(with_link) == len(msgs) else "warn",
            f"{len(with_link)}/{len(msgs)} لینک مستقیم دارند",
        )

        # ─── چک لینک کانال ───
        with_channel_url = [
            m for m in msgs
            if m.get("channel_url")
        ]
        report.add(
            "msg.channel_url",
            "ok" if with_channel_url else "info",
            f"{len(with_channel_url)}/{len(msgs)} لینک کانال دارند",
        )
    else:
        report.add(
            "msg.structure", "warn",
            "هیچ ویدیویی fetch نشد "
            "(شاید فید خالی است یا نشست تازه است)",
        )

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
                msgs_with_kw, platform="youtube"
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
                print("\n  📥 نمونهٔ leadهای ذخیره‌شده در DB:")
                for rid, row in list(found.items())[:5]:
                    chat = (row.get("chat_name") or "?")[:30]
                    txt = (row.get("text") or "")[:60]
                    kwv = row.get("keywords") or "—"
                    print(f"     [{chat}] {txt}")
                    print(f"        db_id={row.get('id', '')[:30]} | "
                          f"kw={kwv[:40]}")
        else:
            report.add(
                "db.scrape_saved", "skip",
                f"هیچ‌کدام از {len(msgs)} ویدیو کلیدواژه ندارند",
            )
            print("\n  ℹ️ هیچ ویدیوی کلیدواژه‌داری در این scrape نبود")
            print("     💡 برای تست کامل: در یوتیوب کانال‌هایی "
                  "با محتوای برند فالو کن")
    else:
        report.add(
            "db.scrape_saved", "skip",
            "هیچ ویدیویی برای چک نیست",
        )

    # ═══ ۱۲) آمار DB ═══
    section("مرحله ۱۲: آمار DB")
    try:
        total_db = await pipeline.count_messages(platform="youtube")
        total_leads = await pipeline.count_leads(platform="youtube")
        report.add(
            "db.total_messages", "ok",
            f"youtube: {total_db} ویدیو در DB تست",
        )
        report.add(
            "db.total_leads", "ok",
            f"youtube: {total_leads} سرنخ در DB تست",
        )

        if total_leads > 0:
            leads = await pipeline.find_leads_in_db("youtube")
            print(f"\n  🎯 {min(3, len(leads))} lead نمونه:")
            for ld in leads[:3]:
                chat = (ld.get("chat_name") or "?")[:30]
                kwv = (ld.get("keywords") or "—")[:40]
                tier = ld.get("opportunity_type") or "—"
                print(f"     [{chat}] kw={kwv} | tier={tier}")
    except Exception as e:
        report.add("db.leads_check", "warn", str(e))

    # ═══ ۱۳) dump_dom ═══
    section("مرحله ۱۳: dump_dom")
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

    # ═══ ۱۴) disconnect ═══
    section("مرحله ۱۴: disconnect")
    await safe_disconnect(collector, report)

    # ═══ ۱۵) پاک‌سازی pipeline ═══
    section("مرحله ۱۵: پاک‌سازی pipeline")
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
        description="تست کامل یوتیوب — Pipeline end-to-end",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="اجرای مرورگر در حالت headless (بدون پنجره)",
    )
    parser.add_argument(
        "--max-videos", type=int, default=10,
        help="حداکثر تعداد ویدیو برای scrape (پیش‌فرض 10)",
    )
    args = parser.parse_args()

    ok = asyncio.run(
        main(
            headless=args.headless,
            max_videos=args.max_videos,
        )
    )
    sys.exit(0 if ok else 1)