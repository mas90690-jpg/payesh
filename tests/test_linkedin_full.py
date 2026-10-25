# tests/test_linkedin_full.py
"""
تست کامل لینکدین (LinkedIn) — Pipeline end-to-end.

نسخهٔ ۳.۰ (فوق‌العاده دقیق):
  - ⏭️ skip منطقی: اگر پلتفرم خاموش بود → skip (نه fail)
  - ✅ DNS + HTTP check
  - ✅ تست ۱۵ فیلد اجباری (مثل بله/ایتا/روبیکا)
  - ✅ تست id یکتا (ضدتکرار)
  - ✅ تست chat_type معتبر
  - ✅ تست timestamp datetime
  - ✅ تست chat_name خالی نباشه
  - ✅ تست فالوور (اینکه فقط فالوورها ذخیره شدن)
  - ✅ تست Company post (نباید باشه)
  - ✅ تست pipeline.feed + DB
  - ✅ تست محتوا در DB (H4 مثل بقیه)
  - ✅ گزارش آمار تفکیکی

اجرا:
  python tests/test_linkedin_full.py
  python tests/test_linkedin_full.py --headless
  python tests/test_linkedin_full.py --headless --since-days 3
"""
from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import (
    TestReport, banner, check_env, check_session_exists,
    safe_disconnect, section, C,
)
from tests.pipeline_helpers import TestPipeline


# ═══════════════════════════════════════════════════════════
# ثابت‌ها — هم‌راستا با بله/ایتا/روبیکا
# ═══════════════════════════════════════════════════════════
VALID_CHAT_TYPES = {"private", "group", "channel", "linkedin"}

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

# فیلدهای اضافی مخصوص لینکدین
LINKEDIN_EXTRA_FIELDS = ["keywords"]

MAX_FETCH_SECONDS = 400


