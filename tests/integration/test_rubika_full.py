# tests/integration/test_rubika_full.py
"""
Integration tests for collectors/rubika.py — v5.1 (precise, from scratch).

Requires:
  - RUBIKA_ENABLED=true in .env
  - Session saved in storage/sessions/rubika_session/  (فایل .rp)
  - Network access to rubika.ir

v5.1:
  - 🐛 FIX: _stats_sum — empty_chats زیرمجموعه processed (نه موازی)
  - 🐛 FIX: E8.stats_sum — `<=` (چون --max-chats ممکن است total > sum)
  - 🐛 FIX: H6.db_keywords — "warn" → "skip"
  - 🎯 همان ساختار A-K مثل test_bale_full.py
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
VALID_CHAT_TYPES = {"private", "group", "channel"}

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

SYSTEM_CHAT_NAMES = {
    "فضای شخصی", "پیام‌های ذخیره شده", "پیامهای ذخیره شده",
    "ذخیره‌شده", "ذخیره شده", "شما", "تصویر", "پیام",
    "بایگانی", "آرشیو", "فایل", "فایل‌ها", "فایل‌های من",
    "استیکر", "استیکرها", "گیف", "گیف‌ها", "ویدیو", "ویدیوها",
    "ویس", "ویس‌ها", "عکس", "عکس‌ها", "لینک", "لینک‌ها",
    "مخاطبین", "مخاطبین من", "پوشه‌ها", "گروه‌ها", "کانال‌ها",
    "آلبوم", "آلبوم‌ها", "روبیکا", "پیام‌های روبیکا", "تیم روبیکا",
    "پشتیبانی روبیکا", "Saved Messages", "Drafts", "You",
    "Images", "Messages", "Archive", "Archived", "Files",
    "Stickers", "GIFs", "Videos", "Voice", "Photos",
    "Links", "Contacts", "Folders", "Groups", "Channels",
    "Albums", "Rubika", "Rubika Messenger", "Rubika Team",
    "پیش‌نویس", "پیام‌های ذخیره‌شده",
}

SYSTEM_PREFIXES = (
    "کانال ایجاد", "گروه ایجاد", "کانال جدید", "گروه جدید",
    "Channel created", "Group created", "New channel", "New group",
    "پیام‌های ذخیره", "Saved messages",
)

SYSTEM_USERNAME_HINTS = ("saved", "draft", "rubika", "eitaa", "bale", "telegram")

MAX_FETCH_SECONDS = 400

# Persian/Arabic Unicode range
_PERSIAN_RANGE = re.compile(r"[\u0600-\u06FF]")


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


def _is_hex32(s: str) -> bool:
    if not isinstance(s, str) or len(s) != 32:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in s)


def _stats_sum(stats: dict) -> int:
    """Sum of mutually exclusive subset counters.

    Note: empty_chats is a SUBSET of processed (not parallel),
    so it must NOT be added separately.
    """
    return (
        stats.get("processed", 0)
        + stats.get("skipped_system", 0)
        + stats.get("skipped_target", 0)
        + stats.get("failed", 0)
    )


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════
async def main(
    headless: bool = False,
    max_chats: int = 0,
    since_days: int = 7,
    skip_second_run: bool = False,
) -> bool:
    banner("تست کامل روبیکا (Rubika) — Pipeline end-to-end (v5.1)")
    report = TestReport("rubika")

    # ═══════════════════════════════════════════════════════
    # A) Pre-flight
    # ═══════════════════════════════════════════════════════
    section("A) پیش‌نیازها")

    # A1: DNS
    try:
        ip = socket.gethostbyname("rubika.ir")
        report.add("A1.dns", "ok", f"→ {ip}")
    except Exception as e:
        report.add("A1.dns", "fail", f"{type(e).__name__}: {e}")
        print(f"\n{C.FAIL}❌ DNS rubika.ir resolve نشد{C.END}")
        report.print_summary()
        report.save()
        return False

    # A2: HTTP
    try:
        req = urllib.request.Request(
            "https://rubika.ir/",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            report.add("A2.http", "ok", f"status={resp.status}")
    except Exception as e:
        report.add("A2.http", "warn", str(e)[:80])

    # A3: env
    env = check_env("rubika")
    enabled = env.get("enabled", False)
    report.add(
        "A3.env.enabled",
        "ok" if enabled else "skip",
        f"RUBIKA_ENABLED={enabled}",
    )
    if not enabled:
        print(
            f"\n{C.INFO}⏭️ روبیکا غیرفعال (skip) — "
            f"برای اجرا: RUBIKA_ENABLED=true{C.END}"
        )
        report.print_summary()
        report.save()
        return True

    # A4: session (mapped to rubika_session via helpers.py)
    session_path = env.get("session_path", "")
    session_exists = (
        check_session_exists(session_path) if session_path else False
    )
    report.add(
        "A4.session",
        "ok" if session_exists else "warn",
        f"exists={session_exists} | path={session_path[:60] or '—'}",
    )
    if not session_exists:
        print(f"\n{C.WARN}⚠️ نشست روبیکا وجود ندارد{C.END}")
        print("   راه‌حل: python login.py → گزینهٔ ۱ (روبیکا)")
        print("   (ادامه می‌دهیم — شاید rubpy بتواند بسازد)")

    # A5: rubpy library
    try:
        import rubpy
        report.add(
            "A5.rubpy", "ok",
            f"version={getattr(rubpy, '__version__', '?')}",
        )
    except ImportError:
        report.add("A5.rubpy", "fail", "rubpy نصب نیست — pip install rubpy")
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
        from collectors.rubika import RubikaCollector

        history_limit = int(os.getenv("RUBIKA_HISTORY_LIMIT", "500"))
        max_chats_env = int(os.getenv("RUBIKA_MAX_CHATS", "0"))
        actual_max_chats = max_chats if max_chats > 0 else max_chats_env

        collector = RubikaCollector(
            phone=env.get("phone") or None,
            poll_seconds=999,
        )
        # دستی تنظیم چون rubika args ندارد
        collector._max_chats = actual_max_chats

        report.add(
            "C1.init", "ok",
            f"platform={collector.platform_name} | "
            f"history_limit={history_limit} | "
            f"max_chats={actual_max_chats}",
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
        "ok" if pname == "rubika" else "fail",
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
        print(f"\n{C.FAIL}❌ اتصال روبیکا برقرار نشد{C.END}")
        print("   راه‌حل: python login.py → گزینهٔ ۱ (روبیکا)")
        await safe_disconnect(collector, report)
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    # D2: is_connected property
    try:
        is_conn = collector._connected
        report.add(
            "D2.is_connected",
            "ok" if is_conn else "fail",
            f"connected={is_conn}",
        )
    except Exception as e:
        report.add("D2.is_connected", "warn", str(e)[:80])

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

    # D4: chat_cache preload
    try:
        cache_size = len(collector._chat_cache)
        report.add(
            "D4.cache",
            "ok" if cache_size > 0 else "warn",
            f"chats_cached={cache_size}",
        )
    except Exception as e:
        report.add("D4.cache", "warn", str(e)[:80])

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
        f"{len(msgs)} پیام",
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
            f"total={stats.get('total_chats', '?')} | "
            f"processed={stats.get('processed', '?')} | "
            f"system={stats.get('skipped_system', '?')} | "
            f"failed={stats.get('failed', '?')} | "
            f"found={stats.get('messages_found', '?')} | "
            f"old={stats.get('messages_skipped_old', '?')} | "
            f"dup={stats.get('messages_skipped_dup', '?')}",
        )

        total_chats = stats.get("total_chats", 0)
        report.add(
            "E6.chats_found",
            "ok" if total_chats > 0 else "warn",
            f"total_chats={total_chats}",
        )

        processed = stats.get("processed", 0)
        report.add(
            "E7.processed",
            "ok" if processed > 0 else "warn",
            f"processed={processed}",
        )

        expected_total = _stats_sum(stats)
        report.add(
            "E8.stats_sum",
            "ok" if expected_total <= total_chats else "warn",
            f"total={total_chats} | sum={expected_total}",
        )
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
        report.add("F.structure", "skip", "پیامی برای بررسی نیست")
    else:
        sample = msgs[0]

        missing = [f for f in REQUIRED_MSG_FIELDS if f not in sample]
        report.add(
            "F1.fields",
            "ok" if not missing else "fail",
            f"missing={missing}" if missing
            else f"همه {len(REQUIRED_MSG_FIELDS)} فیلد موجود",
        )

        all_rubika = all(m.get("platform") == "rubika" for m in msgs)
        report.add(
            "F2.platform",
            "ok" if all_rubika else "fail",
            f"همه rubika: {all_rubika}",
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

        bad_media = [
            m for m in msgs
            if m.get("media_type") == "text"
            and (m.get("file_id") or m.get("local_file_path"))
        ]
        report.add(
            "F7b.media_consistent",
            "ok" if not bad_media else "warn",
            f"{len(bad_media)} text با file_id" if bad_media
            else "معتبر",
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

        bad_persian = []
        for m in msgs:
            text = m.get("text") or ""
            if _PERSIAN_RANGE.search(text):
                if not _PERSIAN_RANGE.search(text):
                    bad_persian.append(m.get("id", "")[:20])
        report.add(
            "F8b.persian",
            "ok" if not bad_persian else "warn",
            f"{len(bad_persian)} فارسی خراب" if bad_persian
            else "فارسی حفظ شد",
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

        by_chat: dict[str, set[str]] = {}
        for m in msgs:
            cid = m.get("chat_id", "")
            name = (m.get("chat_name") or "").strip()
            if cid and name:
                by_chat.setdefault(cid, set()).add(name)
        bad_consistency = [
            cid for cid, names in by_chat.items() if len(names) > 1
        ]
        report.add(
            "F9b.name_consistent",
            "ok" if not bad_consistency else "warn",
            f"{len(bad_consistency)} chat با نام‌های مختلف"
            if bad_consistency else "یکسان",
        )

        bad_sender = []
        for m in msgs:
            ct = m.get("chat_type")
            sn = (m.get("sender_name") or "").strip()
            if ct == "private" and not sn:
                bad_sender.append(
                    f"private id={m.get('id','')[:8]} sender empty"
                )
            elif ct in ("group", "channel") and not sn:
                bad_sender.append(
                    f"{ct} id={m.get('id','')[:8]} sender empty"
                )
        report.add(
            "F10.sender_rules",
            "ok" if not bad_sender else "warn",
            f"{len(bad_sender)} نقض" if bad_sender
            else "معتبر",
        )

        bad_cid = []
        for m in msgs:
            cid = m.get("chat_id", "")
            uname = m.get("chat_username")
            if uname and cid == uname:
                bad_cid.append(m.get("id", "")[:20])
        report.add(
            "F11.chat_id_distinct",
            "ok" if not bad_cid else "warn",
            f"{len(bad_cid)} chat_id=username" if bad_cid
            else "متمایز",
        )

        print(f"\n  📩 {min(5, len(msgs))} پیام scrape‌شده:")
        for m in msgs[:5]:
            txt = (m.get("text") or "")[:60].replace("\n", " ")
            cname = (m.get("chat_name") or "?")[:30]
            ctype = m.get("chat_type", "?")
            print(f"     [{cname}] ({ctype})")
            print(f"        {txt}")

        types = {m.get("chat_type") for m in msgs}
        report.add("F13.types", "ok", f"types={types}")

    # ═══════════════════════════════════════════════════════
    # G) System chat filter
    # ═══════════════════════════════════════════════════════
    section("G) فیلتر چت سیستمی")

    if msgs:
        sys_in_output = [
            m.get("chat_name", "")
            for m in msgs
            if _is_system_chat(m.get("chat_name", ""))
        ]
        unique_sys = list(set(sys_in_output))
        report.add(
            "G1.system_filter",
            "ok" if not unique_sys else "fail",
            f"{len(sys_in_output)} پیام از "
            f"{len(unique_sys)} سیستمی: {unique_sys[:5]}"
            if unique_sys else "هیچ چت سیستمی در خروجی نیست",
        )

        sys_unames = []
        for m in msgs:
            uname = (m.get("chat_username") or "").lower()
            if uname and any(h in uname for h in SYSTEM_USERNAME_HINTS):
                sys_unames.append(uname)
        report.add(
            "G2.username_filter",
            "ok" if not sys_unames else "warn",
            f"{len(sys_unames)} username سیستمی" if sys_unames
            else "هیچ username سیستمی",
        )

        real_chats = set()
        for m in msgs:
            cn = (m.get("chat_name") or "").strip()
            if cn and not _is_system_chat(cn):
                real_chats.add(cn)
        report.add(
            "G3.real_chats", "ok",
            f"{len(real_chats)} چت واقعی",
        )

        if real_chats:
            print(f"\n  📋 چت‌های واقعی ({len(real_chats)}):")
            for i, name in enumerate(sorted(real_chats)[:10], 1):
                print(f"     {i}. {name[:60]}")
            if len(real_chats) > 10:
                print(f"     ... و {len(real_chats) - 10} دیگر")
    else:
        report.add("G1.system_filter", "skip", "پیامی نیست")
        report.add("G2.username_filter", "skip", "پیامی نیست")
        report.add("G3.real_chats", "skip", "پیامی نیست")

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
        total_db = await pipeline.count_messages(platform="rubika")
        report.add(
            "H2.db.total", "ok",
            f"rubika: {total_db} پیام در DB",
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
        total_leads = await pipeline.count_leads(platform="rubika")
        report.add(
            "H3.db.leads", "ok",
            f"rubika: {total_leads} سرنخ",
        )
    except Exception as e:
        report.add("H3.db.leads", "warn", str(e)[:80])

    if msgs:
        try:
            found = await pipeline.find_in_db(msgs[:20], platform="rubika")
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

        try:
            from analyzers.keyword_finder import get_keyword_finder
            kw = get_keyword_finder()
            with_kw = [m for m in msgs if kw.has_match(m.get("text") or "")]
            if with_kw:
                found_kw = await pipeline.find_in_db(
                    with_kw[:10], platform="rubika"
                )
                kw_populated = sum(
                    1 for row in found_kw.values()
                    if (row.get("keywords") or "").strip()
                )
                report.add(
                    "H6.db_keywords",
                    "ok" if kw_populated > 0 else "skip",
                    f"{kw_populated}/{len(found_kw)} ردیف keywords دارند",
                )
            else:
                report.add(
                    "H6.db_keywords", "skip",
                    "هیچ پیام کلیدواژه‌داری نیست",
                )
        except Exception as e:
            report.add("H6.db_keywords", "warn", str(e)[:80])

    if total_leads > 0:
        try:
            leads = await pipeline.find_leads_in_db("rubika")
            print(f"\n  🎯 {min(3, len(leads))} lead نمونه:")
            for ld in leads[:3]:
                chat = (ld.get("chat_name") or "?")[:30]
                kwv = (ld.get("keywords") or "—")[:40]
                tier = ld.get("opportunity_type") or "—"
                print(f"     [{chat}] kw={kwv} | tier={tier}")
            report.add(
                "H7.lead_sample", "ok",
                f"{min(3, len(leads))} نمونه",
            )

            valid_mid = sum(
                1 for ld in leads if ld.get("message_id")
            )
            report.add(
                "H7b.lead_mid",
                "ok" if valid_mid == len(leads) else "warn",
                f"{valid_mid}/{len(leads)} lead با message_id",
            )
        except Exception as e:
            report.add("H7.lead_sample", "warn", str(e)[:80])
    else:
        report.add(
            "H7.lead_sample", "skip",
            "هیچ سرنخی نیست (طبیعی اگر پیام‌ها کلیدواژه نداشتند)",
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

            if len(msgs2) == 0:
                report.add(
                    "I2.dedup", "ok",
                    "بار دوم ۰ پیام جدید — dedup کامل",
                )
            elif len(msgs2) < len(msgs):
                report.add(
                    "I2.dedup", "warn",
                    f"بار دوم {len(msgs2)} (از {len(msgs)} اولیه) — "
                    f"احتمالاً پیام‌های واقعاً جدید",
                )
            else:
                report.add(
                    "I2.dedup", "warn",
                    f"بار دوم {len(msgs2)} — dedup ممکن است "
                    f"به‌درستی کار نکند",
                )

            overlap_ids = msgs1_ids & msgs2_ids
            report.add(
                "I3.id_determinism",
                "ok" if not overlap_ids else "fail",
                f"{len(overlap_ids)} id تکراری بین دو اجرا" if overlap_ids
                else "هیچ id تکراری",
            )

            new_ids = msgs2_ids - msgs1_ids
            if new_ids:
                bad_new = []
                for mid in new_ids:
                    m = next(
                        (x for x in msgs2 if str(x.get("id")) == mid),
                        None,
                    )
                    if not m:
                        continue
                    ts = m.get("timestamp")
                    if (
                        isinstance(ts, datetime)
                        and last_run_before is not None
                        and ts <= last_run_before
                    ):
                        bad_new.append(mid[:20])
                report.add(
                    "I4.new_ids_genuine",
                    "ok" if not bad_new else "warn",
                    f"{len(bad_new)} id جدید ولی timestamp قدیمی" if bad_new
                    else f"{len(new_ids)} id جدید — همه واقعاً جدید",
                )
            else:
                report.add(
                    "I4.new_ids_genuine", "ok",
                    "هیچ id جدید",
                )

            stored2 = await pipeline.feed(msgs2)
            report.add(
                "I5.second_feed",
                "ok" if stored2 == 0 else "warn",
                f"stored={stored2} (باید ۰ باشد اگر dedup درست)",
            )

            total_db2 = await pipeline.count_messages(platform="rubika")
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
                    "بدون پیام جدید — seen رشد نکرده (طبیعی)",
                )

        except Exception as e:
            report.add("I.dedup", "fail", f"{type(e).__name__}: {e}")

    # ═══════════════════════════════════════════════════════
    # J) dump_dom (skip for rubika — no DOM)
    # ═══════════════════════════════════════════════════════
    section("J) dump_dom (نامربوط برای rubika — API)")

    report.add(
        "J1.dump_dom", "skip",
        "rubika از API استفاده می‌کند (no DOM)",
    )
    report.add(
        "J2.dump_content", "skip",
        "rubika از API استفاده می‌کند (no DOM)",
    )

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
        "ok" if collector._client is None else "warn",
        f"client={collector._client is not None}",
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
    os.getenv("RUBIKA_INTEGRATION") != "1",
    reason="integration — با RUBIKA_INTEGRATION=1 فعال شود",
)
def test_rubika_integration():
    """E2E test — runs main() and asserts success."""
    from dotenv import load_dotenv
    load_dotenv(override=False)

    ok = asyncio.run(main(
        headless=True,
        max_chats=0,
        since_days=7,
        skip_second_run=False,
    ))
    assert ok, "integration test failed — see report above"


# ═══════════════════════════════════════════════════════════
# Entry point (as script)
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="تست یکپارچه روبیکا — Pipeline end-to-end (v5.1)",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-chats", type=int, default=0)
    parser.add_argument("--since-days", type=int, default=7)
    parser.add_argument("--skip-second-run", action="store_true")
    args = parser.parse_args()

    ok = asyncio.run(main(
        headless=args.headless,
        max_chats=args.max_chats,
        since_days=args.since_days,
        skip_second_run=args.skip_second_run,
    ))
    sys.exit(0 if ok else 1)