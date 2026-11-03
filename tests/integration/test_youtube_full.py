# tests/integration/test_youtube_full.py
"""
Integration tests for collectors/youtube.py — v6.0 (Pipeline end-to-end)

Requires:
  - YOUTUBE_ENABLED=true in .env
  - Session saved in storage/sessions/youtube_web/
  - Network access to youtube.com
  - اکانت گوگل حداقل به یک کانال subscribe باشد

تغییرات v6.0:
  - 🎯 همان ساختار A-K مثل بقیهٔ پلتفرم‌ها
  - 🎯 _stats_sum — total_in_feed / new_videos / processed / stored / ...
  - 🎯 E8 stats_sum: total_in_feed >= (new_videos + skipped_seen)
  - 🎯 F1: 15 فیلد اجباری
  - 🎯 F4: id = video_id (len < 32) → "warn" نه "fail"
  - 🎯 F7: media_type = "video" برای همه
  - 🎯 F10: chat_type = "youtube" (نه group/private)
  - 🎯 G: فیلتر سیستمی (YouTube: کانال‌های سیستمی)
  - 🎯 H: Pipeline → DB
  - 🎯 I: dedup (بار دوم ۰ ویدیو)
  - 🎯 J: dump_dom (youtube.com DOM دارد)
  - 🎯 K: cleanup
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

# Add project root to sys.path
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
VALID_CHAT_TYPES = {"youtube"}

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

# کانال‌های سیستمی که باید skip شوند
SYSTEM_CHAT_NAMES = {
    "YouTube", "youtube",
    "YouTube Music", "YouTube Premium",
    "یوتیوب", "تیم یوتیوب",
}

SYSTEM_PREFIXES = (
    "Channel created", "کانال ایجاد",
)

MAX_FETCH_SECONDS = 400

# Persian/Arabic Unicode range
_PERSIAN_RANGE = re.compile(r"[\u0600-\u06FF]")

# YouTube video_id: 11 chars, [A-Za-z0-9_-]
_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,32}$")


# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════
def _is_system_chat(name: str) -> bool:
    if not name:
        return False
    n = name.strip()
    if not n:
        return False
    if n in SYSTEM_CHAT_NAMES:
        return True
    return any(n.startswith(p) for p in SYSTEM_PREFIXES)


def _is_valid_text(text: str) -> bool:
    return bool(text and text.strip())


def _is_video_id(s: str) -> bool:
    """YouTube video_id معتبر است؟ (11 کاراکتر معمولاً)"""
    if not isinstance(s, str):
        return False
    return bool(_YT_ID_RE.match(s))


def _stats_sum(stats: dict) -> int:
    """مجموع شمارنده‌های یوتیوب.

    total_in_feed = new_videos + skipped_seen
    new_videos    = processed (یا skipped_old / skipped_no_kw / skipped_invalid / stored)
    """
    return (
        stats.get("new_videos", 0)
        + stats.get("skipped_seen", 0)
    )


def _fmt_ts(ts) -> str:
    if isinstance(ts, datetime):
        return ts.strftime("%Y-%m-%d %H:%M")
    return str(ts)


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════
async def main(
    headless: bool = False,
    max_videos: int = 0,
    since_days: int = 7,
    skip_second_run: bool = False,
    force_no_since: bool = False,
) -> bool:
    banner("تست کامل یوتیوب (YouTube) — Pipeline end-to-end (v6.0)")
    report = TestReport("youtube")

    # ═══════════════════════════════════════════════════════
    # A) Pre-flight
    # ═══════════════════════════════════════════════════════
    section("A) پیش‌نیازها")

    # A1: DNS
    try:
        ip = socket.gethostbyname("www.youtube.com")
        report.add("A1.dns", "ok", f"→ {ip}")
    except Exception as e:
        report.add("A1.dns", "fail", f"{type(e).__name__}: {e}")
        print(f"\n{C.FAIL}❌ DNS youtube.com resolve نشد{C.END}")
        print("   راه‌حل: VPN/اینترنت را چک کن")
        report.print_summary()
        report.save()
        return False

    # A2: HTTP — skip (YouTube با Playwright خوانده می‌شود)
    report.add(
        "A2.http", "skip",
        "youtube.com — Playwright استفاده می‌کند",
    )

    # A3: env
    env = check_env("youtube")
    enabled = env.get("enabled", False)
    report.add(
        "A3.env.enabled",
        "ok" if enabled else "skip",
        f"YOUTUBE_ENABLED={enabled}",
    )
    if not enabled:
        print(
            f"\n{C.INFO}⏭️ یوتیوب غیرفعال (skip) — "
            f"برای اجرا: YOUTUBE_ENABLED=true{C.END}"
        )
        report.print_summary()
        report.save()
        return True

    # A4: session (mapped to youtube_web via helpers.py)
    session_path = env.get("session_path", "")
    session_exists = (
        check_session_exists(session_path) if session_path else False
    )
    report.add(
        "A4.session",
        "ok" if session_exists else "warn",
        f"exists={session_exists} | "
        f"path={session_path[-60:] if session_path else '—'}",
    )
    if not session_exists:
        print(f"\n{C.WARN}⚠️ نشست یوتیوب وجود ندارد{C.END}")
        print("   راه‌حل: python login.py → گزینهٔ ۸ (YouTube)")

    # A5: Chrome
    try:
        from config import CHROME_PATH
        if CHROME_PATH and os.path.exists(CHROME_PATH):
            report.add("A5.chrome", "ok", CHROME_PATH[:70])
        else:
            report.add(
                "A5.chrome", "warn",
                "CHROME_PATH نیست — channel=chrome",
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

    # B2: schema check
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
        from collectors.youtube import YouTubeCollector

        _mv = int(os.getenv("YOUTUBE_MAX_VIDEOS", "20"))
        actual_max = max_videos if max_videos > 0 else _mv
        _scrolls = int(os.getenv("YOUTUBE_SCROLL_ROUNDS", "6"))
        _delay = float(os.getenv("YOUTUBE_DELAY", "2.0"))

        collector = YouTubeCollector(
            headless=headless,
            poll_seconds=999,
            max_videos=actual_max,
            scroll_rounds=_scrolls,
            delay_between=_delay,
        )
        report.add(
            "C1.init", "ok",
            f"max_videos={actual_max} | "
            f"scroll_rounds={_scrolls} | "
            f"delay={_delay}",
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
        "ok" if pname == "youtube" else "fail",
        f"{pname}",
    )

    # C3: poll interval
    poll = getattr(collector, "poll_interval_seconds", None)
    report.add(
        "C3.poll_interval",
        "ok" if isinstance(poll, (int, float)) else "warn",
        f"{poll}",
    )

    # C4: first_run
    report.add(
        "C4.first_run",
        "ok",
        f"done={collector._first_run_done} | "
        f"last_run={collector._last_run_at}",
    )

    # C5: stats initialized
    report.add(
        "C5.stats_init",
        "ok" if isinstance(collector._last_scrape_stats, dict) else "warn",
        f"keys={list(collector._last_scrape_stats.keys())[:3]}...",
    )

    # ═══════════════════════════════════════════════════════
    # D) Connect
    # ═══════════════════════════════════════════════════════
    section("D) اتصال")

    last_run_before = collector._last_run_at

    # D1: connect
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
        print(f"\n{C.FAIL}❌ اتصال یوتیوب برقرار نشد{C.END}")
        print("   راه‌حل: python login.py → گزینهٔ ۸ (YouTube)")
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
            "ok" if "youtube.com" in url.lower() else "warn",
            url[:80],
        )
    except Exception as e:
        report.add("D4.page_url", "warn", str(e)[:80])

    # ═══════════════════════════════════════════════════════
    # E) Fetch
    # ═══════════════════════════════════════════════════════
    # 🎯 تعیین since:
    #   - force_no_since=True → None
    #   - since_days=0 → None
    #   - since_days>0 → now - since_days
    if force_no_since or since_days <= 0:
        since_dt = None
        section("E) fetch_messages (بدون since — تاریخچه کامل)")
    else:
        since_dt = datetime.now() - timedelta(days=since_days)
        section(f"E) fetch_messages (since={since_days} روز)")
        print(f"  🎯 since = {since_dt.isoformat()}")

    msgs: list = []
    fetch_elapsed = 0.0
    try:
        t0 = time.monotonic()
        msgs = await collector.fetch_messages(
            since=since_dt,
            force_no_since=force_no_since or since_days <= 0,
        )
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
        f"{len(msgs)} ویدیو",
    )

    # E3: timeout
    if fetch_elapsed > MAX_FETCH_SECONDS:
        report.add(
            "E3.timeout", "warn",
            f"{fetch_elapsed:.0f}s > {MAX_FETCH_SECONDS}s",
        )
    else:
        report.add("E3.timeout", "ok", f"{fetch_elapsed:.0f}s")

    # E4: since honored (lower bound)
    now = datetime.now()
    if msgs and since_dt:
        too_old = [
            m for m in msgs
            if isinstance(m.get("timestamp"), datetime)
            and m["timestamp"] < since_dt
        ]
        report.add(
            "E4.since_lower",
            "ok" if not too_old else "warn",
            f"{len(too_old)} قدیمی" if too_old
            else f"همه {len(msgs)} جدیدتر",
        )

        # E4b: timestamp upper bound (no future)
        future = [
            m for m in msgs
            if isinstance(m.get("timestamp"), datetime)
            and m["timestamp"] > now + timedelta(minutes=1)
        ]
        report.add(
            "E4b.since_upper",
            "ok" if not future else "warn",
            f"{len(future)} با timestamp آینده" if future
            else "هیچ timestamp آینده",
        )
    else:
        report.add("E4.since_lower", "skip", "پیامی نیست")
        report.add("E4b.since_upper", "skip", "پیامی نیست")

    # E5: stats
    stats = getattr(collector, "_last_scrape_stats", {})
    if isinstance(stats, dict) and stats:
        report.add(
            "E5.stats", "ok",
            f"total={stats.get('total_in_feed', '?')} | "
            f"new={stats.get('new_videos', '?')} | "
            f"processed={stats.get('processed', '?')} | "
            f"stored={stats.get('stored', '?')} | "
            f"seen={stats.get('skipped_seen', '?')} | "
            f"old={stats.get('skipped_old', '?')} | "
            f"no-kw={stats.get('skipped_no_kw', '?')} | "
            f"invalid={stats.get('skipped_invalid', '?')} | "
            f"failed={stats.get('failed', '?')}",
        )

        # E6: total_in_feed > 0
        total_in_feed = stats.get("total_in_feed", 0)
        report.add(
            "E6.feed_found",
            "ok" if total_in_feed > 0 else "warn",
            f"total_in_feed={total_in_feed}",
        )

        # E7: processed > 0
        processed = stats.get("processed", 0)
        report.add(
            "E7.processed",
            "ok" if processed > 0 else "warn",
            f"processed={processed}",
        )

        # E8: stats consistency
        # new_videos + skipped_seen باید <= total_in_feed باشد
        expected_total = _stats_sum(stats)
        report.add(
            "E8.stats_sum",
            "ok" if expected_total <= total_in_feed else "warn",
            f"total_in_feed={total_in_feed} | "
            f"sum(new+seen)={expected_total}",
        )

        # E10: diagnose — processed > 0 ولی stored = 0
        stored = stats.get("stored", 0)
        if processed > 0 and stored == 0:
            old = stats.get("skipped_old", 0)
            no_kw = stats.get("skipped_no_kw", 0)
            invalid = stats.get("skipped_invalid", 0)
            failed = stats.get("failed", 0)
            if old > 0:
                report.add(
                    "E10.diagnose",
                    "warn",
                    f"processed={processed} ولی stored=0 | "
                    f"old={old} — همه قبل از since",
                )
            elif no_kw > 0:
                report.add(
                    "E10.diagnose",
                    "warn",
                    f"processed={processed} ولی stored=0 | "
                    f"no-kw={no_kw} — هیچ‌کدام کلیدواژه نداشتند",
                )
            elif invalid > 0:
                report.add(
                    "E10.diagnose",
                    "warn",
                    f"processed={processed} ولی stored=0 | "
                    f"invalid={invalid} — ytInitialPlayerResponse خالی",
                )
            elif failed > 0:
                report.add(
                    "E10.diagnose",
                    "warn",
                    f"processed={processed} ولی stored=0 | "
                    f"failed={failed} — خطا در navigate",
                )
            else:
                report.add(
                    "E10.diagnose",
                    "warn",
                    f"processed={processed} ولی stored=0 — "
                    f"مشکل ناشناخته",
                )
        else:
            report.add("E10.diagnose", "ok", "healthy")
    else:
        report.add("E5.stats", "warn", "no stats")

    # E9: last_run_at updated
    report.add(
        "E9.last_run_updated",
        "ok" if (
            collector._last_run_at is not None
            and (last_run_before is None
                 or collector._last_run_at > last_run_before)
        ) else "warn",
        f"before={last_run_before} | after={collector._last_run_at}",
    )

    # ═══════════════════════════════════════════════════════
    # F) Message structure
    # ═══════════════════════════════════════════════════════
    section("F) ساختار پیام (۱۵ فیلد + semantic)")

    if not msgs:
        report.add("F.structure", "skip", "ویدیویی برای بررسی نیست")
    else:
        sample = msgs[0]

        # F1: 15 fields
        missing = [f for f in REQUIRED_MSG_FIELDS if f not in sample]
        report.add(
            "F1.fields",
            "ok" if not missing else "fail",
            f"missing={missing}" if missing
            else f"همه {len(REQUIRED_MSG_FIELDS)} فیلد موجود",
        )

        # F2: platform
        all_yt = all(m.get("platform") == "youtube" for m in msgs)
        report.add(
            "F2.platform",
            "ok" if all_yt else "fail",
            f"همه youtube: {all_yt}",
        )

        # F3: unique id
        ids = [str(m.get("id", "")) for m in msgs]
        dup = len(ids) - len(set(ids))
        report.add(
            "F3.id_unique",
            "ok" if dup == 0 else "fail",
            f"{dup} تکراری از {len(ids)}",
        )

        # F4: id format = video_id (11 char معمولاً) → warn اگر غیر استاندارد
        bad_id = [
            m for m in msgs
            if not _is_video_id(str(m.get("id", "")))
        ]
        report.add(
            "F4.id_format",
            "ok" if not bad_id else "warn",
            f"{len(bad_id)} id غیر video_id" if bad_id
            else "همه video_id معتبر",
        )

        # F5: timestamp
        ts_ok = all(
            isinstance(m.get("timestamp"), datetime) for m in msgs
        )
        report.add(
            "F5.timestamp",
            "ok" if ts_ok else "warn",
            f"datetime: {ts_ok}",
        )

        # F6: chat_type = "youtube"
        bad_ct = [
            m for m in msgs
            if m.get("chat_type") not in VALID_CHAT_TYPES
        ]
        report.add(
            "F6.chat_type",
            "ok" if not bad_ct else "warn",
            f"{len(bad_ct)} نامعتبر" if bad_ct else "معتبر",
        )

        # F7: media_type = "video"
        bad_mt = [
            m for m in msgs
            if m.get("media_type") not in VALID_MEDIA_TYPES
        ]
        report.add(
            "F7.media_type",
            "ok" if not bad_mt else "warn",
            f"{len(bad_mt)} نامعتبر" if bad_mt else "معتبر",
        )

        # F7b: همه media_type باید "video" باشند
        not_video = [
            m for m in msgs
            if m.get("media_type") != "video"
        ]
        report.add(
            "F7b.all_video",
            "ok" if not not_video else "warn",
            f"{len(not_video)} غیر video" if not_video
            else "همه video",
        )

        # F8: text not empty
        empty_text = [
            m for m in msgs if not _is_valid_text(m.get("text", ""))
        ]
        report.add(
            "F8.text",
            "ok" if not empty_text else "fail",
            f"{len(empty_text)} خالی" if empty_text
            else "همه متن دارند",
        )

        # F8b: Persian text preservation
        bad_persian = 0
        for m in msgs:
            t = m.get("text") or ""
            if _PERSIAN_RANGE.search(t):
                if not _PERSIAN_RANGE.search(t):
                    bad_persian += 1
        report.add(
            "F8b.persian",
            "ok" if bad_persian == 0 else "warn",
            f"{bad_persian} فارسی خراب" if bad_persian
            else "فارسی حفظ شد",
        )

        # F9: chat_name
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

        # F9b: chat_name = "YouTube: <channel>"
        bad_prefix = [
            m for m in msgs
            if not (m.get("chat_name") or "").startswith("YouTube:")
        ]
        report.add(
            "F9b.prefix",
            "ok" if not bad_prefix else "warn",
            f"{len(bad_prefix)} بدون prefix" if bad_prefix
            else "همه prefix دارند",
        )

        # F10: sender_name == channel
        bad_sender = [
            m for m in msgs
            if not (m.get("sender_name") or "").strip()
        ]
        report.add(
            "F10.sender_rules",
            "ok" if not bad_sender else "warn",
            f"{len(bad_sender)} بدون sender" if bad_sender
            else "همه sender دارند",
        )

        # F11: chat_link = watch URL
        bad_link = [
            m for m in msgs
            if not (m.get("chat_link") or "").startswith(
                "https://www.youtube.com/watch?v="
            )
        ]
        report.add(
            "F11.chat_link",
            "ok" if not bad_link else "warn",
            f"{len(bad_link)} link غیر watch" if bad_link
            else "همه watch",
        )

        # F12: sample preview
        print(f"\n  📩 {min(10, len(msgs))} ویدیو scrape‌شده:")
        for i, m in enumerate(msgs[:10], 1):
            txt = (m.get("text") or "")[:50].replace("\n", " ")
            cname = (m.get("chat_name") or "?")[:30]
            ts = _fmt_ts(m.get("timestamp"))
            print(f"     {i}. [{cname}] {ts}")
            print(f"        {txt}")
        if len(msgs) > 10:
            print(f"     ... و {len(msgs) - 10} ویدیو دیگر")

    # ═══════════════════════════════════════════════════════
    # G) System chat filter
    # ═══════════════════════════════════════════════════════
    section("G) فیلتر کانال سیستمی")

    if msgs:
        sys_in_output = [
            m.get("chat_name", "")
            for m in msgs
            if _is_system_chat(m.get("chat_name", ""))
        ]
        unique_sys = list(set(sys_in_output))
        report.add(
            "G1.system_filter",
            "ok" if not unique_sys else "warn",
            f"{len(sys_in_output)} ویدیو از "
            f"{len(unique_sys)} کانال سیستمی: {unique_sys[:5]}"
            if unique_sys else "هیچ کانال سیستمی در خروجی نیست",
        )

        real_channels = set()
        for m in msgs:
            cn = (m.get("chat_name") or "").strip()
            if cn and not _is_system_chat(cn):
                real_channels.add(cn)
        report.add(
            "G2.real_channels", "ok",
            f"{len(real_channels)} کانال واقعی",
        )

        # G3: درصد فیلتر سیستمی
        stats = collector._last_scrape_stats
        total = stats.get("total_in_feed", 0)
        if total > 0:
            report.add(
                "G3.feed_pct",
                "ok",
                f"{len(msgs)}/{total} ویدیو ذخیره شد",
            )

        if real_channels:
            print(f"\n  📺 کانال‌های واقعی ({len(real_channels)}):")
            for i, name in enumerate(sorted(real_channels)[:10], 1):
                print(f"     {i}. {name[:60]}")
            if len(real_channels) > 10:
                print(f"     ... و {len(real_channels) - 10} دیگر")
    else:
        report.add("G1.system_filter", "skip", "ویدیویی نیست")
        report.add("G2.real_channels", "skip", "ویدیویی نیست")

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
        total_db = await pipeline.count_messages(platform="youtube")
        report.add(
            "H2.db.total", "ok",
            f"youtube: {total_db} ویدیو در DB",
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
        total_leads = await pipeline.count_leads(platform="youtube")
        report.add(
            "H3.db.leads", "ok",
            f"youtube: {total_leads} سرنخ",
        )
    except Exception as e:
        report.add("H3.db.leads", "warn", str(e)[:80])

    # H4: content match
    if msgs:
        try:
            found = await pipeline.find_in_db(
                msgs[:20], platform="youtube"
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

        # H5: DB timestamp honored
        try:
            db_old = []
            for raw_id, row in found.items():
                db_ts_str = row.get("timestamp")
                if not db_ts_str:
                    continue
                try:
                    db_ts = datetime.fromisoformat(db_ts_str)
                except (ValueError, TypeError):
                    continue
                if since_dt and db_ts < since_dt:
                    db_old.append(raw_id[:20])

            report.add(
                "H5.db_since",
                "ok" if not db_old else "warn",
                f"{len(db_old)} ردیف قدیمی در DB" if db_old
                else "همه در DB جدیدتر از since",
            )
        except Exception as e:
            report.add("H5.db_since", "warn", str(e)[:80])

        # H6: keywords in DB (TestPipeline پر نمی‌کند → skip)
        report.add(
            "H6.db_keywords", "skip",
            "TestPipeline keywords پر نمی‌کند",
        )

    # H7: lead sample
    if total_leads > 0:
        try:
            leads = await pipeline.find_leads_in_db("youtube")
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
        report.add("I.dedup", "skip", "no videos from first run")
    else:
        section("I) اجرای دوم — تست dedup")

        try:
            seen_before = len(collector._seen_ids)
            msgs1_ids = {str(m.get("id", "")) for m in msgs}

            t0 = time.monotonic()
            msgs2 = await collector.fetch_messages(
                since=since_dt,
                force_no_since=force_no_since or since_days <= 0,
            )
            elapsed2 = time.monotonic() - t0
            seen_after = len(collector._seen_ids)
            msgs2_ids = {str(m.get("id", "")) for m in msgs2}

            report.add(
                "I1.second_fetch", "ok",
                f"count={len(msgs2)} | "
                f"seen {seen_before}→{seen_after} | {elapsed2:.0f}s",
            )

            if len(msgs2) == 0:
                report.add(
                    "I2.dedup", "ok",
                    "بار دوم ۰ ویدیو — dedup کامل",
                )
            elif len(msgs2) < len(msgs):
                report.add(
                    "I2.dedup", "warn",
                    f"بار دوم {len(msgs2)} (از {len(msgs)} اولیه) — "
                    f"احتمالاً ویدیوهای واقعاً جدید",
                )
            else:
                report.add(
                    "I2.dedup", "warn",
                    f"بار دوم {len(msgs2)} — dedup کامل نیست",
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

            total_db2 = await pipeline.count_messages(platform="youtube")
            report.add(
                "I6.db_unchanged",
                "ok" if total_db2 == total_db else "warn",
                f"DB قبل={total_db} | بعد={total_db2}",
            )

            if len(msgs2) > 0:
                grew = seen_after > seen_before
                report.add(
                    "I7.seen_growth",
                    "ok" if grew else "warn",
                    f"seen {seen_before}→{seen_after} | "
                    f"new={len(msgs2)}",
                )
            else:
                report.add(
                    "I7.seen_growth", "ok",
                    "بدون ویدیو جدید — seen رشد نکرده (طبیعی)",
                )

        except Exception as e:
            report.add("I.dedup", "fail", f"{type(e).__name__}: {e}")

    # ═══════════════════════════════════════════════════════
    # J) dump_dom
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
        if dom_path and size > 0:
            try:
                with open(dom_path, encoding="utf-8", errors="ignore") as f:
                    head = f.read(2000)
                has_html = (
                    "youtube" in head.lower()
                    or "ytd-" in head.lower()
                    or "<div" in head
                    or "<html" in head.lower()
                )
                report.add(
                    "J2.dump_content",
                    "ok" if has_html else "info",
                    "محتوای HTML دارد" if has_html
                    else "محتوا مشکوک (صفحهٔ اصلی)",
                )
            except Exception as e:
                report.add("J2.dump_content", "warn", str(e)[:80])
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
# Pytest wrapper
# ═══════════════════════════════════════════════════════════
import pytest


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("YOUTUBE_INTEGRATION") != "1",
    reason="integration — با YOUTUBE_INTEGRATION=1 فعال شود",
)
def test_youtube_integration():
    """E2E test — runs main() and asserts success."""
    from dotenv import load_dotenv
    load_dotenv(override=False)

    ok = asyncio.run(main(
        headless=True,
        max_videos=0,
        since_days=7,
        skip_second_run=False,
    ))
    assert ok, "integration test failed — see report above"


# ═══════════════════════════════════════════════════════════
# Entry point (as script)
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="تست یکپارچه یوتیوب — Pipeline end-to-end (v6.0)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
مثال‌ها:
  python tests/integration/test_youtube_full.py
  python tests/integration/test_youtube_full.py --since-days 0
  python tests/integration/test_youtube_full.py --since-days 365
  python tests/integration/test_youtube_full.py --max-videos 5 --since-days 0
  python tests/integration/test_youtube_full.py --force-no-since
        """,
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--max-videos", type=int, default=0,
        help="حداکثر ویدیو (0=از env)",
    )
    parser.add_argument(
        "--since-days", type=int, default=7,
        help="بازه (0=بدون since)",
    )
    parser.add_argument("--skip-second-run", action="store_true")
    parser.add_argument(
        "--force-no-since", action="store_true",
        help="بدون فیلتر since (تاریخچه کامل)",
    )
    args = parser.parse_args()

    ok = asyncio.run(main(
        headless=args.headless,
        max_videos=args.max_videos,
        since_days=args.since_days,
        skip_second_run=args.skip_second_run,
        force_no_since=args.force_no_since,
    ))
    sys.exit(0 if ok else 1)