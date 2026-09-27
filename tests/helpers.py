# tests/helpers.py
"""
توابع مشترک همه تست‌های پلتفرم + چک دیتابیس.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from datetime import datetime
from typing import Any

# ─── مسیرها ───
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.kernel.console_compat import patch_console
patch_console(verbose=False)


# ─── رنگ‌ها ───
class C:
    OK = "\033[92m"
    FAIL = "\033[91m"
    WARN = "\033[93m"
    INFO = "\033[96m"
    BOLD = "\033[1m"
    END = "\033[0m"


# ═════════════════════════════════════════════════════════
# 📊 کلاس گزارش تست
# ═════════════════════════════════════════════════════════
class TestReport:
    """گزارش نتیجه تست."""

    def __init__(self, platform: str):
        self.platform = platform
        self.started_at = datetime.now()
        self.steps: list[dict[str, Any]] = []
        self.t0 = time.monotonic()

    def add(
        self,
        name: str,
        status: str,  # "ok" | "fail" | "skip" | "warn"
        detail: str = "",
        elapsed_ms: float = 0.0,
    ):
        self.steps.append({
            "name": name,
            "status": status,
            "detail": detail[:500],
            "elapsed_ms": round(elapsed_ms, 1),
            "at": datetime.now().isoformat(timespec="seconds"),
        })
        icon = {
            "ok": f"{C.OK}✅{C.END}",
            "fail": f"{C.FAIL}❌{C.END}",
            "warn": f"{C.WARN}⚠️{C.END}",
            "skip": f"{C.INFO}⏭️{C.END}",
        }.get(status, "?")
        print(f"  {icon} [{name}] {detail}")

    def summary(self) -> dict[str, int]:
        counts = {"ok": 0, "fail": 0, "warn": 0, "skip": 0}
        for s in self.steps:
            counts[s["status"]] = counts.get(s["status"], 0) + 1
        return counts

    def print_summary(self) -> bool:
        counts = self.summary()
        elapsed = time.monotonic() - self.t0
        print()
        print("=" * 70)
        print(f"📊 گزارش نهایی تست: {self.platform}")
        print("=" * 70)
        print(f"  ✅ موفق:    {counts['ok']}")
        print(f"  ❌ ناموفق:  {counts['fail']}")
        print(f"  ⚠️  هشدار:  {counts['warn']}")
        print(f"  ⏭️  رد شده: {counts['skip']}")
        print(f"  ⏱️  زمان:    {elapsed:.1f}s")
        print("=" * 70)

        if counts["fail"] > 0:
            print(f"\n{C.FAIL}❌ تست‌های ناموفق:{C.END}")
            for s in self.steps:
                if s["status"] == "fail":
                    print(f"  • {s['name']}: {s['detail']}")
        return counts["fail"] == 0

    def save(self, path: str | None = None):
        if path is None:
            ts = self.started_at.strftime("%Y%m%d_%H%M%S")
            path = os.path.join(
                ROOT, "storage", "test_reports",
                f"test_{self.platform}_{ts}.json",
            )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "platform": self.platform,
                "started_at": self.started_at.isoformat(),
                "elapsed_s": round(time.monotonic() - self.t0, 1),
                "summary": self.summary(),
                "steps": self.steps,
            }, f, ensure_ascii=False, indent=2)
        print(f"\n📁 گزارش ذخیره شد: {path}")


# ═════════════════════════════════════════════════════════
# 🛠 ابزار تست
# ═════════════════════════════════════════════════════════
async def step(
    report: TestReport,
    name: str,
    coro_fn,
    *args,
    critical: bool = True,
    **kwargs,
):
    """اجرای یک مرحله با timing و لاگ."""
    t0 = time.monotonic()
    try:
        result = await coro_fn(*args, **kwargs)
        elapsed = (time.monotonic() - t0) * 1000
        detail = _short_repr(result)
        report.add(name, "ok", detail, elapsed)
        return result
    except Exception as e:
        elapsed = (time.monotonic() - t0) * 1000
        tb = traceback.format_exc().splitlines()[-3:]
        detail = f"{type(e).__name__}: {e} | {' / '.join(tb)}"
        report.add(
            name,
            "fail" if critical else "warn",
            detail,
            elapsed,
        )
        return None


def _short_repr(obj) -> str:
    if obj is None:
        return "None"
    if isinstance(obj, bool):
        return "✅ True" if obj else "❌ False"
    if isinstance(obj, (int, float)):
        return str(obj)
    if isinstance(obj, str):
        return obj[:100]
    if isinstance(obj, list):
        return f"list({len(obj)})"
    if isinstance(obj, dict):
        keys = list(obj.keys())[:5]
        return f"dict({len(obj)}) keys={keys}"
    return f"{type(obj).__name__}"


def banner(title: str):
    print()
    print("=" * 70)
    print(f"🧪 {title}")
    print("=" * 70)


def section(title: str):
    print()
    print(f"── {title} " + "─" * max(0, 60 - len(title)))


# ═════════════════════════════════════════════════════════
# چک‌های پیش‌نیاز
# ═════════════════════════════════════════════════════════
def check_env(platform: str) -> dict[str, Any]:
    """چک env برای پلتفرم."""
    from config import PLUGINS_CONFIG

    key = f"{platform}collector"
    cfg = PLUGINS_CONFIG.get(key, {})
    return {
        "enabled": cfg.get("enabled", False),
        "phone": cfg.get("phone", ""),
        "session_path": cfg.get("session_path", ""),
        "headless": cfg.get("headless", True),
        "raw": cfg,
    }


def check_session_exists(session_path: str) -> bool:
    """چک وجود نشست."""
    if not session_path:
        return False
    if os.path.isfile(session_path):
        return True
    if os.path.isdir(session_path):
        return any(os.scandir(session_path))
    return False


async def safe_disconnect(collector, report: TestReport):
    """قطع اتصال امن."""
    if collector is None:
        return
    try:
        await collector.disconnect()
        report.add("disconnect", "ok")
    except Exception as e:
        report.add("disconnect", "warn", str(e))


# ═════════════════════════════════════════════════════════
# 🆕 چک‌های دیتابیس
# ═════════════════════════════════════════════════════════
from tests.db_helpers import (
    db_available, db_size_kb, db_schema_version,
    count_messages, count_leads, check_ids_in_db,
    get_platforms_stats, get_recent_messages,
    find_message_by_text,
)


def report_db_summary(report: TestReport, platform: str) -> dict[str, Any]:
    """
    چک کلی DB قبل از تست.
    """
    info: dict[str, Any] = {}

    if not db_available():
        report.add("db.available", "fail", "DB not found")
        return info

    report.add("db.available", "ok", "messages.db موجود است")

    size = db_size_kb()
    report.add("db.size", "ok", f"{size:.1f}KB")

    version = db_schema_version()
    report.add(
        "db.schema",
        "ok" if version.startswith("v") or version.isdigit() else "warn",
        f"version={version}",
    )

    before = count_messages(platform=platform)
    report.add(
        "db.before",
        "ok",
        f"{platform}: {before} پیام قبل از تست",
    )
    info["before"] = before

    return info


def report_scrape_to_db(
    report: TestReport,
    platform: str,
    scraped_msgs: list[dict[str, Any]],
    sample_size: int = 5,
) -> dict[str, Any]:
    """
    چک می‌کند که پیام‌های scrape‌شده در DB ذخیره شده‌اند یا نه.
    """
    if not scraped_msgs:
        report.add(
            "db.scrape_none",
            "warn",
            "هیچ پیامی برای چک در DB وجود ندارد",
        )
        return {"total": 0, "in_db": 0, "missing": 0}

    result = check_ids_in_db(platform, scraped_msgs)

    total = result["total"]
    in_db = result["in_db"]
    missing = result["missing"]

    if in_db == total:
        status = "ok"
        detail = f"{in_db}/{total} پیام در DB ✅"
    elif in_db > 0:
        status = "warn"
        detail = f"{in_db}/{total} پیام در DB | {missing} missing"
    else:
        status = "fail"
        detail = f"0/{total} پیام در DB | همه missing!"

    report.add("db.scrape_saved", status, detail)

    if result["details_in_db"]:
        print(f"\n  📥 نمونهٔ پیام‌های ذخیره‌شده در DB:")
        for mid, info in list(result["details_in_db"].items())[:sample_size]:
            chat = (info.get("chat_name") or "?")[:25]
            txt = (info.get("text") or "")[:50]
            kw = info.get("keywords") or "—"
            print(f"     [{chat}] {txt}")
            print(f"        id={mid[:30]} | kw={kw[:40]}")

    if result["details_missing"]:
        print(f"\n  ❌ نمونهٔ پیام‌های ذخیره‌نشده:")
        for mid in result["details_missing"]:
            print(f"     id={mid[:50]}")

    after = count_messages(platform=platform)
    before = result.get("before", 0)
    added = after - before
    if added > 0:
        report.add(
            "db.new_rows", "ok",
            f"{added} پیام جدید به DB اضافه شد (total={after})",
        )

    return {
        "total": total,
        "in_db": in_db,
        "missing": missing,
        "ids_missing": result["ids_missing"],
        "ids_in_db": result["ids_in_db"],
    }


def report_keyword_verification(
    report: TestReport,
    platform: str,
    sample_kw: str = "دلفان پلاس",
) -> None:
    """چک می‌کند که پیام کلیدواژه‌دار در DB هست."""
    try:
        msg = find_message_by_text(platform, sample_kw)
        if msg:
            report.add(
                "db.keyword_lead",
                "ok",
                f"پیام با «{sample_kw}» در DB هست | "
                f"tier={msg.get('tier')} | kw={msg.get('keywords')}",
            )
            print(f"\n  🎯 پیدا شد در DB:")
            print(f"     id={msg['id']}")
            print(f"     chat={msg['chat_name']}")
            print(f"     text={(msg['text'] or '')[:60]}")
            print(f"     keywords={msg['keywords']}")
            print(f"     tier={msg['tier']}")
            print(f"     time={msg['timestamp']}")
        else:
            report.add(
                "db.keyword_lead",
                "warn",
                f"پیامی با «{sample_kw}» در DB {platform} پیدا نشد",
            )
    except Exception as e:
        report.add("db.keyword_lead", "warn", str(e))


def report_db_final_stats(report: TestReport, platform: str) -> None:
    """آمار نهایی DB برای پلتفرم."""
    try:
        total = count_messages(platform=platform)
        leads = count_leads(platform=platform)

        report.add(
            "db.stats.total", "ok",
            f"{platform}: {total} پیام کل",
        )
        report.add(
            "db.stats.leads", "ok",
            f"{platform}: {leads} سرنخ",
        )

        recent = get_recent_messages(platform, limit=3)
        if recent:
            print(f"\n  📋 آخرین ۳ پیام ذخیره‌شده در DB ({platform}):")
            for r in recent:
                print(f"     [{r['timestamp']}] {r['chat_name']}: {r['text']}")
    except Exception as e:
        report.add("db.stats", "warn", str(e))


def report_all_platforms(report: TestReport) -> None:
    """آمار همهٔ پلتفرم‌ها (برای خلاصهٔ نهایی)."""
    try:
        stats = get_platforms_stats()
        if not stats:
            report.add("db.all_platforms", "warn", "هیچ پلتفرمی در DB نیست")
            return

        print(f"\n  📊 آمار همهٔ پلتفرم‌ها:")
        print(f"  {'Platform':15s} | {'Total':>8s} | {'Leads':>8s}")
        print(f"  {'-'*15}-+-{'-'*8}-+-{'-'*8}")

        total_all = 0
        leads_all = 0
        for p in sorted(stats.keys()):
            t = stats[p]["total"]
            l = stats[p]["leads"]
            total_all += t
            leads_all += l
            print(f"  {p:15s} | {t:>8d} | {l:>8d}")

        print(f"  {'-'*15}-+-{'-'*8}-+-{'-'*8}")
        print(f"  {'TOTAL':15s} | {total_all:>8d} | {leads_all:>8d}")

        report.add(
            "db.all_platforms", "ok",
            f"{len(stats)} پلتفرم | total={total_all} | leads={leads_all}",
        )
    except Exception as e:
        report.add("db.all_platforms", "warn", str(e))