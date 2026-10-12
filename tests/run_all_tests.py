# tests/run_all_tests.py
"""
اجرای همه تست‌های پلتفرم + StorageHandler.

اجرا:
  python tests/run_all_tests.py
  python tests/run_all_tests.py --only bale eitaa rubika youtube facebook
  python tests/run_all_tests.py --headless
  python tests/run_all_tests.py --include-disabled

نسخهٔ ۳.۰ (این نسخه):
  - 🆕 پشتیبانی از Facebook (env-based enabled check).
  - 🆕 Facebook در PLATFORMS list.
  - 🎯 `_is_platform_enabled` حالا برای هر دو youtube و facebook
    از env مستقیم استفاده می‌کند (چون ممکن است در PLUGINS_CONFIG
    نباشند یا `enabled` نداشته باشند).
  - 🎯 `run_test` حالا facebook را هم به لیست --headless اضافه می‌کند.
  - 🎯 بهبود لاگ: نمایش منبع enabled برای هر پلتفرم.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۲.۰.

نسخهٔ ۲.۰ (قبلی):
  - 🆕 پشتیبانی از YouTube (env-based enabled check).
  - 🆕 YouTube در PLATFORMS list.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۱.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.kernel.console_compat import patch_console
patch_console(verbose=False)


# ══════════════════════════════════════════════════════════════════════
# لیست پلتفرم‌ها (به ترتیب PLATFORMS_ORDER در config.py)
# ══════════════════════════════════════════════════════════════════════
PLATFORMS = [
    "bale",
    "eitaa",
    "rubika",
    "telegram",
    "whatsapp",
    "linkedin",
    "instagram",
    "youtube",
    "facebook",     # 🆕
    "web",
]


# ══════════════════════════════════════════════════════════════════════
# پلتفرم‌هایی که env-based enabled check دارند
# (چون ممکن است در PLUGINS_CONFIG نباشند یا مقدار enabled نداشته باشند)
# ══════════════════════════════════════════════════════════════════════
_ENV_BASED_PLATFORMS = {"youtube", "facebook"}


# ══════════════════════════════════════════════════════════════════════
# پلتفرم‌هایی که --headless را می‌پذیرند
# (کلکتورهای مرورگری که flag --headless دارند)
# ══════════════════════════════════════════════════════════════════════
_HEADLESS_CAPABLE = {
    "bale",
    "eitaa",
    "linkedin",
    "instagram",
    "telegram",
    "whatsapp",
    "youtube",
    "facebook",     # 🆕
}


def run_test(name: str, headless: bool = False) -> tuple[bool, float]:
    """اجرای یک تست در subprocess جدا."""
    script = os.path.join(ROOT, "tests", f"test_{name}_full.py")
    if not os.path.exists(script):
        print(f"  ⚠️ تست {name} وجود ندارد: {script}")
        return (False, 0.0)

    cmd = [sys.executable, script]
    if headless and name in _HEADLESS_CAPABLE:
        cmd.append("--headless")

    t0 = time.monotonic()
    try:
        rc = subprocess.call(cmd, cwd=ROOT)
        return (rc == 0, time.monotonic() - t0)
    except Exception as e:
        print(f"  ❌ خطا در اجرای {name}: {e}")
        return (False, time.monotonic() - t0)


def run_healthcheck() -> tuple[bool, float]:
    """اجرای Health Check جامع بعد از همهٔ تست‌ها."""
    script = os.path.join(ROOT, "tests", "test_healthcheck_full.py")
    if not os.path.exists(script):
        print(f"  ⚠️ Health Check وجود ندارد: {script}")
        return (True, 0.0)   # نبودنش خطا نیست

    print()
    print("═" * 70)
    print("🔬 اجرای Health Check جامع")
    print("═" * 70)

    t0 = time.monotonic()
    try:
        rc = subprocess.call([sys.executable, script], cwd=ROOT)
        return (rc == 0, time.monotonic() - t0)
    except Exception as e:
        print(f"  ❌ خطا در Health Check: {e}")
        return (False, time.monotonic() - t0)


def _is_platform_enabled(platform: str) -> bool:
    """آیا پلتفرم فعال است؟

    برای پلتفرم‌های env-based (youtube, facebook) → env مستقیم.
    برای بقیه → PLUGINS_CONFIG.

    اگر پلتفرم ناشناخته باشد یا config خطا داد → True (پیش‌فرض: تلاش).
    """
    # ─── اول: load .env برای env-based ───
    if platform in _ENV_BASED_PLATFORMS:
        try:
            from dotenv import load_dotenv
            load_dotenv(override=False)
        except Exception:
            pass

        env_name = f"{platform.upper()}_ENABLED"
        raw = os.getenv(env_name, "false").strip().lower()
        return raw in ("true", "1", "yes", "on")

    # ─── بقیه: از PLUGINS_CONFIG ───
    try:
        from config import PLUGINS_CONFIG
        key = f"{platform}collector"
        cfg = PLUGINS_CONFIG.get(key, {})
        return bool(cfg.get("enabled", False))
    except Exception:
        return True


def _enabled_source(platform: str) -> str:
    """منبع enabled برای این پلتفرم — برای نمایش در لاگ."""
    if platform in _ENV_BASED_PLATFORMS:
        return f"env:{platform.upper()}_ENABLED"
    return "config:PLUGINS_CONFIG"


def main():
    parser = argparse.ArgumentParser(
        description="اجرای همهٔ تست‌های پلتفرم + StorageHandler",
    )
    parser.add_argument(
        "--only", nargs="*", default=None,
        help="فقط این پلتفرم‌ها",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="اجرای مرورگرها در حالت headless (بدون پنجره)",
    )
    parser.add_argument(
        "--include-disabled", action="store_true",
        help="تست پلتفرم‌های خاموش را هم اجرا کن",
    )
    args = parser.parse_args()

    # ─── فیلتر پلتفرم‌های خاموش ───
    requested = args.only if args.only else PLATFORMS
    tests: list[str] = []
    skipped: list[str] = []

    for name in requested:
        if name not in PLATFORMS:
            print(f"  ⚠️ پلتفرم ناشناخته: {name} (skip)")
            skipped.append(name)
            continue

        if args.include_disabled:
            tests.append(name)
        elif _is_platform_enabled(name):
            tests.append(name)
        else:
            skipped.append(name)

    if skipped:
        print(f"⏭️  پلتفرم‌های خاموش/ناشناخته (skip): {', '.join(skipped)}")
        print("   (برای اجرا: --include-disabled)")

    if not tests:
        print("⚠️ هیچ پلتفرم فعالی برای تست نیست.")
        sys.exit(0)

    # ─── نمایش خلاصهٔ قبل از شروع ───
    print("=" * 70)
    print(f"🚀 اجرای {len(tests)} تست پلتفرم (Pipeline end-to-end)")
    print(f"   tests={tests} | headless={args.headless}")
    print("=" * 70)

    # ─── نمایش منبع enabled هر پلتفرم ───
    print("\n📋 منبع فعال بودن پلتفرم‌ها:")
    for name in tests:
        source = _enabled_source(name)
        print(f"   • {name:12s} → {source}")
    print()

    results: dict[str, tuple[bool, float]] = {}
    t_start = time.monotonic()

    for i, name in enumerate(tests, 1):
        print()
        print("═" * 70)
        print(f"▶️  [{i}/{len(tests)}] تست {name.upper()}")
        print("═" * 70)

        ok, elapsed = run_test(name, headless=args.headless)
        results[name] = (ok, elapsed)

        status = "✅" if ok else "❌"
        print(f"\n{status} نتیجه {name}: "
              f"{'موفق' if ok else 'ناموفق'} | {elapsed:.1f}s")

    # ─── خلاصه ───
    total_elapsed = time.monotonic() - t_start
    print()
    print("=" * 70)
    print("📊 خلاصهٔ نهایی")
    print("=" * 70)

    for name in tests:
        ok, elapsed = results.get(name, (False, 0.0))
        status = "✅" if ok else "❌"
        print(f"  {status} {name:12s} | {elapsed:6.1f}s")

    n_ok = sum(1 for ok, _ in results.values() if ok)
    n_fail = len(results) - n_ok

    print()
    print(f"  ✅ موفق:    {n_ok}")
    print(f"  ❌ ناموفق:  {n_fail}")
    print(f"  ⏱️  کل زمان: {total_elapsed:.1f}s")
    print("=" * 70)

    # ─── Health Check نهایی ───
    hc_ok, hc_elapsed = run_healthcheck()
    hc_status = "✅" if hc_ok else "❌"
    print(f"\n  {hc_status} Health Check | {hc_elapsed:.1f}s")

    all_ok = (n_fail == 0) and hc_ok
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()