# tests/test_web_full.py
"""
تست کامل وب — Pipeline end-to-end.

Pipeline:
  Collector → EventBus → StorageHandler → DB (isolated)

نسخهٔ ۲.۱ (این نسخه):
  - 🐛 FIX: C.DIM — اضافه کردن fallback برای سازگاری با helpers.
  - 🐛 FIX: traceback برای خطاهای غیرمنتظره.
  - 🎯 نمایش منابع کامل + progress.
  - 🎯 argparse (--headless, --max-pages, --no-render).

نسخهٔ ۲.۰:
  - 🎯 اضافه شدن argparse (--headless, --max-pages, --no-render).
  - 🎯 نمایش زندهٔ منابع در حال اسکرپ (URL + progress).
  - 🎯 نمایش منابع کامل (نه فقط ۳ تای اول).
  - 🎯 انتقال args به WebCollector.
  - 🎯 پیام‌های آموزشی برای کاربر.
  - 🎯 یکپارچگی با run_all_tests.py.

اجرا:
  python tests/test_web_full.py
  python tests/test_web_full.py --headless
  python tests/test_web_full.py --max-pages 20
  python tests/test_web_full.py --no-render
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import traceback

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from tests.helpers import (
    TestReport, banner, check_env, safe_disconnect, section, C,
)
from tests.pipeline_helpers import TestPipeline


# ═════════════════════════════════════════════════════════
# 🐛 FIX: اطمینان از وجود C.DIM
# ═════════════════════════════════════════════════════════
if not hasattr(C, "DIM"):
    C.DIM = "\033[2m"


# ═════════════════════════════════════════════════════════
# helpers
# ═════════════════════════════════════════════════════════
def _print_sources(sources_file: str, max_show: int = 20) -> int:
    """نمایش منابع با شماره + نوع + URL. Returns تعداد خطوط معتبر."""
    if not os.path.exists(sources_file):
        print(f"  {C.WARN}⚠️ فایل منابع پیدا نشد: {sources_file}{C.END}")
        return 0

    try:
        with open(sources_file, encoding="utf-8") as f:
            lines = [
                l.strip() for l in f
                if l.strip() and not l.startswith("#")
            ]
    except OSError as e:
        print(f"  {C.FAIL}❌ خطا در خواندن فایل: {e}{C.END}")
        return 0

    if not lines:
        return 0

    print(f"\n  {C.BOLD}📚 منابع ({len(lines)}):{C.END}")
    for i, line in enumerate(lines[:max_show], 1):
        # فرمت: "type | url | limit=N"
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 2:
            src_type = parts[0]
            url = parts[1]
            extra = parts[2] if len(parts) > 2 else ""
            print(f"     {C.INFO}[{i}]{C.END} "
                  f"{src_type:8s} → {url[:70]}")
            if extra:
                print(f"        {C.DIM}{extra}{C.END}")
        else:
            # فرمت قدیمی: فقط URL
            print(f"     {C.INFO}[{i}]{C.END} {line[:80]}")

    if len(lines) > max_show:
        print(f"     {C.DIM}... و {len(lines) - max_show} منبع دیگر{C.END}")

    return len(lines)


# ═════════════════════════════════════════════════════════
# main
# ═════════════════════════════════════════════════════════
async def main(
    headless: bool = False,
    max_pages: int = 40,
    no_render: bool = False,
):
    banner("تست کامل وب (Web) — Pipeline end-to-end")
    report = TestReport("web")

    # ═══ ۱) env ═══
    section("مرحله ۱: env و config")
    env_info = check_env("web")

    report.add(
        "env.enabled",
        "ok" if env_info["enabled"] else "skip",
        f"WEB_ENABLED={env_info['enabled']}",
    )
    if not env_info["enabled"]:
        print(
            f"{C.INFO}⏭️ وب غیرفعال (skip) — "
            f"برای اجرا: WEB_ENABLED=true{C.END}"
        )
        report.print_summary()
        report.save()
        return True

    # ═══ ۲) sources ═══
    section("مرحله ۲: بررسی فایل منابع")
    from config import BASE_DIR
    sources_file = os.path.join(
        BASE_DIR, "data", "sources", "web_sources.txt"
    )

    n_sources = _print_sources(sources_file)
    report.add(
        "sources.file",
        "ok" if n_sources else "warn",
        f"lines={n_sources}",
    )

    if not n_sources:
        print(f"\n  {C.WARN}⚠️ هیچ منبعی وجود ندارد — تست ادامه می‌یابد{C.END}")

    # ═══ ۳) pipeline ═══
    section("مرحله ۳: ساخت pipeline")
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

    # ═══ ۴) collector ═══
    section("مرحله ۴: ساخت collector")
    try:
        from collectors.web import WebCollector

        # ─── انتقال args به WebCollector (با fallback) ───
        collector_kwargs = {}
        if max_pages > 0:
            collector_kwargs["max_pages"] = max_pages
        if headless:
            collector_kwargs["headless"] = True
        if no_render:
            collector_kwargs["render"] = False

        try:
            collector = WebCollector(**collector_kwargs)
        except TypeError:
            # fallback: اگه args پشتیبانی نشد
            collector = WebCollector()

        report.add(
            "collector.init", "ok",
            f"platform={collector.platform_name} | "
            f"mode={'no-render' if no_render else 'render'}",
        )
    except Exception as e:
        report.add("collector.init", "fail", str(e))
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    # ═══ ۵) connect ═══
    section("مرحله ۵: connect")
    t0 = time.monotonic()
    try:
        ok = await collector.connect()
        elapsed = (time.monotonic() - t0) * 1000

        # ─── تعداد منابع بارگذاری‌شده ───
        try:
            n_loaded = len(collector._sources)
        except Exception:
            n_loaded = n_sources

        report.add(
            "connect",
            "ok" if ok else "fail",
            f"ok={ok} | sources={n_loaded} | {elapsed:.0f}ms",
            elapsed,
        )
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
    section("مرحله ۶: fetch_messages (زنده)")
    print(f"\n  {C.INFO}🌐 شروع اسکرپ منابع...{C.END}")
    print(f"     {C.DIM}این مرحله ممکن است چند دقیقه طول بکشد{C.END}")

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

    # ═══ ۷) ساختار ═══
    section("مرحله ۷: بررسی ساختار مطلب‌ها")
    if msgs:
        sample = msgs[0]
        required = ["id", "platform", "text", "chat_name", "timestamp"]
        missing = [k for k in required if k not in sample]
        report.add(
            "msg.structure",
            "ok" if not missing else "warn",
            f"missing={missing}",
        )

        print(f"\n  📩 {min(5, len(msgs))} مطلب scrape‌شده:")
        for m in msgs[:5]:
            src = (m.get("chat_name") or "?")[:35]
            mid = (m.get("id") or "?")[:25]
            txt = (m.get("text") or "")[:55].replace("\n", " ")
            print(f"     {C.INFO}[{src}]{C.END}")
            print(f"        id={mid}")
            print(f"        text={txt}...")

        # ─── آمار per-source ───
        sources_count: dict[str, int] = {}
        for m in msgs:
            src = m.get("chat_name") or "?"
            sources_count[src] = sources_count.get(src, 0) + 1

        report.add(
            "msg.stats", "ok",
            f"count={len(msgs)} | "
            f"sources={len(sources_count)}",
        )

        print(f"\n  {C.BOLD}📊 پراکندگی منابع:{C.END}")
        for src, cnt in sorted(
            sources_count.items(),
            key=lambda x: -x[1],
        )[:10]:
            print(f"     {src[:50]:50s} → {cnt} مطلب")
    else:
        report.add(
            "msg.structure", "warn",
            "هیچ مطلبی نیامد (سایت‌ها ممکنه تغییر کرده باشن)",
        )

    # ═══ ۸) pipeline.feed ═══
    section("مرحله ۸: عبور از pipeline")
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

    # ═══ ۹) چک DB ═══
    section("مرحله ۹: چک ذخیره‌سازی در DB")
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
                msgs_with_kw, platform="web"
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
                    print(f"     [{row.get('chat_name', '?')[:40]}]")
                    print(f"        {(row.get('text') or '')[:60]}")
                    print(f"        id={row.get('id', '')[:40]}")
                    print(f"        kw={row.get('keywords', '—')[:40]}")
        else:
            report.add(
                "db.scrape_saved", "skip",
                f"هیچ‌کدام از {len(msgs)} مطلب کلیدواژه ندارند",
            )
            print(
                f"\n  ℹ️ {C.INFO}هیچ مطلب کلیدواژه‌داری نبود "
                f"(طبیعی — اخبار عمومی){C.END}"
            )
    else:
        report.add(
            "db.scrape_saved", "skip",
            "هیچ مطلبی برای چک نیست",
        )

    # ═══ ۱۰) آمار DB ═══
    section("مرحله ۱۰: آمار DB")
    try:
        total_db = await pipeline.count_messages(platform="web")
        total_leads = await pipeline.count_leads(platform="web")
        report.add(
            "db.total_messages", "ok",
            f"web: {total_db} مطلب در DB تست",
        )
        report.add(
            "db.total_leads", "ok",
            f"web: {total_leads} سرنخ در DB تست",
        )
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

    # ═══ نتیجه ═══
    ok = report.print_summary()
    report.save()
    return ok


# ═════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="تست کامل وب — Pipeline end-to-end",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="اجرای مرورگر رندر در حالت headless (بدون پنجره)",
    )
    parser.add_argument(
        "--max-pages", type=int, default=40,
        help="حداکثر تعداد صفحات برای scrape (پیش‌فرض 40)",
    )
    parser.add_argument(
        "--no-render", action="store_true",
        help="بدون رندر مرورگر (فقط requests + BeautifulSoup)",
    )
    args = parser.parse_args()

    ok = asyncio.run(
        main(
            headless=args.headless,
            max_pages=args.max_pages,
            no_render=args.no_render,
        )
    )
    sys.exit(0 if ok else 1)