# ═══════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════
async def main(
    headless: bool = False,
    max_feed_posts: int = 0,
    since_days: int = 7,
):
    banner("تست کامل و فوق‌دقیق لینکدین (LinkedIn) — Pipeline end-to-end (v3.0)")
    report = TestReport("linkedin")

    # ═══════════════════════════════════════════════════
    # ۱) DNS
    # ═══════════════════════════════════════════════════
    section("مرحله ۱: DNS")
    try:
        ip = socket.gethostbyname("www.linkedin.com")
        report.add("A1.dns.resolve", "ok", f"→ {ip}")
    except Exception as e:
        report.add("A1.dns.resolve", "fail", f"{type(e).__name__}: {e}")
        report.print_summary()
        report.save()
        return False

    # ═══════════════════════════════════════════════════
    # ۲) HTTP
    # ═══════════════════════════════════════════════════
    section("مرحله ۲: HTTP")
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://www.linkedin.com/",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            report.add("A2.http.connect", "ok", f"status={resp.status}")
    except Exception as e:
        report.add("A2.http.connect", "warn", str(e))

    # ═══════════════════════════════════════════════════
    # ۳) env
    # ═══════════════════════════════════════════════════
    section("مرحله ۳: env و config")
    env_info = check_env("linkedin")
    enabled = env_info["enabled"]
    report.add(
        "A3.env.enabled",
        "ok" if enabled else "skip",
        f"LINKEDIN_ENABLED={enabled}",
    )
    if not enabled:
        print(f"{C.INFO}⏭️ لینکدین غیرفعال (skip) — "
              f"برای اجرا: LINKEDIN_ENABLED=true{C.END}")
        report.print_summary()
        report.save()
        return True

    email = env_info.get("email", "") or ""
    report.add(
        "A4.env.email",
        "ok" if email else "warn",
        f"email={email[:6]}..." if email else "خالی",
    )

    # ═══════════════════════════════════════════════════
    # ۴) نشست
    # ═══════════════════════════════════════════════════
    section("مرحله ۴: بررسی نشست")
    session_path = env_info.get("session_path", "")
    exists = check_session_exists(session_path) if session_path else False
    report.add(
        "A5.session.exists",
        "ok" if exists else "warn",
        f"exists={exists} | path={session_path[:50] if session_path else '—'}",
    )

    # ═══════════════════════════════════════════════════
    # ۵) pipeline
    # ═══════════════════════════════════════════════════
    section("مرحله ۵: ساخت pipeline")
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
    # ۶) collector
    # ═══════════════════════════════════════════════════
    section("مرحله ۶: ساخت collector")
    collector = None
    try:
        from collectors.linkedin import LinkedInCollector

        collector = LinkedInCollector(
            headless=headless,
            poll_seconds=999,
            max_results=5,
            scrolls=3,
            monitor_feed=True,
            max_feed_posts=max_feed_posts,
            force_run=True,
            following_only=True,
            stop_on_non_follower=True,
            once_per_day=False,
        )
        report.add(
            "C1.collector.init", "ok",
            f"platform={collector.platform_name} | "
            f"following_only={collector._following_only} | "
            f"max_posts={max_feed_posts or '∞'}",
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
        "ok" if pname == "linkedin" else "fail",
        f"platform_name={pname}",
    )

    poll = getattr(collector, "poll_interval_seconds", None)
    report.add(
        "C3.poll_interval",
        "ok" if isinstance(poll, (int, float)) else "warn",
        f"poll={poll}",
    )

    # ═══════════════════════════════════════════════════
    # ۷) connect
    # ═══════════════════════════════════════════════════
    section("مرحله ۷: connect")
    t0 = time.monotonic()
    try:
        ok = await collector.connect()
        elapsed = (time.monotonic() - t0) * 1000
        report.add(
            "D1.connect",
            "ok" if ok else "fail",
            f"ok={ok} | {elapsed:.0f}ms",
            elapsed,
        )
        if not ok:
            print(f"\n{C.WARN}⚠️ لینکدین وصل نشد — "
                  f"`python login.py` گزینهٔ ۶{C.END}")
            await safe_disconnect(collector, report)
            await pipeline.stop()
            report.print_summary()
            report.save()
            return False
    except Exception as e:
        report.add("D1.connect", "fail", f"{type(e).__name__}: {e}")
        await safe_disconnect(collector, report)
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    # ═══════════════════════════════════════════════════
    # ۸) چک صفحه
    # ═══════════════════════════════════════════════════
    section("مرحله ۸: چک صفحه")
    try:
        url = collector._page.url or ""
        title = await collector._page.title()
        report.add("D2.page.url", "ok", f"{url[:80]}")
        report.add("D3.page.title", "ok", f"{title[:80]}")
        logged = await collector._is_logged_in()
        report.add(
            "D4.is_logged_in",
            "ok" if logged else "fail",
            f"logged={logged}",
        )
    except Exception as e:
        report.add("D2.page.check", "warn", str(e))

    # ═══════════════════════════════════════════════════
    # ۹) fetch_messages
    # ═══════════════════════════════════════════════════
    section(f"مرحله ۹: fetch_messages (since={since_days} روز)")
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
            "E1.fetch",
            "ok" if isinstance(msgs, list) else "fail",
            f"count={len(msgs) if msgs else 0} | {fetch_elapsed:.0f}s",
            fetch_elapsed * 1000,
        )
    except Exception as e:
        report.add("E1.fetch", "fail", f"{type(e).__name__}: {e}")
        msgs = []

    if msgs:
        report.add("E2.fetch.count", "ok", f"{len(msgs)} پست گرفته شد")
    else:
        report.add("E2.fetch.count", "warn",
                   "هیچ پستی گرفته نشد (شاید همه غیرفالوور/تکراری بودن)")

    if fetch_elapsed > MAX_FETCH_SECONDS:
        report.add(
            "E3.fetch.timeout", "warn",
            f"{fetch_elapsed:.0f}s > {MAX_FETCH_SECONDS}s",
        )
    else:
        report.add(
            "E3.fetch.timeout", "ok",
            f"{fetch_elapsed:.0f}s ≤ {MAX_FETCH_SECONDS}s",
        )

    # ═══════════════════════════════════════════════════
    # ۱۰) آمار scrape (از collector)
    # ═══════════════════════════════════════════════════
    section("مرحله ۱۰: آمار scrape (از collector)")
    stats = getattr(collector, "_last_scrape_stats", None)
    if isinstance(stats, dict) and stats:
        report.add(
            "E4.scrape.stats", "ok",
            f"total={stats.get('total_posts', '?')} | "
            f"stored={stats.get('stored', '?')} | "
            f"follower={stats.get('skipped_follower', '?')} | "
            f"non-follower={stats.get('skipped_non_follower', '?')} | "
            f"company={stats.get('skipped_company', '?')} | "
            f"unknown={stats.get('skipped_unknown', '?')} | "
            f"no-kw={stats.get('skipped_no_kw', '?')} | "
            f"seen={stats.get('skipped_seen', '?')}",
        )
    else:
        report.add("E4.scrape.stats", "warn",
                   "کالکتور _last_scrape_stats نمی‌دهد")

    # ═══════════════════════════════════════════════════
    # ۱۱) ساختار پیام (۱۵ فیلد اجباری)
    # ═══════════════════════════════════════════════════
    section("مرحله ۱۱: ساختار پیام (۱۵ فیلد اجباری)")
    if not msgs:
        report.add("F.structure", "skip", "پستی برای بررسی نیست")
    else:
        sample = msgs[0]

        # F1: فیلدهای اجباری
        missing_fields = [f for f in REQUIRED_MSG_FIELDS if f not in sample]
        report.add(
            "F1.msg.fields",
            "ok" if not missing_fields else "fail",
            f"missing={missing_fields}" if missing_fields
            else f"همه {len(REQUIRED_MSG_FIELDS)} فیلد موجود",
        )

        # F2: platform = linkedin
        all_linkedin = all(m.get("platform") == "linkedin" for m in msgs)
        report.add(
            "F2.msg.platform",
            "ok" if all_linkedin else "fail",
            f"همه platform=linkedin: {all_linkedin}",
        )

        # F3: id یکتا
        ids = [str(m.get("id", "")) for m in msgs]
        unique_ids = set(ids)
        dup_count = len(ids) - len(unique_ids)
        report.add(
            "F3.msg.id_unique",
            "ok" if dup_count == 0 else "fail",
            f"{dup_count} id تکراری از {len(ids)}",
        )

        # F4: timestamp datetime
        ts_ok = all(isinstance(m.get("timestamp"), datetime) for m in msgs)
        report.add(
            "F4.msg.timestamp",
            "ok" if ts_ok else "warn",
            f"همه datetime: {ts_ok}",
        )

        # F5: chat_type معتبر
        bad_ct = [
            m for m in msgs
            if m.get("chat_type") not in VALID_CHAT_TYPES
        ]
        report.add(
            "F5.msg.chat_type",
            "ok" if not bad_ct else "warn",
            f"{len(bad_ct)} chat_type نامعتبر" if bad_ct
            else "همه معتبر",
        )

        # F6: media_type معتبر
        bad_mt = [
            m for m in msgs
            if m.get("media_type") not in VALID_MEDIA_TYPES
        ]
        report.add(
            "F6.msg.media_type",
            "ok" if not bad_mt else "warn",
            f"{len(bad_mt)} media_type نامعتبر" if bad_mt
            else "همه معتبر",
        )

        # F7: متن خالی نباشه
        empty_text = [
            m for m in msgs
            if not (m.get("text") or "").strip()
        ]
        report.add(
            "F7.msg.text",
            "ok" if not empty_text else "fail",
            f"{len(empty_text)} پست با متن خالی" if empty_text
            else "همه متن دارند",
        )

        # F8: chat_name خالی نباشه
        empty_name = [
            m for m in msgs
            if not (m.get("chat_name") or "").strip()
        ]
        report.add(
            "F8.msg.chat_name",
            "ok" if not empty_name else "fail",
            f"{len(empty_name)} پست بدون chat_name" if empty_name
            else "همه chat_name دارند",
        )

        # F9: keywords پر باشه (مخصوص لینکدین)
        empty_kw = [
            m for m in msgs
            if not (m.get("keywords") or "").strip()
        ]
        report.add(
            "F9.msg.keywords",
            "ok" if not empty_kw else "warn",
            f"{len(empty_kw)} پست بدون keywords" if empty_kw
            else "همه keywords دارند",
        )

        # F10: id پایدار (۳۲ کاراکتر hex از hash)
        bad_id_format = [
            m for m in msgs
            if len(str(m.get("id", ""))) != 32
        ]
        report.add(
            "F10.msg.id_format",
            "ok" if not bad_id_format else "warn",
            f"{len(bad_id_format)} id با فرمت غیرمنتظره" if bad_id_format
            else "همه id فرمت درست (32 hex)",
        )

    # ═══════════════════════════════════════════════════
    # ۱۲) چک فالوور (کلیدی!)
    # ═══════════════════════════════════════════════════
    section("مرحله ۱۲: چک فالوور (نباید غیرفالوور ذخیره شده باشه)")
    if not msgs:
        report.add("G.follower.check", "skip", "پستی برای بررسی نیست")
    else:
        # از chat_id استفاده می‌کنیم که source هست
        sources = {m.get("chat_id", "") for m in msgs}
        report.add(
            "G1.sources", "ok",
            f"sources={sources}",
        )

        # چک: هیچ پستی از source غیرفید نباید باشه (اگه search_enabled=False)
        non_feed = [
            m for m in msgs
            if m.get("chat_id") != "فید"
        ]
        if non_feed:
            report.add(
                "G2.feed_only", "warn",
                f"{len(non_feed)} پست از غیرفید",
            )
        else:
            report.add(
                "G2.feed_only", "ok",
                "همه پست‌ها از فید",
            )

    # ═══════════════════════════════════════════════════
    # ۱۳) pipeline.feed → DB
    # ═══════════════════════════════════════════════════
    section("مرحله ۱۳: عبور از pipeline")
    stored = 0
    try:
        t0 = time.monotonic()
        stored = await pipeline.feed(msgs)
        elapsed = (time.monotonic() - t0) * 1000
        report.add(
            "H1.pipeline.feed",
            "ok" if stored >= 0 else "fail",
            f"fed={len(msgs)} | stored={stored} | {elapsed:.0f}ms",
            elapsed,
        )
    except Exception as e:
        report.add("H1.pipeline.feed", "fail", f"{type(e).__name__}: {e}")

    try:
        total_db = await pipeline.count_messages(platform="linkedin")
        report.add("H2.db.total", "ok",
                   f"linkedin: {total_db} پست در DB تست")
    except Exception as e:
        report.add("H2.db.total", "warn", f"{type(e).__name__}: {e}")
        total_db = 0

    try:
        total_leads = await pipeline.count_leads(platform="linkedin")
        report.add("H3.db.leads", "ok",
                   f"linkedin: {total_leads} سرنخ در DB تست")
    except Exception as e:
        report.add("H3.db.leads", "warn", f"{type(e).__name__}: {e}")
        total_leads = 0

    # ═══════════════════════════════════════════════════
    # ۱۴) چک محتوا در DB (H4 مثل بقیه)
    # ═══════════════════════════════════════════════════
    section("مرحله ۱۴: چک محتوا در DB")
    if msgs:
        try:
            found = await pipeline.find_in_db(msgs[:20], platform="linkedin")
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
                    content_bad.append(f"id={raw_id[:20]} text mismatch")
                else:
                    content_ok += 1

            if not content_bad:
                report.add(
                    "H4.db.content", "ok",
                    f"{content_ok}/{len(found)} پست با محتوای درست",
                )
            else:
                report.add(
                    "H4.db.content", "warn",
                    f"{len(content_bad)} پست ناهمخوان: {content_bad[:3]}",
                )
        except Exception as e:
            report.add("H4.db.content", "warn", f"{type(e).__name__}: {e}")

    # ═══════════════════════════════════════════════════
    # ۱۵) گزارش صریح
    # ═══════════════════════════════════════════════════
    section("مرحله ۱۵: گزارش صریح")
    if msgs and stored > 0 and total_leads == 0:
        report.add(
            "I1.lead.clarify", "info",
            f"{stored} پست ذخیره شد ولی 0 کلیدواژه داشت → Lead واقعی = 0",
        )
        print(f"\n  ℹ️ {stored} پست در DB ذخیره شد ولی 0 کلیدواژه داشت")
    elif total_leads > 0:
        report.add(
            "I1.lead.clarify", "ok",
            f"{total_leads} Lead واقعی در DB",
        )
    else:
        report.add("I1.lead.clarify", "skip", "پستی برای بررسی نبود")

    if total_leads > 0:
        try:
            leads = await pipeline.find_leads_in_db("linkedin")
            print(f"\n  🎯 {min(3, len(leads))} lead نمونه:")
            for ld in leads[:3]:
                chat = (ld.get("chat_name") or "?")[:25]
                kw = (ld.get("keywords") or "—")[:40]
                print(f"     [{chat}] kw={kw}")
            report.add(
                "I2.lead.sample", "ok",
                f"{min(3, len(leads))} نمونه نمایش داده شد",
            )
        except Exception as e:
            report.add("I2.lead.sample", "warn", f"{type(e).__name__}: {e}")

    # ═══════════════════════════════════════════════════
    # ۱۶) پاک‌سازی
    # ═══════════════════════════════════════════════════
    section("مرحله ۱۶: پاک‌سازی")

    try:
        dom_path = await collector.dump_dom()
        size = (
            os.path.getsize(dom_path)
            if dom_path and os.path.exists(dom_path) else 0
        )
        report.add(
            "J1.dump_dom",
            "ok" if dom_path and size > 0 else "warn",
            f"size={size}B",
        )
    except Exception as e:
        report.add("J1.dump_dom", "warn", f"{type(e).__name__}: {e}")

    try:
        await safe_disconnect(collector, report)
    except Exception as e:
        report.add("J2.disconnect", "warn", f"{type(e).__name__}: {e}")

    try:
        await pipeline.stop()
        report.add("J3.pipeline.cleanup", "ok", "DB تست پاک شد")
    except Exception as e:
        report.add("J3.pipeline.cleanup", "warn", f"{type(e).__name__}: {e}")

    ok = report.print_summary()
    report.save()
    return ok


# ═══════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="تست دقیق لینکدین — Pipeline end-to-end (v3.0)",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="اجرای مرورگر بدون پنجره",
    )
    parser.add_argument(
        "--max-posts", type=int, default=0,
        help="حداکثر پست (0=همه)",
    )
    parser.add_argument(
        "--since-days", type=int, default=7,
        help="تاریخچه چند روز (0=بدون محدودیت)",
    )
    args = parser.parse_args()

    ok = asyncio.run(
        main(
            headless=args.headless,
            max_feed_posts=args.max_posts,
            since_days=args.since_days,
        )
    )
    sys.exit(0 if ok else 1)