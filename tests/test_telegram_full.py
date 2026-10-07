# tests/test_telegram_full.py
"""
تست کامل تلگرام — Pipeline end-to-end (نسخهٔ مرورگری).

Pipeline:
  Collector → EventBus → StorageHandler → DB (isolated)

نسخهٔ ۲.۱ (این نسخه — TelegramWeb):
  - 🎯 استفاده از TelegramWebCollector (مرورگر، مثل بله/ایتا)
  - ❌ حذف نیاز به api_id/api_hash (دیگه از my.telegram.org نمی‌خواد)
  - ✅ فقط نیاز به نشست ذخیره‌شده (python login.py → گزینه تلگرام وب)
  - ✅ سازگار با ساختار test_bale_full.py و test_eitaa_full.py
  - ⏭️ skip منطقی: اگر هیچ پیام کلیدواژه‌داری نبود → skip (نه fail)
  - ⏭️ skip منطقی: اگر پلتفرم خاموش بود → skip (نه fail) ← v2.1

پیش‌نیازها:
  - TELEGRAM_ENABLED=true
  - نشست ذخیره‌شده در storage/sessions/telegram_web/
  - VPN روشن (web.telegram.org روی IP ایران بسته است)

اجرا:
  python tests/test_telegram_full.py
  python tests/test_telegram_full.py --headless
  python tests/test_telegram_full.py --max-chats 5
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


# ═════════════════════════════════════════════════════════
# مسیر نشست مرورگر تلگرام
# ═════════════════════════════════════════════════════════
def _telegram_web_session_path() -> str:
    """مسیر user_data_dir مرورگر تلگرام."""
    try:
        from config import STORAGE_DIR
        return os.path.join(
            STORAGE_DIR, "sessions", "telegram_web"
        )
    except Exception:
        root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        return os.path.join(
            root, "storage", "sessions", "telegram_web"
        )


def _session_has_data(path: str) -> bool:
    """آیا پوشهٔ نشست محتوای واقعی دارد؟"""
    if not path or not os.path.isdir(path):
        return False
    try:
        entries = [
            e for e in os.listdir(path)
            if not e.startswith(".")
        ]
        return len(entries) > 0
    except Exception:
        return False


# ═════════════════════════════════════════════════════════
# main
# ═════════════════════════════════════════════════════════
async def main(headless: bool = False, max_chats: int = 0):
    banner("تست کامل تلگرام (Telegram Web) — Pipeline end-to-end")
    report = TestReport("telegram")

    # ═══ ۱) env ═══
    section("مرحله ۱: بررسی env و config")
    env_info = check_env("telegram")
    raw = env_info.get("raw", {})

    report.add(
        "env.enabled",
        "ok" if env_info["enabled"] else "skip",
        f"TELEGRAM_ENABLED={env_info['enabled']}",
    )
    if not env_info["enabled"]:
        print(
            f"{C.INFO}⏭️ تلگرام غیرفعال (skip) — "
            f"برای اجرا: TELEGRAM_ENABLED=true{C.END}"
        )
        report.print_summary()
        report.save()
        return True

    # ─── توضیح نسخهٔ جدید ───
    phone = (raw.get("phone") or "").strip()
    report.add(
        "env.mode", "ok",
        "mode=web (مرورگر) — بدون نیاز به api_id/api_hash",
    )
    if phone:
        report.add(
            "env.phone", "ok",
            f"phone={phone[:6]}...",
        )
    else:
        report.add(
            "env.phone", "warn",
            "TELEGRAM_PHONE خالی (اختیاری در نسخهٔ مرورگری)",
        )

    # ═══ ۲) نشست مرورگر ═══
    section("مرحله ۲: بررسی نشست مرورگر")
    session_path = _telegram_web_session_path()
    report.add(
        "session.path", "ok",
        session_path,
    )
    session_exists = _session_has_data(session_path)
    report.add(
        "session.exists",
        "ok" if session_exists else "fail",
        f"exists={session_exists}",
    )
    if not session_exists:
        print(f"\n{C.FAIL}❌ نشست تلگرام‌وب وجود ندارد{C.END}")
        print(f"   راه‌حل:")
        print(f"     ۱) VPN را روشن کن")
        print(f"     ۲) python login.py → گزینه تلگرام وِب")
        print(f"     ۳) در مرورگر با شماره و کد پیامکی وارد شو")
        print(f"     ۴) بعد دوباره این تست را اجرا کن")
        report.print_summary()
        report.save()
        return False

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
        from collectors.telegram import TelegramWebCollector
        collector = TelegramWebCollector(
            phone=phone or None,
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
            print(f"\n{C.WARN}⚠️ اتصال تلگرام‌وب شکست خورد{C.END}")
            print(f"   راه‌حل:")
            print(f"     ۱) VPN را چک کن (web.telegram.org)")
            print(f"     ۲) python login.py → گزینه تلگرام وِب")
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
        url = ""
        try:
            url = collector._page.url[:80] if collector._page else ""
        except Exception:
            pass
        report.add(
            "is_logged_in",
            "ok" if logged else "fail",
            f"logged={logged} | url={url}",
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

        print(f"\n  📩 {min(5, len(msgs))} پیام scrape‌شده:")
        for m in msgs[:5]:
            txt = (m.get("text") or "")[:60].replace("\n", " ")
            ctype = m.get("chat_type", "?")
            print(
                f"     [{m.get('chat_name', '?')[:25]}] "
                f"({ctype}) id={m.get('id', '?')[:20]} | {txt}"
            )

        platforms = {m.get("platform") for m in msgs}
        chats = {m.get("chat_name") for m in msgs}
        chat_types = {m.get("chat_type") for m in msgs}
        report.add(
            "msg.stats", "ok",
            f"platforms={platforms} | chats={len(chats)} | "
            f"types={chat_types}",
        )
    else:
        report.add(
            "msg.structure", "warn",
            "هیچ پیامی fetch نشد (طبیعی اگر چت جدیدی نبود)",
        )

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

    # ═══ ۱۰) چک DB — فقط کلیدواژه‌دارها ═══
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
                msgs_with_kw, platform="telegram"
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
                    f"{in_db}/{total} کلیدواژه‌دار در DB",
                )
            else:
                report.add(
                    "db.scrape_saved", "fail",
                    f"0/{total} کلیدواژه‌دار در DB!",
                )

            if found:
                print(f"\n  📥 نمونهٔ leadهای ذخیره‌شده در DB:")
                for rid, row in list(found.items())[:5]:
                    chat = (row.get("chat_name") or "?")[:25]
                    txt = (row.get("text") or "")[:50]
                    kwv = row.get("keywords") or "—"
                    print(f"     [{chat}] {txt}")
                    print(
                        f"        db_id={row.get('id', '')[:30]} | "
                        f"kw={kwv[:40]}"
                    )
        else:
            report.add(
                "db.scrape_saved", "skip",
                f"هیچ‌کدام از {len(msgs)} پیام کلیدواژه ندارند",
            )
            print(f"\n  ℹ️ هیچ پیام کلیدواژه‌داری در این scrape نبود")
            print(f"     💡 برای تست کامل: پیام با برند "
                  f"(مثل «یاراترا کریستا اس او پی») در یه چت بفرست")
    else:
        report.add(
            "db.scrape_saved", "skip",
            "هیچ پیامی برای چک نیست",
        )

    # ═══ ۱۱) چک lead ═══
    section("مرحله ۱۱: چک lead (پیام‌های کلیدواژه‌دار)")
    try:
        total_db = await pipeline.count_messages(platform="telegram")
        total_leads = await pipeline.count_leads(platform="telegram")
        report.add(
            "db.total_messages", "ok",
            f"telegram: {total_db} پیام در DB تست",
        )
        report.add(
            "db.total_leads", "ok",
            f"telegram: {total_leads} سرنخ در DB تست",
        )

        if total_leads > 0:
            leads = await pipeline.find_leads_in_db("telegram")
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

    # ═══ ۱۴) پاک‌سازی pipeline ═══
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-chats", type=int, default=0)
    args = parser.parse_args()

    ok = asyncio.run(
        main(headless=args.headless, max_chats=args.max_chats)
    )
    sys.exit(0 if ok else 1)