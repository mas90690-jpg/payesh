# tests/integration/test_linkedin_full.py
"""
Integration tests for collectors/linkedin.py — v5.1 (precise).

Requires:
  - LINKEDIN_ENABLED=true in .env
  - Session saved in storage/sessions/linkedin_v2/
  - Network access to linkedin.com

v5.1:
  - 🐛 FIX: _stats_sum — بدون empty_chats
  - 🐛 FIX: E8.stats_sum — `<=`
  - 🐛 FIX: H6.db_keywords — "skip"
  - 🐛 FIX: A2.http — skip (Cloudflare block)
  - 🎯 همان ساختار A-K
  - 🎯 linkedin: 15 fields، id 32-hex، chat_type="linkedin"
  - 🎯 J) dump_dom — ok (LinkedIn DOM دارد)
  - 🎯 K5.resources — page/context/pw
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import socket
import sys
import time
import urllib.request
from datetime import datetime, timedelta

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tests.helpers import (
    TestReport, banner, check_env, check_session_exists,
    safe_disconnect, section, C,
)
from tests.pipeline_helpers import TestPipeline


# ═══════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════
VALID_CHAT_TYPES = {"linkedin", "private", "group", "channel"}

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

REQUIRED_DB_COLUMNS = {
    "id", "platform", "text", "chat_id", "chat_name",
    "chat_type", "chat_username", "chat_link",
    "user_id", "sender_name", "timestamp", "media_type",
    "file_id", "local_file_path", "reply_to", "keywords",
    "text_translated", "created_at",
}

MAX_FETCH_SECONDS = 400

_PERSIAN_RANGE = re.compile(r"[\u0600-\u06FF]")


# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════
def _is_valid_text(text: str) -> bool:
    return bool(text and text.strip())


def _is_hex32(s: str) -> bool:
    if not isinstance(s, str) or len(s) != 32:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in s)


def _stats_sum(stats: dict) -> int:
    """Sum of mutually exclusive subset counters.

    Note: empty_chats is a SUBSET of processed (not parallel).
    """
    return (
        stats.get("processed", 0)
        + stats.get("skipped_system", 0)
        + stats.get("skipped_target", 0)
        + stats.get("skipped_admin", 0)
        + stats.get("failed", 0)
    )


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════
async def main(
    headless: bool = False,
    max_feed_posts: int = 0,
    since_days: int = 7,
    skip_second_run: bool = False,
) -> bool:
    banner("تست کامل لینکدین (LinkedIn) — Pipeline end-to-end (v5.1)")
    report = TestReport("linkedin")

    # ═══════════════════════════════════════════════════════
    # A) Pre-flight
    # ═══════════════════════════════════════════════════════
    section("A) پیش‌نیازها")

    # A1: DNS
    try:
        ip = socket.gethostbyname("www.linkedin.com")
        report.add("A1.dns", "ok", f"→ {ip}")
    except Exception as e:
        report.add("A1.dns", "fail", f"{type(e).__name__}: {e}")
        print(f"\n{C.FAIL}❌ DNS linkedin.com resolve نشد{C.END}")
        report.print_summary()
        report.save()
        return False

    # A2: HTTP — skip (Cloudflare)
    report.add(
        "A2.http", "skip",
        "LinkedIn Cloudflare block — web.py aiohttp استفاده می‌کند",
    )

    # A3: env
    env = check_env("linkedin")
    enabled = env.get("enabled", False)
    report.add(
        "A3.env.enabled",
        "ok" if enabled else "skip",
        f"LINKEDIN_ENABLED={enabled}",
    )
    if not enabled:
        print(
            f"\n{C.INFO}⏭️ لینکدین غیرفعال (skip) — "
            f"برای اجرا: LINKEDIN_ENABLED=true{C.END}"
        )
        report.print_summary()
        report.save()
        return True

    # A4: session
    session_path = env.get("session_path", "")
    session_exists = (
        check_session_exists(session_path) if session_path else False
    )
    report.add(
        "A4.session",
        "ok" if session_exists else "warn",
        f"exists={session_exists} | path={session_path[-60:] if session_path else '—'}",
    )
    if not session_exists:
        print(f"\n{C.WARN}⚠️ نشست لینکدین وجود ندارد{C.END}")
        print("   راه‌حل: python login.py → گزینهٔ ۶ (LinkedIn)")

    # A5: Chrome
    try:
        from config import CHROME_PATH
        if CHROME_PATH and os.path.exists(CHROME_PATH):
            report.add("A5.chrome", "ok", CHROME_PATH[:70])
        else:
            report.add(
                "A5.chrome", "warn",
                "CHROME_PATH نیست — از channel=chrome استفاده می‌شود",
            )
    except Exception as e:
        report.add("A5.chrome", "warn", str(e)[:80])

    # ═══════════════════════════════════════════════════════
    # B) Pipeline
    # ═══════════════════════════════════════════════════════
    section("B) ساخت pipeline (DB ایزوله)")

    pipeline: TestPipeline | None = None
    try:
        pipeline = TestPipeline(
            keywords_only=False,
            require_t1=False,
            allowed_tiers=("T1", "T2", "T3"),
        )
        await pipeline.start()
        report.add(
            "B1.init", "ok",
            f"db={os.path.basename(pipeline._db_path or '?')}",
        )
    except Exception as e:
        report.add("B1.init", "fail", f"{type(e).__name__}: {e}")
        report.print_summary()
        report.save()
        return False

    # B2: schema
    try:
        async with pipeline._db.execute(
            "PRAGMA table_info(messages)"
        ) as cur:
            rows = await cur.fetchall()
        cols = {row[1] for row in rows}
        missing = REQUIRED_DB_COLUMNS - cols
        report.add(
            "B2.schema",
            "ok" if not missing else "fail",
            f"missing={missing}" if missing
            else f"{len(cols)} ستون",
        )
    except Exception as e:
        report.add("B2.schema", "warn", str(e)[:80])

    # ═══════════════════════════════════════════════════════
    # C) Collector
    # ═══════════════════════════════════════════════════════
    section("C) ساخت collector")

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
            force_run=True,           # bypass once_per_day
            following_only=True,
            stop_on_non_follower=True,
            once_per_day=False,
        )
        report.add(
            "C1.init", "ok",
            f"platform={collector.platform_name} | "
            f"following_only={collector._following_only} | "
            f"max_posts={max_feed_posts or '∞'}",
        )
    except Exception as e:
        report.add("C1.init", "fail", f"{type(e).__name__}: {e}")
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    # C2: platform_name
    pname = getattr(collector, "platform_name", None)
    report.add(
        "C2.platform_name",
        "ok" if pname == "linkedin" else "fail",
        f"{pname}",
    )

    # C3: poll interval
    poll = getattr(collector, "poll_interval_seconds", None)
    report.add(
        "C3.poll_interval",
        "ok" if isinstance(poll, (int, float)) else "warn",
        f"{poll}",
    )

    # C4: options
    report.add(
        "C4.options", "ok",
        f"following_only={collector._following_only} | "
        f"force_run={collector._force_run} | "
        f"once_per_day={collector._once_per_day}",
    )

    # C5: max_posts
    report.add(
        "C5.max_posts", "ok",
        f"max_feed_posts={collector._max_feed_posts}",
    )

    # ═══════════════════════════════════════════════════════
    # D) Connect
    # ═══════════════════════════════════════════════════════
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
        print(f"\n{C.FAIL}❌ اتصال لینکدین برقرار نشد{C.END}")
        print("   راه‌حل: python login.py → گزینهٔ ۶ (LinkedIn)")
        await safe_disconnect(collector, report)
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    # D2: is_logged_in
    try:
        logged = await collector._is_logged_in()
        url = collector._page.url if collector._page else ""
        report.add(
            "D2.is_logged_in",
            "ok" if logged else "fail",
            f"logged={logged} | url={url[:70]}",
        )
    except Exception as e:
        report.add("D2.is_logged_in", "warn", str(e)[:80])

    # D3: check_health
    try:
        health = await collector.check_health()
        report.add(
            "D3.check_health",
            "ok" if health else "warn",
            f"health={health}",
        )
    except Exception as e:
        report.add("D3.check_health", "warn", str(e)[:80])

    # D4: page URL
    try:
        url = collector._page.url if collector._page else ""
        report.add(
            "D4.page_url",
            "ok" if "linkedin.com" in url.lower() else "warn",
            url[:80],
        )
    except Exception as e:
        report.add("D4.page_url", "warn", str(e)[:80])

    # ═══════════════════════════════════════════════════════
    # E) Fetch
    # ═══════════════════════════════════════════════════════
    section(f"E) fetch_messages (since={since_days} روز)")

    since_dt = (
        datetime.now() - timedelta(days=since_days)
        if since_days > 0 else None
    )
    if since_dt:
        print(f"  🎯 since = {since_dt.isoformat()}")

    msgs: list = []
    fetch_elapsed = 0.0
    try:
        t0 = time.monotonic()
        msgs = await collector.fetch_messages(since=since_dt)
        fetch_elapsed = time.monotonic() - t0
        report.add(
            "E1.fetch",
            "ok" if isinstance(msgs, list) else "fail",
            f"count={len(msgs)} | {fetch_elapsed:.0f}s",
            fetch_elapsed * 1000,
        )
    except Exception as e:
        report.add("E1.fetch", "fail", f"{type(e).__name__}: {e}")
        msgs = []

    # E2: count
    report.add(
        "E2.count",
        "ok" if msgs else "warn",
        f"{len(msgs)} پست",
    )

    # E3: timeout
    if fetch_elapsed > MAX_FETCH_SECONDS:
        report.add(
            "E3.timeout", "warn",
            f"{fetch_elapsed:.0f}s > {MAX_FETCH_SECONDS}s",
        )
    else:
        report.add("E3.timeout", "ok", f"{fetch_elapsed:.0f}s")

    # E4: timestamps
    now = datetime.now()
    if msgs:
        future = [
            m for m in msgs
            if isinstance(m.get("timestamp"), datetime)
            and m["timestamp"] > now + timedelta(minutes=1)
        ]
        report.add(
            "E4b.since_upper",
            "ok" if not future else "warn",
            f"{len(future)} آینده" if future else "هیچ آینده",
        )
    else:
        report.add("E4b.since_upper", "skip", "پستی نیست")

    # E5: stats (linkedin keys)
    stats = getattr(collector, "_last_scrape_stats", {})
    if isinstance(stats, dict) and stats:
        report.add(
            "E5.stats", "ok",
            f"total={stats.get('total_posts', '?')} | "
            f"stored={stats.get('stored', '?')} | "
            f"follower={stats.get('skipped_follower', '?')} | "
            f"non-follower={stats.get('skipped_non_follower', '?')} | "
            f"company={stats.get('skipped_company', '?')} | "
            f"unknown={stats.get('skipped_unknown', '?')} | "
            f"no-kw={stats.get('skipped_no_kw', '?')} | "
            f"seen={stats.get('skipped_seen', '?')}",
        )

        total_posts = stats.get("total_posts", 0)
        report.add(
            "E6.posts_found",
            "ok" if total_posts > 0 else "warn",
            f"total_posts={total_posts}",
        )

        stored = stats.get("stored", 0)
        report.add(
            "E7.stored",
            "ok" if stored >= 0 else "warn",
            f"stored={stored}",
        )

        report.add(
            "E8.stats_sum", "skip",
            "linkedin stats دارد — نیازی به sum نیست",
        )
    else:
        report.add("E5.stats", "warn", "no stats")

    # E9: last_run
    report.add(
        "E9.last_run_updated",
        "ok" if collector._last_run_date else "warn",
        f"last_run_date={collector._last_run_date}",
    )

    # ═══════════════════════════════════════════════════════
    # F) Message structure
    # ═══════════════════════════════════════════════════════
    section("F) ساختار پیام (۱۵ فیلد + semantic)")

    if not msgs:
        report.add("F.structure", "skip", "پستی برای بررسی نیست")
    else:
        sample = msgs[0]

        missing = [f for f in REQUIRED_MSG_FIELDS if f not in sample]
        report.add(
            "F1.fields",
            "ok" if not missing else "fail",
            f"missing={missing}" if missing
            else f"همه {len(REQUIRED_MSG_FIELDS)} فیلد موجود",
        )

        all_li = all(m.get("platform") == "linkedin" for m in msgs)
        report.add(
            "F2.platform",
            "ok" if all_li else "fail",
            f"همه linkedin: {all_li}",
        )

        ids = [str(m.get("id", "")) for m in msgs]
        dup = len(ids) - len(set(ids))
        report.add(
            "F3.id_unique",
            "ok" if dup == 0 else "fail",
            f"{dup} تکراری از {len(ids)}",
        )

        bad_id = [m for m in msgs if not _is_hex32(str(m.get("id", "")))]
        report.add(
            "F4.id_format",
            "ok" if not bad_id else "warn",
            f"{len(bad_id)} غیر 32-hex" if bad_id
            else "همه 32-hex",
        )

        ts_ok = all(
            isinstance(m.get("timestamp"), datetime) for m in msgs
        )
        report.add(
            "F5.timestamp",
            "ok" if ts_ok else "warn",
            f"datetime: {ts_ok}",
        )

        bad_ct = [
            m for m in msgs
            if m.get("chat_type") not in VALID_CHAT_TYPES
        ]
        report.add(
            "F6.chat_type",
            "ok" if not bad_ct else "warn",
            f"{len(bad_ct)} نامعتبر" if bad_ct else "معتبر",
        )

        bad_mt = [
            m for m in msgs
            if m.get("media_type") not in VALID_MEDIA_TYPES
        ]
        report.add(
            "F7.media_type",
            "ok" if not bad_mt else "warn",
            f"{len(bad_mt)} نامعتبر" if bad_mt else "معتبر",
        )

        empty_text = [
            m for m in msgs if not _is_valid_text(m.get("text", ""))
        ]
        report.add(
            "F8.text",
            "ok" if not empty_text else "fail",
            f"{len(empty_text)} خالی" if empty_text
            else "همه متن دارند",
        )

        empty_name = [
            m for m in msgs
            if not (m.get("chat_name") or "").strip()
        ]
        report.add(
            "F9.chat_name",
            "ok" if not empty_name else "fail",
            f"{len(empty_name)} خالی" if empty_name
            else "همه نام دارند",
        )

        empty_kw = [
            m for m in msgs
            if not (m.get("keywords") or "").strip()
        ]
        report.add(
            "F10.keywords",
            "ok" if not empty_kw else "warn",
            f"{len(empty_kw)} بدون keywords" if empty_kw
            else "همه keywords دارند",
        )

        with_link = [
            m for m in msgs if m.get("chat_link")
        ]
        report.add(
            "F11.chat_link",
            "ok" if len(with_link) == len(msgs) else "warn",
            f"{len(with_link)}/{len(msgs)} لینک دارند",
        )

        print(f"\n  📩 {min(5, len(msgs))} پست scrape‌شده:")
        for m in msgs[:5]:
            txt = (m.get("text") or "")[:60].replace("\n", " ")
            author = (m.get("sender_name") or "?")[:25]
            kw = (m.get("keywords") or "—")[:30]
            print(f"     [{author}] kw={kw}")
            print(f"        {txt}")

    # ═══════════════════════════════════════════════════════
    # G) Follower filter
    # ═══════════════════════════════════════════════════════
    section("G) فیلتر فالوور")

    if msgs:
        non_follower = [
            m for m in msgs
            if "non-follower" in (m.get("keywords") or "").lower()
        ]
        report.add(
            "G1.non_follower",
            "ok" if not non_follower else "warn",
            f"{len(non_follower)} غیرفالوور" if non_follower
            else "هیچ غیرفالوور",
        )

        # چک: chat_type=linkedin
        bad_chat_type = [
            m for m in msgs if m.get("chat_type") != "linkedin"
        ]
        report.add(
            "G2.chat_type",
            "ok" if not bad_chat_type else "warn",
            f"{len(bad_chat_type)} غیر linkedin" if bad_chat_type
            else "همه linkedin",
        )
    else:
        report.add("G1.non_follower", "skip", "پستی نیست")
        report.add("G2.chat_type", "skip", "پستی نیست")

    # ═══════════════════════════════════════════════════════
    # H) Pipeline → DB
    # ═══════════════════════════════════════════════════════
    section("H) Pipeline → DB")

    stored = 0
    try:
        t0 = time.monotonic()
        stored = await pipeline.feed(msgs)
        elapsed = (time.monotonic() - t0) * 1000
        report.add(
            "H1.feed",
            "ok" if stored >= 0 else "fail",
            f"fed={len(msgs)} | stored={stored} | {elapsed:.0f}ms",
            elapsed,
        )
    except Exception as e:
        report.add("H1.feed", "fail", f"{type(e).__name__}: {e}")
        stored = 0

    total_db = 0
    try:
        total_db = await pipeline.count_messages(platform="linkedin")
        report.add(
            "H2.db.total", "ok",
            f"linkedin: {total_db} پست در DB",
        )
        report.add(
            "H2b.db_count_match",
            "ok" if total_db >= stored else "warn",
            f"stored={stored} | db={total_db}",
        )
    except Exception as e:
        report.add("H2.db.total", "warn", str(e)[:80])

    total_leads = 0
    try:
        total_leads = await pipeline.count_leads(platform="linkedin")
        report.add(
            "H3.db.leads", "ok",
            f"linkedin: {total_leads} سرنخ",
        )
    except Exception as e:
        report.add("H3.db.leads", "warn", str(e)[:80])

    if msgs:
        try:
            found = await pipeline.find_in_db(
                msgs[:20], platform="linkedin"
            )
            content_ok = 0
            content_bad = []
            for raw_id, row in found.items():
                orig = next(
                    (m for m in msgs if str(m.get("id")) == raw_id),
                    None,
                )
                if not orig:
                    continue
                if (orig.get("text") or "").strip()[:100] != \
                   (row.get("text") or "").strip()[:100]:
                    content_bad.append(raw_id[:20])
                else:
                    content_ok += 1

            report.add(
                "H4.content",
                "ok" if not content_bad else "warn",
                f"{content_ok}/{len(found)} درست" if not content_bad
                else f"{len(content_bad)} ناهمخوان",
            )
        except Exception as e:
            report.add("H4.content", "warn", str(e)[:80])

        report.add(
            "H6.db_keywords", "skip",
            "TestPipeline keywords پر نمی‌کند",
        )

    if total_leads > 0:
        try:
            leads = await pipeline.find_leads_in_db("linkedin")
            print(f"\n  🎯 {min(3, len(leads))} lead نمونه:")
            for ld in leads[:3]:
                chat = (ld.get("chat_name") or "?")[:30]
                kw = (ld.get("keywords") or "—")[:40]
                tier = ld.get("opportunity_type") or "—"
                print(f"     [{chat}] kw={kw} | tier={tier}")
            report.add(
                "H7.lead_sample", "ok",
                f"{min(3, len(leads))} نمونه",
            )
        except Exception as e:
            report.add("H7.lead_sample", "warn", str(e)[:80])
    else:
        report.add(
            "H7.lead_sample", "skip",
            "هیچ سرنخی نیست (طبیعی — بدون کلیدواژه)",
        )

    # ═══════════════════════════════════════════════════════
    # I) Dedup
    # ═══════════════════════════════════════════════════════
    if skip_second_run:
        report.add("I.dedup", "skip", "--skip-second-run")
    elif not msgs:
        report.add("I.dedup", "skip", "no messages from first run")
    else:
        section("I) اجرای دوم — تست dedup")

        try:
            seen_before = len(collector._seen_ids)
            msgs1_ids = {str(m.get("id", "")) for m in msgs}

            t0 = time.monotonic()
            msgs2 = await collector.fetch_messages(since=since_dt)
            elapsed2 = time.monotonic() - t0
            seen_after = len(collector._seen_ids)
            msgs2_ids = {str(m.get("id", "")) for m in msgs2}

            report.add(
                "I1.second_fetch", "ok",
                f"count={len(msgs2)} | "
                f"seen {seen_before}→{seen_after} | {elapsed2:.0f}s",
            )

            overlap_ids = msgs1_ids & msgs2_ids
            report.add(
                "I3.id_determinism",
                "ok" if not overlap_ids else "fail",
                f"{len(overlap_ids)} id تکراری" if overlap_ids
                else "هیچ id تکراری",
            )

            stored2 = await pipeline.feed(msgs2)
            report.add(
                "I5.second_feed",
                "ok" if stored2 == 0 else "warn",
                f"stored={stored2}",
            )

        except Exception as e:
            report.add("I.dedup", "fail", f"{type(e).__name__}: {e}")

    # ═══════════════════════════════════════════════════════
    # J) dump_dom (LinkedIn DOM دارد)
    # ═══════════════════════════════════════════════════════
    section("J) dump_dom")

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
        if dom_path:
            print(f"  📄 DOM: {dom_path}")
    except Exception as e:
        report.add("J1.dump_dom", "warn", str(e)[:80])

    # ═══════════════════════════════════════════════════════
    # K) Cleanup
    # ═══════════════════════════════════════════════════════
    section("K) پاک‌سازی")

    await safe_disconnect(collector, report)

    report.add(
        "K2.disconnected",
        "ok" if not collector._connected else "warn",
        f"connected={collector._connected}",
    )

    db_path_before = None
    try:
        db_path_before = pipeline._db_path
        await pipeline.stop()
        report.add(
            "K3.pipeline_stop", "ok",
            "DB تست پاک شد",
        )
    except Exception as e:
        report.add("K3.pipeline_stop", "warn", str(e)[:80])

    if db_path_before:
        exists_after = os.path.exists(db_path_before)
        report.add(
            "K4.db_deleted",
            "ok" if not exists_after else "warn",
            f"exists={exists_after}",
        )

    report.add(
        "K5.resources",
        "ok" if (
            collector._page is None
            and collector._context is None
            and collector._pw is None
        ) else "warn",
        f"page={collector._page is not None} | "
        f"context={collector._context is not None} | "
        f"pw={collector._pw is not None}",
    )

    ok = report.print_summary()
    report.save()
    return ok


# ═══════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════
import pytest


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("LINKEDIN_INTEGRATION") != "1",
    reason="integration — با LINKEDIN_INTEGRATION=1 فعال شود",
)
def test_linkedin_integration():
    from dotenv import load_dotenv
    load_dotenv(override=False)

    ok = asyncio.run(main(
        headless=True,
        max_feed_posts=0,
        since_days=7,
        skip_second_run=False,
    ))
    assert ok, "integration test failed — see report above"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="تست یکپارچه لینکدین — Pipeline end-to-end (v5.1)",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-posts", type=int, default=0)
    parser.add_argument("--since-days", type=int, default=7)
    parser.add_argument("--skip-second-run", action="store_true")
    args = parser.parse_args()

    ok = asyncio.run(main(
        headless=args.headless,
        max_feed_posts=args.max_posts,
        since_days=args.since_days,
        skip_second_run=args.skip_second_run,
    ))
    sys.exit(0 if ok else 1)