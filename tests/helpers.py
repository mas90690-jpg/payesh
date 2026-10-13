# tests/helpers.py
"""
توابع مشترک همهٔ تست‌های پلتفرم + چک دیتابیس.

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX: باگ منطقی در report_scrape_to_db — قبلاً `before` همیشه ۰ بود
    (چون check_ids_in_db اصلاً کلید "before" ندارد). حالا پارامتر
    `before_count` اضافه شد که تست‌ها باید از `info["count_before"]`
    پاس بدهند.
  - 🐛 FIX: check_session_exists حالا از context manager استفاده می‌کند
    (os.scandir بدون with → iterator leak).
  - 🎯 MOVE: from tests.db_helpers import به بالای فایل منتقل شد.
  - 🎯 RENAME: `info["before"]` → `info["count_before"]` (شفافیت).
  - 🎯 ADD: type hints کامل برای همهٔ توابع و متدها.
  - 🎯 ADD: `__all__` برای API عمومی.
  - 🎯 CLEAN: docstring بهتر + commentهای شفاف.
  - ✅ حفظ همهٔ API عمومی (backward-compat).

نکته: تابع `step()` هنوز در هیچ تستی استفاده نمی‌شود — به‌عنوان
      API عمومی نگه داشته شد (برای تست‌های آینده).
"""
from __future__ import annotations

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

# ─── این‌ها نیاز به ROOT در sys.path دارند ───
from core.kernel.console_compat import patch_console
from tests.db_helpers import (
    db_available, db_size_kb, db_schema_version,
    count_messages, count_leads, check_ids_in_db,
    get_platforms_stats, get_recent_messages,
    find_message_by_text,
)

patch_console(verbose=False)


__all__ = [
    # رنگ‌ها
    "C",
    # کلاس اصلی
    "TestReport",
    # ابزار
    "step", "banner", "section",
    # چک‌های پیش‌نیاز
    "check_env", "check_session_exists", "safe_disconnect",
    # چک‌های DB
    "report_db_summary", "report_scrape_to_db",
    "report_keyword_verification", "report_db_final_stats",
    "report_all_platforms",
]


# ═════════════════════════════════════════════════════════
# رنگ‌ها
# ═════════════════════════════════════════════════════════
class C:
    OK = "\033[92m"
    FAIL = "\033[91m"
    WARN = "\033[93m"
    INFO = "\033[96m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    END = "\033[0m"
    # 🎯 اضافه کن:
    YELLOW = "\033[93m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    GRAY = "\033[90m"
    WHITE = "\033[97m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"


# ═════════════════════════════════════════════════════════
# کلاس گزارش تست
# ═════════════════════════════════════════════════════════
class TestReport:
    """
    گزارش نتیجه تست.

    Methods:
        add(name, status, detail, elapsed_ms) → ثبت یک مرحله
        summary() → شمارش وضعیت‌ها
        print_summary() → چاپ خلاصه (return bool = موفقیت)
        save(path=None) → ذخیره JSON
    """

    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.started_at = datetime.now()
        self.steps: list[dict[str, Any]] = []
        self.t0 = time.monotonic()

    def add(
        self,
        name: str,
        status: str,       # "ok" | "fail" | "skip" | "warn" | "info"
        detail: str = "",
        elapsed_ms: float = 0.0,
    ) -> None:
        """ثبت یک مرحله در گزارش."""
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
            "info": f"{C.INFO}ℹ️{C.END}",
        }.get(status, "?")
        print(f"  {icon} [{name}] {detail}")

    def summary(self) -> dict[str, int]:
        """شمارش وضعیت‌ها."""
        counts: dict[str, int] = {
            "ok": 0, "fail": 0, "warn": 0, "skip": 0, "info": 0,
        }
        for s in self.steps:
            counts[s["status"]] = counts.get(s["status"], 0) + 1
        return counts

    def print_summary(self) -> bool:
        """چاپ خلاصه. Returns True اگر هیچ fail نبود."""
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

    def save(self, path: str | None = None) -> str:
        """ذخیرهٔ گزارش به JSON. Returns مسیر فایل."""
        if path is None:
            ts = self.started_at.strftime("%Y%m%d_%H%M%S")
            path = os.path.join(
                ROOT, "storage", "test_reports",
                f"test_{self.platform}_{ts}.json",
            )
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "platform": self.platform,
                    "started_at": self.started_at.isoformat(),
                    "elapsed_s": round(
                        time.monotonic() - self.t0, 1
                    ),
                    "summary": self.summary(),
                    "steps": self.steps,
                }, f, ensure_ascii=False, indent=2)
            print(f"\n📁 گزارش ذخیره شد: {path}")
        except Exception as e:
            print(f"\n⚠️ ذخیرهٔ گزارش ناموفق: {type(e).__name__}: {e}")
        return path


# ═════════════════════════════════════════════════════════
# ابزار تست
# ═════════════════════════════════════════════════════════
async def step(
    report: TestReport,
    name: str,
    coro_fn: Any,
    *args: Any,
    critical: bool = True,
    **kwargs: Any,
) -> Any:
    """
    اجرای یک مرحله با timing و لاگ.

    ⚠️ در حال حاضر در هیچ تستی استفاده نمی‌شود — API عمومی برای
    تست‌های آینده.

    Args:
        report: TestReport برای ثبت نتیجه.
        name: نام مرحله.
        coro_fn: تابع async که اجرا می‌شود.
        critical: اگر True → خطا fail است، وگرنه warn.
    """
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


