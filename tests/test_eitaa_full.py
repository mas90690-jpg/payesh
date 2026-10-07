# tests/test_eitaa_full.py
"""
تست کامل ایتا — Pipeline end-to-end.

Pipeline:
  Collector → EventBus → StorageHandler → DB (isolated)

نسخهٔ ۳.۲ (این نسخه):
  - 🐛 FIX v3.1: مرحلهٔ ۱۰ حالا Saved Messages را فیلتر می‌کند
    (چون StorageHandler گارد ۰ دارد و پیام‌های Saved را رد می‌کند،
     پس نباید انتظار داشت که در DB باشند).
  - ⏭️ skip منطقی v3.1: اگر همهٔ پیام‌های کلیدواژه‌دار Saved بودند → skip
  - ⏭️ skip منطقی v3.1: اگر پلتفرم خاموش بود → skip (نه fail)
  - 🎯 v3.1: گزارش دقیق‌تر — تعداد Saved Messages رد شده
  - 🎯 v3.2: استفاده از keyword args در __main__ (خوانایی + استاندارد).
  - 🎯 v3.2: افزودن description به argparse (UX بهتر).
  - ✅ حفظ همهٔ مراحل قبلی.

مراحل:
  ۱) env و config
  ۲) نشست
  ۳) ساخت pipeline
  ۴) ساخت collector
  ۵) connect
  ۶) is_logged_in
  ۷) fetch_messages
  ۸) ساختار پیام‌ها
  ۹) feed pipeline (ذخیره در DB)
  ۱۰) چک DB (فقط کلیدواژه‌دارهای غیر-Saved)
  ۱۱) آمار DB
  ۱۲) dump_dom
  ۱۳) disconnect
  ۱۴) آمار نهایی pipeline

اجرا:
  python tests/test_eitaa_full.py
  python tests/test_eitaa_full.py --headless
  python tests/test_eitaa_full.py --max-chats 5
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
    TestReport, banner, check_env, check_session_exists,
    safe_disconnect, section, C,
)
from tests.pipeline_helpers import TestPipeline


# ═════════════════════════════════════════════════════════
# 🎯 فیلتر Saved Messages
# ═════════════════════════════════════════════════════════
_SAVED_MARKERS = (
    "پیام‌های ذخیره شده",
    "پیامهای ذخیره شده",
    "Saved Messages",
    "فضای شخصی",
    "ذخیره‌شده",
)


def _is_saved_message(msg: dict) -> bool:
    """آیا این پیام از Saved Messages / فضای شخصی است؟"""
    chat_name = (msg.get("chat_name") or "").strip()
    return any(marker in chat_name for marker in _SAVED_MARKERS)


# ═════════════════════════════════════════════════════════
# main
# ═════════════════════════════════════════════════════════
async def main(headless: bool = False, max_chats: int = 0):
    banner("تست کامل ایتا (Eitaa) — Pipeline end-to-end")
    report = TestReport("eitaa")

    # ═══ ۱) env ═══
    section("مرحله ۱: بررسی env و config")
    env_info = check_env("eitaa")

    report.add(
        "env.enabled",
        "ok" if env_info["enabled"] else "skip",
        f"EITAA_ENABLED={env_info['enabled']}",
    )
    if not env_info["enabled"]:
        print(
            f"{C.INFO}⏭️ ایتا غیرفعال (skip) — "
            f"برای اجرا: EITAA_ENABLED=true{C.END}"
        )
        report.print_summary()
        report.save()
        return True

    report.add(
        "env.phone",
        "ok" if env_info["phone"] else "fail",
        f"EITAA_PHONE={env_info['phone'][:6]}...",
    )
    report.add(
        "env.session_path",
        "ok" if env_info["session_path"] else "fail",
        env_info["session_path"],
    )

    # ═══ ۲) نشست ═══
    section("مرحله ۲: بررسی نشست")
    session_exists = check_session_exists(env_info["session_path"])
    report.add(
        "session.exists",
        "ok" if session_exists else "warn",
        f"exists={session_exists}",
    )

    # ═══ ۳) pipeline ═══
    section("مرحله ۳: ساخت pipeline end-to-end")
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
        from collectors.eitaa import EitaaCollector
        collector = EitaaCollector(
            headless=headless,
            poll_seconds=999,
            max_chats=max_chats,
            messages_per_chat=30,
            backfill_scrolls=0,
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
    section("مرحله ۵: connect")
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
            print(
                f"\n{C.WARN}⚠️ ایتا وصل نشد — "
                f"`python login.py` گزینهٔ ۳{C.END}"
            )
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
        report.add(
            "is_logged_in",
            "ok" if logged else "fail",
            f"logged={logged}",
        )
    except Exception as e:
        report.add("is_logged_in", "warn", str(e))

    # ═══ ۷) fetch_messages ═══
    section("مرحله ۷: fetch_messages")
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

    # ═══ ۸) ساختار ═══
    section("مرحله ۸: بررسی ساختار پیام‌ها")
    if msgs:
        sample = msgs[0]
        required = ["id", "platform", "text", "chat_name", "timestamp"]
        missing = [k for k in required if k not in sample]
        report.add(
            "msg.structure",
            "ok" if not missing else "warn",
            f"missing={missing}",
        )

        print(f"\n  📩 {min(5, len(msgs))} پیام scrape‌شده:")
        for m in msgs[:5]:
            txt = (m.get("text") or "")[:60].replace("\n", " ")
            is_saved = "💾" if _is_saved_message(m) else "  "
            print(
                f"     {is_saved} [{m.get('chat_name', '?')[:25]}] "
                f"id={m.get('id', '?')[:20]} | {txt}"
            )

        platforms = {m.get("platform") for m in msgs}
        chats = {m.get("chat_name") for m in msgs}
        report.add(
            "msg.stats", "ok",
            f"platforms={platforms} | chats={len(chats)}",
        )
    else:
        report.add("msg.structure", "warn", "هیچ پیامی نیامد")

    # ═══ ۹) pipeline.feed ═══
    section("مرحله ۹: عبور از pipeline")
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

    # ═══ ۱۰) چک DB ═══
    section("مرحله ۱۰: چک ذخیره‌سازی در DB")
    if msgs:
        from analyzers.keyword_finder import get_keyword_finder
        kw = get_keyword_finder()

        # ─── فیلتر کلیدواژه ───
        msgs_kw = [
            m for m in msgs
            if kw.has_match(m.get("text") or "")
        ]

        # ─── فیلتر Saved Messages ───
        saved_count = sum(1 for m in msgs if _is_saved_message(m))
        saved_kw_count = sum(
            1 for m in msgs_kw if _is_saved_message(m)
        )

        msgs_with_kw = [
            m for m in msgs_kw
            if not _is_saved_message(m)
        ]

        # ─── گزارش آمار ───
        report.add(
            "db.scrape_stats", "ok",
            f"{len(msgs)} scrape | "
            f"{len(msgs_kw)} کلیدواژه‌دار | "
            f"{saved_count} Saved "
            f"({saved_kw_count} از آن‌ها کلیدواژه‌دار)",
        )

        # ─── چک DB ───
        if msgs_with_kw:
            found = await pipeline.find_in_db(
                msgs_with_kw, platform="eitaa"
            )
            total = len(msgs_with_kw)
            in_db = len(found)

            if in_db == total:
                report.add(
                    "db.scrape_saved", "ok",
                    f"{in_db}/{total} کلیدواژه‌دار (غیر-Saved) "
                    f"در DB ✅",
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
                    print(
                        f"     [{row.get('chat_name', '?')[:25]}] "
                        f"{(row.get('text') or '')[:50]}"
                    )
                    print(
                        f"        db_id={row.get('id', '')[:30]} | "
                        f"kw={row.get('keywords', '—')[:40]}"
                    )
        else:
            # ─── هیچ پیام کلیدواژه‌داری نبود ───
            if saved_kw_count > 0:
                # همه از Saved بودند
                report.add(
                    "db.scrape_saved", "skip",
                    f"تنها {saved_kw_count} پیام کلیدواژه‌دار از "
                    f"Saved Messages بود (StorageHandler گارد ۰ دارد)",
                )
                print(
                    f"\n  ℹ️ {saved_kw_count} پیام کلیدواژه‌دار در "
                    f"Saved Messages پیدا شد"
                )
                print(
                    f"     StorageHandler گارد ۰ دارد و آن‌ها را "
                    f"رد می‌کند"
                )
                print(
                    f"     این طبیعی است — Saved Messages منبع "
                    f"واقعی نیست"
                )
            elif msgs_kw:
                # کلیدواژه داشت ولی به هر دلیل حذف شد
                report.add(
                    "db.scrape_saved", "skip",
                    f"{len(msgs_kw)} کلیدواژه‌دار بود ولی هیچ‌کدام "
                    f"به DB نرسیدند",
                )
            else:
                # هیچ کلیدواژه‌داری نبود
                report.add(
                    "db.scrape_saved", "skip",
                    f"هیچ‌کدام از {len(msgs)} پیام scrape‌شده "
                    f"کلیدواژه ندارند",
                )
                print(
                    f"\n  ℹ️ هیچ پیام کلیدواژه‌داری در این scrape "
                    f"نبود"
                )
                print(
                    f"     💡 برای تست کامل: پیام با برند "
                    f"(مثل «یاراترا کریستا اس او پی») در ایتا بفرست"
                )
    else:
        report.add(
            "db.scrape_saved", "skip",
            "هیچ پیامی برای چک نیست",
        )

    # ═══ ۱۱) آمار DB ═══
    section("مرحله ۱۱: آمار DB")
    try:
        total_db = await pipeline.count_messages(platform="eitaa")
        total_leads = await pipeline.count_leads(platform="eitaa")
        report.add(
            "db.total_messages", "ok",
            f"eitaa: {total_db} پیام در DB تست",
        )
        report.add(
            "db.total_leads", "ok",
            f"eitaa: {total_leads} سرنخ در DB تست",
        )

        if total_leads > 0:
            leads = await pipeline.find_leads_in_db("eitaa")
            print(f"\n  🎯 {min(3, len(leads))} lead نمونه:")
            for ld in leads[:3]:
                chat = (ld.get("chat_name") or "?")[:25]
                kwv = (ld.get("keywords") or "—")[:40]
                tier = ld.get("opportunity_type") or "—"
                print(f"     [{chat}] kw={kwv} | tier={tier}")
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

    # ═══ نتیجه ═══
    ok = report.print_summary()
    report.save()
    return ok


# ═════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="تست کامل ایتا — Pipeline end-to-end",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="اجرای مرورگر در حالت headless (بدون پنجره)",
    )
    parser.add_argument(
        "--max-chats", type=int, default=0,
        help="حداکثر تعداد چت برای scrape (0 = بدون محدودیت)",
    )
    args = parser.parse_args()

    ok = asyncio.run(
        main(
            headless=args.headless,
            max_chats=args.max_chats,
        )
    )
    sys.exit(0 if ok else 1)