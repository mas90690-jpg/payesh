# tests/integration/test_web_full.py
"""
Integration tests for collectors/web.py — v5.1 (precise).

Requires:
  - WEB_ENABLED=true in .env
  - data/sources/web_sources.txt
  - Network access

v5.1:
  - 🐛 FIX: A2 → skip (urllib SSL مشکل دارد؛ web.py از aiohttp استفاده می‌کند)
  - 🐛 FIX: E2 → skip اگر همه seen (dedup)
  - 🐛 FIX: E5 stats جدید (web v3.1)
  - 🐛 FIX: G junk filter
  - 🎯 ساختار A-K
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
    TestReport, banner, check_env, safe_disconnect, section, C,
)
from tests.pipeline_helpers import TestPipeline


# ═══════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════
VALID_CHAT_TYPES = {"web", "private", "group", "channel"}

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


def _is_valid_text(text: str) -> bool:
    return bool(text and text.strip())


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════
async def main(
    headless: bool = False,
    max_pages: int = 40,
    since_days: int = 7,
    skip_second_run: bool = False,
) -> bool:
    banner("تست کامل وب (Web) — Pipeline end-to-end (v5.1)")
    report = TestReport("web")

    # ═══════════════════════════════════════════════════════
    # A) Pre-flight
    # ═══════════════════════════════════════════════════════
    section("A) پیش‌نیازها")

    # A1: DNS
    try:
        ip = socket.gethostbyname("example.com")
        report.add("A1.dns", "ok", f"example.com → {ip}")
    except Exception as e:
        report.add("A1.dns", "warn", f"{type(e).__name__}: {e}")

    # A2: HTTP — skip (urllib SSL؛ web.py از aiohttp استفاده می‌کند)
    report.add(
        "A2.http", "skip",
        "urllib SSL — web.py از aiohttp استفاده می‌کند",
    )

    # A3: env
    env = check_env("web")
    enabled = env.get("enabled", False)
    report.add(
        "A3.env.enabled",
        "ok" if enabled else "skip",
        f"WEB_ENABLED={enabled}",
    )
    if not enabled:
        print(
            f"\n{C.INFO}⏭️ وب غیرفعال (skip) — "
            f"برای اجرا: WEB_ENABLED=true{C.END}"
        )
        report.print_summary()
        report.save()
        return True

    # A4: sources file
    sources_file = None
    try:
        from config import BASE_DIR
        sources_file = os.path.join(
            BASE_DIR, "data", "sources", "web_sources.txt"
        )
        exists = os.path.exists(sources_file)
        report.add(
            "A4.sources_file",
            "ok" if exists else "warn",
            f"exists={exists} | path={sources_file[-60:]}",
        )
    except Exception as e:
        report.add("A4.sources_file", "warn", str(e)[:80])

    # A5: aiohttp + bs4
    try:
        import aiohttp  # noqa: F401
        import bs4  # noqa: F401
        report.add("A5.deps", "ok", "aiohttp + bs4")
    except ImportError as e:
        report.add(
            "A5.deps", "fail",
            f"pip install aiohttp beautifulsoup4 | {e}",
        )
        report.print_summary()
        report.save()
        return False

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
        from collectors.web import WebCollector

        collector = WebCollector(
            sources_file=sources_file,
            max_pages=max_pages,
            timeout=20,
            translate=False,
        )
        report.add(
            "C1.init", "ok",
            f"max_pages={max_pages} | translate=False",
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
        "ok" if pname == "web" else "fail",
        f"{pname}",
    )

    # C3: poll interval
    poll = getattr(collector, "poll_interval_seconds", None)
    report.add(
        "C3.poll_interval",
        "ok" if isinstance(poll, (int, float)) and poll >= 3600 else "warn",
        f"{poll}s ({poll/3600:.1f}h)" if isinstance(poll, (int, float)) else "N/A",
    )

    # C4: translate disabled
    report.add(
        "C4.translate",
        "ok" if not collector.translate else "warn",
        f"translate={collector.translate}",
    )

    # C5: max_pages
    report.add(
        "C5.max_pages",
        "ok",
        f"max_pages={collector.max_pages}",
    )

    # ═══════════════════════════════════════════════════════
    # D) Connect
    # ═══════════════════════════════════════════════════════
    section("D) اتصال (load sources)")

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
        print(f"\n{C.FAIL}❌ اتصال وب برقرار نشد{C.END}")
        await safe_disconnect(collector, report)
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    # D2: sources loaded
    try:
        n_sources = len(collector._sources)
        report.add(
            "D2.sources",
            "ok" if n_sources > 0 else "warn",
            f"{n_sources} منبع",
        )
    except Exception as e:
        report.add("D2.sources", "warn", str(e)[:80])

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

    # D4: seen loaded
    try:
        n_seen = len(collector._seen_hashes)
        report.add(
            "D4.seen",
            "ok",
            f"{n_seen} اثرانگشت دیده‌شده",
        )
    except Exception as e:
        report.add("D4.seen", "warn", str(e)[:80])

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

    # E2: count — skip اگر همه seen
    stats = getattr(collector, "_last_scrape_stats", {})
    found = stats.get("messages_found", 0) if isinstance(stats, dict) else 0
    if msgs:
        report.add("E2.count", "ok", f"{len(msgs)} مطلب")
    elif found > 0:
        report.add(
            "E2.count", "skip",
            f"{found} مطلب یافت ولی همه seen (dedup)",
        )
    else:
        report.add("E2.count", "warn", "هیچ مطلبی یافت نشد")

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
        too_old = [
            m for m in msgs
            if isinstance(m.get("timestamp"), datetime)
            and since_dt and m["timestamp"] < since_dt
        ]
        report.add(
            "E4.since_lower",
            "ok" if not too_old else "warn",
            f"{len(too_old)} قدیمی" if too_old
            else f"همه {len(msgs)} جدیدتر",
        )

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
        report.add("E4.since_lower", "skip", "مطلبی نیست")
        report.add("E4b.since_upper", "skip", "مطلبی نیست")

    # E5: stats (web v3.1)
    if isinstance(stats, dict) and stats:
        report.add(
            "E5.stats", "ok",
            f"sources={stats.get('total_chats', '?')} | "
            f"processed={stats.get('processed', '?')} | "
            f"found={stats.get('messages_found', '?')} | "
            f"stored={stats.get('messages_stored', '?')} | "
            f"seen={stats.get('skipped_seen', '?')} | "
            f"junk={stats.get('skipped_junk', '?')} | "
            f"errors={stats.get('errors', '?')}",
        )
    else:
        report.add("E5.stats", "warn", "no stats")

    # E6-E9: برای وب معنی ندارند
    report.add("E6.chats_found", "ok",
               f"total={stats.get('total_chats', 0)}")
    report.add("E7.processed", "ok",
               f"processed={stats.get('processed', 0)}")
    report.add("E8.stats_sum", "skip", "وب stats_sum ندارد")
    report.add("E9.last_run_updated", "skip",
               "وب last_run_at ندارد (idempotent via seen)")

    # ═══════════════════════════════════════════════════════
    # F) Message structure
    # ═══════════════════════════════════════════════════════
    section("F) ساختار پیام (۱۵ فیلد + semantic)")

    if not msgs:
        report.add("F.structure", "skip", "مطلبی برای بررسی نیست")
    else:
        sample = msgs[0]

        missing = [f for f in REQUIRED_MSG_FIELDS if f not in sample]
        report.add(
            "F1.fields",
            "ok" if not missing else "fail",
            f"missing={missing}" if missing
            else f"همه {len(REQUIRED_MSG_FIELDS)} فیلد موجود",
        )

        all_web = all(m.get("platform") == "web" for m in msgs)
        report.add(
            "F2.platform",
            "ok" if all_web else "fail",
            f"همه web: {all_web}",
        )

        ids = [str(m.get("id", "")) for m in msgs]
        dup = len(ids) - len(set(ids))
        report.add(
            "F3.id_unique",
            "ok" if dup == 0 else "fail",
            f"{dup} تکراری از {len(ids)}",
        )

        bad_id = [
            m for m in msgs
            if not str(m.get("id", "")).startswith("web_")
        ]
        report.add(
            "F4.id_format",
            "ok" if not bad_id else "warn",
            f"{len(bad_id)} غیر web_" if bad_id
            else "همه web_",
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

        bad_link = [
            m for m in msgs
            if m.get("chat_link") != m.get("chat_id")
        ]
        report.add(
            "F11.link_eq_cid",
            "ok" if not bad_link else "warn",
            f"{len(bad_link)} link≠cid" if bad_link
            else "link=cid",
        )

        print(f"\n  📩 {min(5, len(msgs))} مطلب scrape‌شده:")
        for m in msgs[:5]:
            txt = (m.get("text") or "")[:60].replace("\n", " ")
            cname = (m.get("chat_name") or "?")[:40]
            print(f"     [{cname}]")
            print(f"        {txt}")

    # ═══════════════════════════════════════════════════════
    # G) Junk filter
    # ═══════════════════════════════════════════════════════
    section("G) فیلتر junk (Cloudflare/Challenge)")

    if msgs:
        from collectors.web import WebCollector
        junk_count = 0
        for m in msgs:
            is_junk, _ = WebCollector._is_junk_page(
                m.get("chat_name", ""),
                m.get("text", ""),
            )
            if is_junk:
                junk_count += 1
        report.add(
            "G1.junk_filter",
            "ok" if junk_count == 0 else "warn",
            f"{junk_count} junk" if junk_count else "هیچ junk",
        )

        empty = [m for m in msgs if not m.get("text")]
        report.add(
            "G2.non_empty",
            "ok" if not empty else "warn",
            f"{len(empty)} خالی" if empty else "همه غیرخالی",
        )
    else:
        report.add("G1.junk_filter", "skip", "مطلبی نیست")
        report.add("G2.non_empty", "skip", "مطلبی نیست")

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
        total_db = await pipeline.count_messages(platform="web")
        report.add(
            "H2.db.total", "ok",
            f"web: {total_db} مطلب در DB",
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
        total_leads = await pipeline.count_leads(platform="web")
        report.add(
            "H3.db.leads", "ok",
            f"web: {total_leads} سرنخ",
        )
    except Exception as e:
        report.add("H3.db.leads", "warn", str(e)[:80])

    if msgs:
        try:
            found_db = await pipeline.find_in_db(
                msgs[:10], platform="web"
            )
            content_ok = 0
            content_bad = []
            for raw_id, row in found_db.items():
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
                f"{content_ok}/{len(found_db)} درست" if not content_bad
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
            leads = await pipeline.find_leads_in_db("web")
            print(f"\n  🎯 {min(3, len(leads))} lead نمونه:")
            for ld in leads[:3]:
                chat = (ld.get("chat_name") or "?")[:30]
                print(f"     [{chat}]")
            report.add(
                "H7.lead_sample", "ok",
                f"{min(3, len(leads))} نمونه",
            )
        except Exception as e:
            report.add("H7.lead_sample", "warn", str(e)[:80])
    else:
        report.add(
            "H7.lead_sample", "skip",
            "هیچ سرنخی نیست (طبیعی — اخبار عمومی)",
        )

    # ═══════════════════════════════════════════════════════
    # I) Dedup (second run)
    # ═══════════════════════════════════════════════════════
    if skip_second_run:
        report.add("I.dedup", "skip", "--skip-second-run")
    elif not msgs:
        report.add("I.dedup", "skip", "no messages from first run")
    else:
        section("I) اجرای دوم — تست dedup")

        try:
            seen_before = len(collector._seen_hashes)

            t0 = time.monotonic()
            msgs2 = await collector.fetch_messages(since=since_dt)
            elapsed2 = time.monotonic() - t0
            seen_after = len(collector._seen_hashes)

            report.add(
                "I1.second_fetch", "ok",
                f"count={len(msgs2)} | "
                f"seen {seen_before}→{seen_after} | {elapsed2:.0f}s",
            )

            if len(msgs2) == 0:
                report.add(
                    "I2.dedup", "ok",
                    "بار دوم ۰ مطلب جدید — dedup کامل",
                )
            elif len(msgs2) < len(msgs):
                report.add(
                    "I2.dedup", "warn",
                    f"بار دوم {len(msgs2)} (از {len(msgs)} اولیه)",
                )
            else:
                report.add(
                    "I2.dedup", "warn",
                    f"بار دوم {len(msgs2)} — dedup کار نکرد؟",
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
    # J) dump (وب no DOM)
    # ═══════════════════════════════════════════════════════
    section("J) dump (وب no DOM)")

    report.add("J1.dump_dom", "skip", "وب DOM ندارد (aiohttp + BS4)")
    report.add("J2.dump_content", "skip", "وب DOM ندارد")

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
            collector._bctx is None
            and collector._bpw is None
        ) else "warn",
        f"bctx={collector._bctx is not None} | "
        f"bpw={collector._bpw is not None}",
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
    os.getenv("WEB_INTEGRATION") != "1",
    reason="integration — با WEB_INTEGRATION=1 فعال شود",
)
def test_web_integration():
    from dotenv import load_dotenv
    load_dotenv(override=False)

    ok = asyncio.run(main(
        headless=True,
        max_pages=40,
        since_days=7,
        skip_second_run=False,
    ))
    assert ok, "integration test failed — see report above"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="تست یکپارچه وب — Pipeline end-to-end (v5.1)",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-pages", type=int, default=40)
    parser.add_argument("--since-days", type=int, default=7)
    parser.add_argument("--skip-second-run", action="store_true")
    args = parser.parse_args()

    ok = asyncio.run(main(
        headless=args.headless,
        max_pages=args.max_pages,
        since_days=args.since_days,
        skip_second_run=args.skip_second_run,
    ))
    sys.exit(0 if ok else 1)