def _short_repr(obj: Any) -> str:
    """نمایش کوتاه از یک شیء — برای لاگ."""
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
    if isinstance(obj, tuple):
        return f"tuple({len(obj)})"
    if isinstance(obj, set):
        return f"set({len(obj)})"
    if isinstance(obj, dict):
        keys = list(obj.keys())[:5]
        return f"dict({len(obj)}) keys={keys}"
    return f"{type(obj).__name__}"


def banner(title: str) -> None:
    """چاپ بنر بالای تست."""
    print()
    print("=" * 70)
    print(f"🧪 {title}")
    print("=" * 70)


def section(title: str) -> None:
    """چاپ بخش — با خط جداکننده."""
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
    """چک وجود نشست (فایل یا پوشهٔ غیرخالی)."""
    if not session_path:
        return False
    try:
        if os.path.isfile(session_path):
            return True
        if os.path.isdir(session_path):
            with os.scandir(session_path) as it:
                return any(it)
    except OSError:
        return False
    return False


async def safe_disconnect(
    collector: Any,
    report: TestReport,
) -> None:
    """قطع اتصال امن — با ثبت در report."""
    if collector is None:
        return
    try:
        await collector.disconnect()
        report.add("disconnect", "ok")
    except Exception as e:
        report.add("disconnect", "warn", str(e))


# ═════════════════════════════════════════════════════════
# چک‌های دیتابیس
# ═════════════════════════════════════════════════════════
def report_db_summary(
    report: TestReport,
    platform: str,
) -> dict[str, Any]:
    """
    چک کلی DB قبل از تست.

    Returns:
        dict با `count_before`: تعداد پیام‌های پلتفرم قبل از تست.
    """
    info: dict[str, Any] = {}

    if not db_available():
        report.add("db.available", "fail", "DB not found")
        return info

    report.add("db.available", "ok", "messages.db موجود است")

    size = db_size_kb()
    report.add("db.size", "ok", f"{size:.1f}KB")

    version = db_schema_version()
    schema_ok = (
        version.startswith("v")
        or version.startswith("migrations=")
        or version.isdigit()
    )
    report.add(
        "db.schema",
        "ok" if schema_ok else "warn",
        f"version={version}",
    )

    before = count_messages(platform=platform)
    report.add(
        "db.before",
        "ok",
        f"{platform}: {before} پیام قبل از تست",
    )
    info["count_before"] = before

    return info


def report_scrape_to_db(
    report: TestReport,
    platform: str,
    scraped_msgs: list[dict[str, Any]],
    sample_size: int = 5,
    before_count: int | None = None,
) -> dict[str, Any]:
    """
    چک می‌کند که پیام‌های scrape‌شده در DB ذخیره شده‌اند یا نه.

    Args:
        report: TestReport.
        platform: نام پلتفرم.
        scraped_msgs: پیام‌های scrape‌شده.
        sample_size: تعداد نمونه برای نمایش.
        before_count: تعداد پیام‌های DB قبل از تست — برای محاسبهٔ
            تعداد پیام‌های جدید. اگر None، «new_rows» گزارش نمی‌شود.

    Returns:
        dict با total, in_db, missing, ids_missing, ids_in_db.
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
        print("\n  📥 نمونهٔ پیام‌های ذخیره‌شده در DB:")
        for mid, info in list(result["details_in_db"].items())[:sample_size]:
            chat = (info.get("chat_name") or "?")[:25]
            txt = (info.get("text") or "")[:50]
            kw = info.get("keywords") or "—"
            print(f"     [{chat}] {txt}")
            print(f"        id={mid[:30]} | kw={kw[:40]}")

    if result["details_missing"]:
        print("\n  ❌ نمونهٔ پیام‌های ذخیره‌نشده:")
        for mid in result["details_missing"]:
            print(f"     id={mid[:50]}")

    # ─── پیام‌های جدید (اگه before_count داده شده) ───
    if before_count is not None:
        after = count_messages(platform=platform)
        added = after - before_count
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
            print("\n  🎯 پیدا شد در DB:")
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


def report_db_final_stats(
    report: TestReport,
    platform: str,
) -> None:
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
            print(
                "\n  📋 آخرین ۳ پیام ذخیره‌شده در DB "
                f"({platform}):"
            )
            for r in recent:
                print(
                    f"     [{r['timestamp']}] "
                    f"{r['chat_name']}: {r['text']}"
                )
    except Exception as e:
        report.add("db.stats", "warn", str(e))


def report_all_platforms(report: TestReport) -> None:
    """آمار همهٔ پلتفرم‌ها (برای خلاصهٔ نهایی)."""
    try:
        stats = get_platforms_stats()
        if not stats:
            report.add(
                "db.all_platforms", "warn",
                "هیچ پلتفرمی در DB نیست",
            )
            return

        print("\n  📊 آمار همهٔ پلتفرم‌ها:")
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
            f"{len(stats)} پلتفرم | total={total_all} | "
            f"leads={leads_all}",
        )
    except Exception as e:
        report.add("db.all_platforms", "warn", str(e))