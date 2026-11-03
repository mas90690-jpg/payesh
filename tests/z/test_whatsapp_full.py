# tests/test_whatsapp_full.py
"""
تست کامل واتساپ — Pipeline end-to-end.

Pipeline:
  Collector → EventBus → StorageHandler → DB (isolated)

نسخهٔ ۱.۱ (این نسخه):
  - هم‌راستا با test_bale_full.py و test_eitaa_full.py
  - ⏭️ skip منطقی: اگر هیچ پیام کلیدواژه‌داری نبود → skip (نه fail)
  - ⏭️ skip منطقی: اگر پلتفرم خاموش بود → skip (نه fail) ← v1.1
  - 🎯 چک data-id جدید (فرمت ۲۰۲۶-۰۴-۳۰): فقط msgId خالی
  - 🎯 چک chat_id از header

اجرا:
  python tests/test_whatsapp_full.py
  python tests/test_whatsapp_full.py --headless
  python tests/test_whatsapp_full.py --max-chats 5
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import (
    TestReport, banner, check_env, check_session_exists,
    safe_disconnect, section, C,
)
from tests.pipeline_helpers import TestPipeline


async def main(headless: bool = False, max_chats: int = 0):
    banner("تست کامل واتساپ (WhatsApp) — Pipeline end-to-end")
    report = TestReport("whatsapp")

    # ═══ ۱) env ═══
    section("مرحله ۱: بررسی env و config")
    env_info = check_env("whatsapp")

    report.add(
        "env.enabled",
        "ok" if env_info["enabled"] else "skip",
        f"WHATSAPP_ENABLED={env_info['enabled']}",
    )
    if not env_info["enabled"]:
        print(
            f"{C.INFO}⏭️ واتساپ غیرفعال (skip) — "
            f"برای اجرا: WHATSAPP_ENABLED=true{C.END}"
        )
        report.print_summary()
        report.save()
        return True

    report.add(
        "env.session_path",
        "ok" if env_info["session_path"] else "warn",
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
    if not session_exists:
        print("\n  ⚠️ نشست واتساپ وجود ندارد")
        print("     یک‌بار اجرا کن: python login.py → گزینهٔ ۵ (واتساپ)")
        print("     سپس با گوشی QR را اسکن کن")

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

    # ═══ ۴) ساخت collector ═══
    section("مرحله ۴: ساخت collector")
    try:
        from collectors.whatsapp import WhatsAppCollector

        collector = WhatsAppCollector(
            headless=headless,
            poll_seconds=999,
            max_chats=max_chats,
            messages_per_chat=50,
            backfill_scrolls=0,
        )
        report.add("collector.init", "ok",
                   f"platform={collector.platform_name}")
    except Exception as e:
        report.add("collector.init", "fail", str(e))
        await pipeline.stop()
        report.print_summary()
        report.save()
        return False

    # ═══ ۵) connect ═══
    section("مرحله ۵: connect()")
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
            print(f"\n{C.WARN}⚠️ اتصال واتساپ شکست خورد.{C.END}")
            print("   راه‌حل: python login.py → گزینهٔ ۵")
            print("   ⚠️ در ایران ممکن است VPN (سایفون) لازم باشد")
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
            f"logged={logged} | url={collector._page.url[:80]}",
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
            "fetch_messages",
            "ok" if msgs is not None else "fail",
            f"count={len(msgs)} | {elapsed:.0f}ms",
            elapsed,
        )
    except Exception as e:
        report.add("fetch_messages", "fail", str(e))
        msgs = []

    # ═══ ۸) ساختار پیام‌ها ═══
    section("مرحله ۸: بررسی ساختار پیام‌ها")
    if msgs:
        sample = msgs[0]
        required_keys = [
            "id", "platform", "text", "chat_id", "chat_name",
            "chat_type", "timestamp", "media_type",
        ]
        missing = [k for k in required_keys if k not in sample]
        report.add(
            "msg.structure",
            "ok" if not missing else "warn",
            f"missing={missing}",
        )

        # 🎯 چک فرمت data-id (فرمت جدید ۲۰۲۶)
        sample_id = str(sample.get("id", ""))
        is_new_format = (
            len(sample_id) == 20
            and all(c in "0123456789ABCDEF" for c in sample_id.upper())
        )
        is_old_format = "_" in sample_id and "@" in sample_id

        if is_new_format:
            report.add(
                "msg.id_format", "ok",
                f"فرمت جدید (msgId خالی): {sample_id[:10]}... ✅",
            )
        elif is_old_format:
            report.add(
                "msg.id_format", "warn",
                f"فرمت قدیمی: {sample_id[:30]}...",
            )
        else:
            report.add(
                "msg.id_format", "warn",
                f"فرمت نامشخص: {sample_id[:30]}...",
            )

        print(f"\n  📩 {min(5, len(msgs))} پیام scrape‌شده:")
        for m in msgs[:5]:
            txt = (m.get("text") or "")[:60].replace("\n", " ")
            print(
                f"     [{m.get('chat_name', '?')[:25]}] "
                f"id={m.get('id', '?')[:20]} | {txt}"
            )

        platforms = {m.get("platform") for m in msgs}
        chats = {m.get("chat_name") for m in msgs}
        report.add(
            "msg.stats", "ok",
            f"platforms={platforms} | chats={len(chats)}",
        )

        # 🎯 چک chat_id (فرمت جدید)
        sample_chat_id = str(sample.get("chat_id", ""))
        if "@" in sample_chat_id:
            report.add(
                "msg.chat_id", "ok",
                f"chat_id از DOM: {sample_chat_id[:30]}",
            )
        elif sample_chat_id and len(sample_chat_id) > 3:
            report.add(
                "msg.chat_id", "ok",
                f"chat_id از header: {sample_chat_id[:30]}",
            )
        else:
            report.add(
                "msg.chat_id", "warn",
                "chat_id خالی یا نامعتبر",
            )
    else:
        report.add("msg.structure", "warn",
                   "هیچ پیامی fetch نشد")

    # ═══ ۹) feed pipeline ═══
    section("مرحله ۹: عبور از pipeline (ذخیره در DB)")
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

    # ═══ ۱۰) چک DB ═══
    section("مرحله ۱۰: چک ذخیره‌سازی در DB")

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
                msgs_with_kw, platform="whatsapp"
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
                    f"{in_db}/{total} کلیدواژه‌دار در DB "
                    f"({total - in_db} missing)",
                )
            else:
                report.add(
                    "db.scrape_saved", "fail",
                    f"0/{total} کلیدواژه‌دار در DB!",
                )

            if found:
                print("\n  📥 نمونهٔ leadهای ذخیره‌شده در DB:")
                for rid, row in list(found.items())[:5]:
                    chat = (row.get("chat_name") or "?")[:25]
                    txt = (row.get("text") or "")[:50]
                    kwv = row.get("keywords") or "—"
                    print(f"     [{chat}] {txt}")
                    print(f"        db_id={row.get('id', '')[:30]} | "
                          f"kw={kwv[:40]}")
        else:
            report.add(
                "db.scrape_saved", "skip",
                f"هیچ‌کدام از {len(msgs)} پیام scrape‌شده "
                "کلیدواژه ندارند (طبیعی — پیام‌های عمومی)",
            )
            print("\n  ℹ️ هیچ پیام کلیدواژه‌داری در این scrape نبود")
            print("     این طبیعی است — StorageHandler فقط "
                  "پیام‌های برند را ذخیره می‌کند")
            print("     ")
            print("     💡 برای تست کامل: یک پیام با برند "
                  "(مثل «یاراترا کریستا اس او پی») در واتساپ بفرست")
    else:
        report.add(
            "db.scrape_saved", "skip",
            "هیچ پیامی برای چک نیست",
        )

    # ═══ ۱۱) چک lead ═══
    section("مرحله ۱۱: چک lead (پیام‌های کلیدواژه‌دار)")

    try:
        total_db = await pipeline.count_messages(platform="whatsapp")
        total_leads = await pipeline.count_leads(platform="whatsapp")
        report.add(
            "db.total_messages", "ok",
            f"whatsapp: {total_db} پیام در DB تست",
        )
        report.add(
            "db.total_leads", "ok",
            f"whatsapp: {total_leads} سرنخ در DB تست",
        )

        if total_leads > 0:
            leads = await pipeline.find_leads_in_db("whatsapp")
            print(f"\n  🎯 {min(3, len(leads))} lead نمونه:")
            for ld in leads[:3]:
                chat = (ld.get("chat_name") or "?")[:25]
                kwv = (ld.get("keywords") or "—")[:40]
                tier = ld.get("opportunity_type") or "—"
                print(f"     [{chat}] kw={kwv} | tier={tier}")
    except Exception as e:
        report.add("db.leads_check", "warn", str(e))

    # ═══ ۱۲) DOM ═══
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

    # ═══ ۱۴) پاک‌سازی pipeline ═══
    section("مرحله ۱۴: پاک‌سازی pipeline")
    try:
        await pipeline.stop()
        report.add("pipeline.cleanup", "ok", "DB تست پاک شد")
    except Exception as e:
        report.add("pipeline.cleanup", "warn", str(e))

    ok = report.print_summary()
    report.save()
    return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-chats", type=int, default=0)
    args = parser.parse_args()

    ok = asyncio.run(
        main(headless=args.headless, max_chats=args.max_chats)
    )
    sys.exit(0 if ok else 1)