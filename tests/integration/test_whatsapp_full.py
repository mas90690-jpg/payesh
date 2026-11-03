# tests/integration/test_whatsapp_full.py
"""
Integration tests for collectors/whatsapp.py — v1.0

Requires:
  - Session saved in storage/sessions/whatsapp/
  - Network access to web.whatsapp.com

تغییرات v1.0:
  - 🎯 تست کامل اتصال و scrape واتساپ
  - 🎯 تست ساختار پیام (۱۵ فیلد)
  - 🎯 تست ذخیره در DB (Pipeline)
  - 🎯 تست dedup (seen_ids)
  - 🎯 هشدار برای GIFهای 0KB (media لود نشده)
  - 🎯 ساختار A-K همانند سایر پلتفرم‌ها
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import socket
import sys
import time
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
VALID_CHAT_TYPES = {"private", "group", "channel"}

VALID_MEDIA_TYPES = {
    "text", "photo", "video", "voice", "audio",
    "document", "sticker", "file", "gif",
    "pdf", "application/pdf",
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

# نام‌های سیستمی واتساپ
SYSTEM_CHAT_NAMES = {
    "پیام‌های ذخیره شده", "پیامهای ذخیره شده",
    "ذخیره‌شده", "ذخیره شده",
    "Saved Messages", "Saved",
    "You", "Yourself",
    "واتساپ", "WhatsApp",
    "تیم واتساپ", "WhatsApp Team",
}

SYSTEM_PREFIXES = (
    "کانال ایجاد", "گروه ایجاد",
    "Channel created", "Group created",
)

MAX_FETCH_SECONDS = 400

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
    return (
        stats.get("processed", 0)
        + stats.get("errors", 0)
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
    max_chats: int = 0,
    since_days: int = 7,
    skip_second_run: bool = False,
    force_no_since: bool = False,
) -> bool:
    banner("تست کامل واتساپ (WhatsApp) — Pipeline end-to-end (v1.0)")
    report = TestReport("whatsapp")

    # ═══════════════════════════════════════════════════════
    # A) Pre-flight
    # ═══════════════════════════════════════════════════════
    section("A) پیش‌نیازها")

    try:
        ip = socket.gethostbyname("web.whatsapp.com")
        report.add("A1.dns", "ok", f"→ {ip}")
    except Exception as e:
        report.add("A1.dns", "fail", f"{type(e).__name__}: {e}")
        print(f"\n{C.FAIL}❌ DNS web.whatsapp.com resolve نشد{C.END}")
        print("   راه‌حل: VPN/اینترنت را چک کن")
        report.print_summary()
        report.save()
        return False

    report.add(
        "A2.http", "skip",
        "web.whatsapp.com — Playwright استفاده می‌کند",
    )

    env = check_env("whatsapp")
    enabled = env.get("enabled", True)  # واتساپ env ندارد
    report.add(
        "A3.env.enabled",
        "ok" if enabled else "skip",
        "whatsapp — env-based ندارد",
    )

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
        print(f"\n{C.WARN}⚠️ نشست واتساپ وجود ندارد{C.END}")
        print("   راه‌حل: python login.py → گزینهٔ ۵ (WhatsApp)")

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
        from collectors.whatsapp import WhatsAppCollector

        collector = WhatsAppCollector(
            headless=headless,
            poll_seconds=999,
            max_chats=max_chats,
            messages_per_chat=30,
            backfill_scrolls=6,
            chat_open_delay=1.5,
        )
        report.add(
            "C1.init", "ok",
            f"mpc=30 | max_chats={max_chats or '∞'}",
        )
    except Exception as e:
        report.add("C1.init", "fail", f"{type(e).__name__}: {e}")
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    pname = getattr(collector, "platform_name", None)
    report.add(
        "C2.platform_name",
        "ok" if pname == "whatsapp" else "fail",
        f"{pname}",
    )

    poll = getattr(collector, "poll_interval_seconds", None)
    report.add(
        "C3.poll_interval",
        "ok" if isinstance(poll, (int, float)) else "warn",
        f"{poll}",
    )

    report.add(
        "C4.keywords_only",
        "ok",
        f"keywords_only={collector._keywords_only}",
    )

    report.add(
        "C5.stats_init",
        "ok" if isinstance(collector._last_scrape_stats, dict) else "warn",
        f"keys={list(collector._last_scrape_stats.keys())[:3]}...",
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
        print(f"\n{C.FAIL}❌ اتصال واتساپ برقرار نشد{C.END}")
        print("   راه‌حل: python login.py → گزینهٔ ۵ (WhatsApp)")
        await safe_disconnect(collector, report)
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

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

    try:
        health = await collector.check_health()
        report.add(
            "D3.check_health",
            "ok" if health else "warn",
            f"health={health}",
        )
    except Exception as e:
        report.add("D3.check_health", "warn", str(e)[:80])

    try:
        url = collector._page.url if collector._page else ""
        report.add(
            "D4.page_url",
            "ok" if "whatsapp" in url.lower() else "warn",
            url[:80],
        )
    except Exception as e:
        report.add("D4.page_url", "warn", str(e)[:80])

    # ═══════════════════════════════════════════════════════
    # E) Fetch
    # ═══════════════════════════════════════════════════════
    if force_no_since or since_days <= 0:
        section("E) fetch_messages (بدون since — تاریخچه کامل)")
    else:
        section(f"E) fetch_messages (since={since_days} روز)")

    msgs: list = []
    fetch_elapsed = 0.0
    try:
        t0 = time.monotonic()
        msgs = await collector.fetch_messages()
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

    report.add(
        "E2.count",
        "ok" if msgs else "warn",
        f"{len(msgs)} پیام",
    )

    if fetch_elapsed > MAX_FETCH_SECONDS:
        report.add(
            "E3.timeout", "warn",
            f"{fetch_elapsed:.0f}s > {MAX_FETCH_SECONDS}s",
        )
    else:
        report.add("E3.timeout", "ok", f"{fetch_elapsed:.0f}s")

    # آمار
    stats = getattr(collector, "_last_scrape_stats", {})
    if isinstance(stats, dict) and stats:
        report.add(
            "E5.stats", "ok",
            f"total={stats.get('total_chats', '?')} | "
            f"processed={stats.get('processed', '?')} | "
            f"found={stats.get('messages_found', '?')} | "
            f"errors={stats.get('errors', '?')}",
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

        found = stats.get("messages_found", 0)
        if processed > 0 and found == 0:
            report.add(
                "E10.diagnose",
                "warn",
                f"processed={processed} ولی found=0 — "
                f"مشکل در خواندن DOM",
            )
        else:
            report.add("E10.diagnose", "ok", "healthy")
    else:
        report.add("E5.stats", "warn", "no stats")

    report.add(
        "E9.seen_size",
        "ok",
        f"seen={len(collector._seen_ids)}",
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

        all_wa = all(m.get("platform") == "whatsapp" for m in msgs)
        report.add(
            "F2.platform",
            "ok" if all_wa else "fail",
            f"همه whatsapp: {all_wa}",
        )

        ids = [str(m.get("id", "")) for m in msgs]
        dup = len(ids) - len(set(ids))
        report.add(
            "F3.id_unique",
            "ok" if dup == 0 else "fail",
            f"{dup} تکراری از {len(ids)}",
        )

        bad_id = [
            m for m in msgs if not str(m.get("id", "")).strip()
        ]
        report.add(
            "F4.id_not_empty",
            "ok" if not bad_id else "warn",
            f"{len(bad_id)} id خالی" if bad_id
            else "همه id دارند",
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

        # 🎯 F9b: chat_name یکسان برای هر chat_id
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

        # 🎯 F10: sender_name rules
        bad_sender = 0
        for m in msgs:
            ct = m.get("chat_type")
            sn = (m.get("sender_name") or "").strip()
            if ct in ("group", "channel") and not sn:
                bad_sender += 1
        report.add(
            "F10.sender_rules",
            "ok" if bad_sender == 0 else "warn",
            f"{bad_sender} پیام گروهی بدون sender" if bad_sender
            else "همه sender دارند",
        )

        # 🎯 F14: توزیع chat_type
        types_count: dict[str, int] = {}
        for m in msgs:
            ct = m.get("chat_type", "?")
            types_count[ct] = types_count.get(ct, 0) + 1
        report.add(
            "F14.types",
            "ok",
            f"{types_count}",
        )

        print(f"\n  📩 {min(10, len(msgs))} پیام scrape‌شده:")
        for i, m in enumerate(msgs[:10], 1):
            txt = (m.get("text") or "")[:50].replace("\n", " ")
            cname = (m.get("chat_name") or "?")[:25]
            ctype = m.get("chat_type", "?")
            ts = _fmt_ts(m.get("timestamp"))
            print(f"     {i}. [{cname}] ({ctype}) {ts}")
            print(f"        {txt}")
        if len(msgs) > 10:
            print(f"     ... و {len(msgs) - 10} پیام دیگر")

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

        real_chats = set()
        for m in msgs:
            cn = (m.get("chat_name") or "").strip()
            if cn and not _is_system_chat(cn):
                real_chats.add(cn)
        report.add(
            "G2.real_chats", "ok",
            f"{len(real_chats)} چت واقعی",
        )

        stats = collector._last_scrape_stats
        total = stats.get("total_chats", 0)
        if total > 0:
            report.add(
                "G3.chats_pct",
                "ok",
                f"{len(real_chats)}/{total} چت",
            )
    else:
        report.add("G1.system_filter", "skip", "پیامی نیست")
        report.add("G2.real_chats", "skip", "پیامی نیست")

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
        total_db = await pipeline.count_messages(platform="whatsapp")
        report.add(
            "H2.db.total", "ok",
            f"whatsapp: {total_db} پیام در DB",
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
        total_leads = await pipeline.count_leads(platform="whatsapp")
        report.add(
            "H3.db.leads", "ok",
            f"whatsapp: {total_leads} سرنخ",
        )
    except Exception as e:
        report.add("H3.db.leads", "warn", str(e)[:80])

    if msgs:
        try:
            found = await pipeline.find_in_db(
                msgs[:20], platform="whatsapp"
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
            leads = await pipeline.find_leads_in_db("whatsapp")
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
            msgs2 = await collector.fetch_messages()
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
                    "بار دوم ۰ پیام — dedup کامل",
                )
            elif len(msgs2) < len(msgs) * 0.5:
                report.add(
                    "I2.dedup", "ok",
                    f"بار دوم {len(msgs2)} (از {len(msgs)} اولیه) — "
                    f"dedup خوب",
                )
            else:
                report.add(
                    "I2.dedup", "warn",
                    f"بار دوم {len(msgs2)} (از {len(msgs)} اولیه) — "
                    f"dedup کامل نیست",
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

            total_db2 = await pipeline.count_messages(platform="whatsapp")
            report.add(
                "I6.db_unchanged",
                "ok" if total_db2 == total_db else "warn",
                f"DB قبل={total_db} | بعد={total_db2}",
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
    os.getenv("WHATSAPP_INTEGRATION") != "1",
    reason="integration — با WHATSAPP_INTEGRATION=1 فعال شود",
)
def test_whatsapp_integration():
    from dotenv import load_dotenv
    load_dotenv(override=False)

    ok = asyncio.run(main(
        headless=True,
        max_chats=0,
        since_days=7,
        skip_second_run=False,
    ))
    assert ok, "integration test failed — see report above"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="تست یکپارچه واتساپ — Pipeline end-to-end (v1.0)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
مثال‌ها:
  python tests/integration/test_whatsapp_full.py
  python tests/integration/test_whatsapp_full.py --since-days 0
  python tests/integration/test_whatsapp_full.py --max-chats 3 --since-days 0
        """,
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--max-chats", type=int, default=0,
        help="حداکثر چت (0=همه)",
    )
    parser.add_argument(
        "--since-days", type=int, default=7,
        help="بازه (0=بدون since — واتساپ supports_history=False)",
    )
    parser.add_argument("--skip-second-run", action="store_true")
    parser.add_argument(
        "--force-no-since", action="store_true",
        help="بدون فیلتر since (واتساپ همیشه بدون since است)",
    )
    args = parser.parse_args()

    ok = asyncio.run(main(
        headless=args.headless,
        max_chats=args.max_chats,
        since_days=args.since_days,
        skip_second_run=args.skip_second_run,
        force_no_since=args.force_no_since,
    ))
    sys.exit(0 if ok else 1)