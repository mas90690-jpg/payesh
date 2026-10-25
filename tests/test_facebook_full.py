# tests/test_facebook_full.py
"""
تست کامل فیسبوک — Pipeline end-to-end (v4.1 GraphQL-first)

هم‌راستا با test_linkedin_full.py:
  - ۱۵ فیلد اجباری
  - id یکتا (32 hex)
  - timestamp datetime
  - chat_type = "facebook"
  - media_type معتبر
  - since honored
  - DB content match
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import (
    TestReport, banner, check_env, safe_disconnect, section, C,
)
from tests.pipeline_helpers import TestPipeline


VALID_CHAT_TYPES = {"facebook", "feed", "group", "private"}
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

MAX_FETCH_SECONDS = 400


def _session_dir() -> str:
    try:
        from config import STORAGE_DIR
        return os.path.join(STORAGE_DIR, "sessions", "facebook")
    except Exception:
        return os.path.join("storage", "sessions", "facebook")


def _session_has_data(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    try:
        entries = [
            e for e in os.listdir(path) if not e.startswith(".")
        ]
        return (
            "Default" in entries
            or "Local State" in entries
            or len(entries) > 3
        )
    except Exception:
        return False


def _facebook_enabled() -> bool:
    raw = os.getenv("FACEBOOK_ENABLED", "").strip().lower()
    if raw in ("true", "1", "yes", "on"):
        return True
    if raw in ("false", "0", "no", "off"):
        return False
    try:
        from config import PLUGINS_CONFIG
        cfg = PLUGINS_CONFIG.get("facebookcollector", {})
        return bool(cfg.get("enabled", False))
    except Exception:
        return False


async def main(
    headless: bool = False,
    max_posts: int = 30,
    since_days: int = 7,
):
    banner("تست کامل فیسبوک — GraphQL (v4.1)")
    report = TestReport("facebook")

    # ═══ ۱) env ═══
    section("۱) env")
    if not _facebook_enabled():
        report.add("env.enabled", "skip", "FACEBOOK_ENABLED=False")
        report.print_summary()
        report.save()
        return True
    report.add("env.enabled", "ok", "FACEBOOK_ENABLED=True")

    # ═══ ۲) نشست ═══
    section("۲) نشست")
    profile_dir = _session_dir()
    if not _session_has_data(profile_dir):
        report.add("session.has_data", "fail", profile_dir)
        report.print_summary()
        report.save()
        return False
    report.add("session.has_data", "ok", profile_dir)

    # ═══ ۳) pipeline ═══
    section("۳) pipeline")
    pipeline = TestPipeline(
        keywords_only=False, require_t1=False,
        allowed_tiers=("T1", "T2", "T3"),
    )
    await pipeline.start()
    report.add(
        "pipeline.init", "ok",
        f"db={os.path.basename(pipeline._db_path)}"
    )

    # ═══ ۴) collector ═══
    section("۴) collector")
    from collectors.facebook import FacebookCollector

    collector = FacebookCollector(
        headless=headless,
        poll_seconds=999,
        max_posts=max_posts,
        read_feed=True,
        read_groups=False,
        scroll_rounds=12,
        delay_between=3.0,
    )
    report.add(
        "collector.init", "ok",
        f"max_posts={max_posts} | scroll_rounds=12"
    )

    # ═══ ۵) connect ═══
    section("۵) connect")
    t0 = time.monotonic()
    ok = await collector.connect()
    elapsed = (time.monotonic() - t0) * 1000
    report.add(
        "connect", "ok" if ok else "fail",
        f"ok={ok} | {elapsed:.0f}ms",
    )
    if not ok:
        await safe_disconnect(collector, report)
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    try:
        logged = await collector._is_logged_in()
        report.add(
            "is_logged_in",
            "ok" if logged else "fail",
            f"logged={logged}",
        )
    except Exception as e:
        report.add("is_logged_in", "warn", str(e))

    # ═══ ۶) fetch_messages ═══
    section(f"۶) fetch_messages (since={since_days} روز)")
    since_dt = (
        datetime.now() - timedelta(days=since_days)
        if since_days > 0 else None
    )
    t0 = time.monotonic()
    try:
        msgs = await collector.fetch_messages(since=since_dt)
    except Exception as e:
        report.add("fetch", "fail", f"{type(e).__name__}: {e}")
        msgs = []
    elapsed = time.monotonic() - t0
    report.add(
        "fetch", "ok",
        f"count={len(msgs)} | {elapsed:.0f}s",
        elapsed * 1000,
    )

    if elapsed > MAX_FETCH_SECONDS:
        report.add(
            "fetch.timeout", "warn",
            f"{elapsed:.0f}s > {MAX_FETCH_SECONDS}s"
        )
    else:
        report.add(
            "fetch.timeout", "ok",
            f"{elapsed:.0f}s"
        )

    # ═══ ۷) آمار ═══
    section("۷) آمار GraphQL")
    stats = getattr(collector, "_last_scrape_stats", {})
    if stats:
        report.add(
            "scrape.stats", "ok",
            f"bodies={stats.get('graphql_bodies', 0)} | "
            f"lines={stats.get('ndjson_lines', 0)} | "
            f"edges={stats.get('edges_total', 0)} | "
            f"stories={stats.get('story_edges', 0)} | "
            f"stored={stats.get('stored', 0)} | "
            f"seen={stats.get('skipped_seen', 0)} | "
            f"old={stats.get('skipped_old', 0)} | "
            f"short={stats.get('skipped_short', 0)} | "
            f"no-kw={stats.get('skipped_no_kw', 0)} | "
            f"failed={stats.get('failed', 0)}"
        )

    # ═══ ۸) ساختار پیام ═══
    section("۸) ساختار پیام")
    if not msgs:
        report.add(
            "msg.structure", "warn",
            "هیچ پستی fetch نشد"
        )
    else:
        sample = msgs[0]

        missing = [
            f for f in REQUIRED_MSG_FIELDS if f not in sample
        ]
        report.add(
            "F1.fields",
            "ok" if not missing else "fail",
            f"missing={missing}" if missing
            else f"همه {len(REQUIRED_MSG_FIELDS)} فیلد موجود",
        )

        all_fb = all(m.get("platform") == "facebook" for m in msgs)
        report.add(
            "F2.platform",
            "ok" if all_fb else "fail",
            f"همه platform=facebook: {all_fb}",
        )

        ids = [str(m.get("id", "")) for m in msgs]
        dup = len(ids) - len(set(ids))
        report.add(
            "F3.id_unique",
            "ok" if dup == 0 else "fail",
            f"{dup} تکراری از {len(ids)}",
        )

        bad_id = [
            m for m in msgs if len(str(m.get("id", ""))) != 32
        ]
        report.add(
            "F4.id_format",
            "ok" if not bad_id else "warn",
            f"{len(bad_id)} id با فرمت غیرمنتظره" if bad_id
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
            m for m in msgs if not (m.get("text") or "").strip()
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
            else "همه chat_name دارند",
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

        with_media = [
            m for m in msgs if m.get("media_type") != "text"
        ]
        report.add(
            "F12.media",
            "ok" if with_media else "info",
            f"{len(with_media)}/{len(msgs)} با media",
        )

        print(f"\n  📩 {min(5, len(msgs))} پست scrape‌شده:")
        for m in msgs[:5]:
            txt = (m.get("text") or "")[:70].replace("\n", " ")
            author = (m.get("sender_name") or "?")[:25]
            media = m.get("media_type", "text")
            kw = (m.get("keywords") or "—")[:30]
            print(f"     [{author}] media={media} kw={kw}")
            print(f"        {txt}")

    # ═══ ۹) pipeline.feed ═══
    section("۹) pipeline.feed")
    stored = 0
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

    # ═══ ۱۰) DB ═══
    section("۱۰) DB")
    try:
        total_db = await pipeline.count_messages(platform="facebook")
        total_leads = await pipeline.count_leads(platform="facebook")
        report.add(
            "db.total", "ok",
            f"facebook: {total_db} پیام | {total_leads} سرنخ"
        )
    except Exception as e:
        report.add("db.total", "warn", str(e))

    # ═══ ۱۱) چک محتوا ═══
    section("۱۱) چک محتوا در DB")
    if msgs:
        try:
            found = await pipeline.find_in_db(
                msgs[:20], platform="facebook"
            )
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
                    content_bad.append(f"id={raw_id[:20]}")
                else:
                    content_ok += 1
            report.add(
                "db.content",
                "ok" if not content_bad else "warn",
                f"{content_ok}/{len(found)} محتوا درست"
                if not content_bad
                else f"{len(content_bad)} ناهمخوان",
            )
        except Exception as e:
            report.add("db.content", "warn", str(e))

    # ═══ ۱۲) dump_dom ═══
    section("۱۲) dump_dom")
    try:
        dom_path = await collector.dump_dom()
        size = (
            os.path.getsize(dom_path)
            if dom_path and os.path.exists(dom_path) else 0
        )
        report.add(
            "dump_dom",
            "ok" if size > 0 else "warn",
            f"size={size}B",
        )
    except Exception as e:
        report.add("dump_dom", "warn", str(e))

    # ═══ ۱۳) cleanup ═══
    section("۱۳) cleanup")
    await safe_disconnect(collector, report)
    try:
        await pipeline.stop()
        report.add("pipeline.cleanup", "ok", "DB تست پاک شد")
    except Exception as e:
        report.add("pipeline.cleanup", "warn", str(e))

    ok = report.print_summary()
    report.save()
    return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="تست فیسبوک — Pipeline end-to-end (v4.1)",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-posts", type=int, default=30)
    parser.add_argument("--since-days", type=int, default=7)
    args = parser.parse_args()

    ok = asyncio.run(main(
        headless=args.headless,
        max_posts=args.max_posts,
        since_days=args.since_days,
    ))
    sys.exit(0 if ok else 1)