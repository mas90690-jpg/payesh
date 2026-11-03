# tests/run_all_tests.py
"""
اجرای همهٔ تست‌ها (unit + integration + e2e) — v4.0

اجرا:
  # فقط unit tests (سریع، بدون شبکه — همیشه اجرا شود)
  python tests/run_all_tests.py --unit

  # فقط integration tests (نیاز به session + شبکه)
  python tests/run_all_tests.py --integration

  # فقط e2e (کل پروژه)
  python tests/run_all_tests.py --e2e

  # همه لایه‌ها
  python tests/run_all_tests.py --all

  # فقط یک پلتفرم خاص
  python tests/run_all_tests.py --platform bale

  # فقط پلتفرم‌های فعال (env-based)
  python tests/run_all_tests.py --unit --only-enabled

  # حالت headless
  python tests/run_all_tests.py --all --headless

  # اجرای همه حتی خاموش‌ها
  python tests/run_all_tests.py --all --include-disabled

نسخهٔ ۴.۰:
  - 🆕 سه لایهٔ مستقل: unit / integration / e2e
  - 🆕 `--unit` — فقط tests/unit/ (بدون شبکه، < 5s)
  - 🆕 `--integration` — فقط tests/integration/ (با شبکه)
  - 🆕 `--e2e` — فقط tests/e2e/ (کل پروژه)
  - 🆕 `--all` — همه لایه‌ها
  - 🆕 `--platform bale` — فقط یک پلتفرم
  - 🆕 `--only-enabled` — فقط پلتفرم‌های env-enabled
  - 🎯 نگهداری run_test قدیمی (سازگاری با tests/platform/)
  - 🎯 تشخیص خودکار لایه‌ها از روی دایرکتوری
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# ─── Project root ───
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.kernel.console_compat import patch_console
patch_console(verbose=False)


# ══════════════════════════════════════════════════════════════════════
# پلتفرم‌ها (به ترتیب config.py)
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
    "facebook",
    "web",
]

# پلتفرم‌هایی که از env برای enabled check استفاده می‌کنند
_ENV_BASED_PLATFORMS = {"youtube", "facebook"}

# پلتفرم‌هایی که --headless دارند
_HEADLESS_CAPABLE = {
    "bale", "eitaa", "linkedin", "instagram",
    "telegram", "whatsapp", "youtube", "facebook",
}

# مسیرهای تست به‌ازای هر لایه
_LAYER_DIRS = {
    "unit": ROOT / "tests" / "unit",
    "integration": ROOT / "tests" / "integration",
    "e2e": ROOT / "tests" / "e2e",
}

# مسیر قدیمی (tests/platform/test_*.py)
_LEGACY_PLATFORM_DIR = ROOT / "tests" / "platform"


# ══════════════════════════════════════════════════════════════════════
# Enabled helpers
# ══════════════════════════════════════════════════════════════════════
def _is_platform_enabled(platform: str) -> bool:
    """آیا پلتفرم فعال است؟

    env-based (youtube, facebook) → env مستقیم.
    بقیه → PLUGINS_CONFIG.
    خطا → True (پیش‌فرض: تلاش).
    """
    if platform in _ENV_BASED_PLATFORMS:
        try:
            from dotenv import load_dotenv
            load_dotenv(override=False)
        except Exception:
            pass

        env_name = f"{platform.upper()}_ENABLED"
        raw = os.getenv(env_name, "false").strip().lower()
        return raw in ("true", "1", "yes", "on")

    try:
        from config import PLUGINS_CONFIG
        key = f"{platform}collector"
        cfg = PLUGINS_CONFIG.get(key, {})
        return bool(cfg.get("enabled", False))
    except Exception:
        return True


def _enabled_source(platform: str) -> str:
    """منبع enabled برای لاگ."""
    if platform in _ENV_BASED_PLATFORMS:
        return f"env:{platform.upper()}_ENABLED"
    return "config:PLUGINS_CONFIG"


# ══════════════════════════════════════════════════════════════════════
# Test runners
# ══════════════════════════════════════════════════════════════════════
def _run_pytest(
    paths: list[Path],
    markers: list[str] | None = None,
    timeout: int = 600,
) -> tuple[bool, float, int, int]:
    """اجرای pytest روی چند مسیر.

    Returns: (ok, elapsed, passed, failed)
    """
    if not paths:
        return (True, 0.0, 0, 0)

    cmd = [
        sys.executable, "-m", "pytest",
        *[str(p) for p in paths],
        "-v", "--tb=short",
        f"--timeout={timeout}",
    ]
    if markers:
        for m in markers:
            cmd.extend(["-m", m])

    t0 = time.monotonic()
    try:
        rc = subprocess.call(cmd, cwd=str(ROOT))
        elapsed = time.monotonic() - t0
        return (rc == 0, elapsed, 0, 0)
    except FileNotFoundError:
        print(f"  ❌ pytest پیدا نشد — نصب کن: pip install pytest pytest-asyncio pytest-timeout")
        return (False, 0.0, 0, 0)
    except Exception as e:
        print(f"  ❌ خطا در اجرا: {e}")
        return (False, time.monotonic() - t0, 0, 0)


def run_unit_tests(
    only_platform: str | None = None,
) -> tuple[bool, float]:
    """اجرای unit tests (tests/unit/)."""
    unit_dir = _LAYER_DIRS["unit"]
    if not unit_dir.exists():
        print(f"  ⚠️ پوشهٔ unit وجود ندارد: {unit_dir}")
        return (False, 0.0)

    print()
    print("═" * 70)
    print("🟢 لایهٔ UNIT (بدون شبکه — سریع)")
    print("═" * 70)

    if only_platform:
        # فقط test_<platform>.py
        target = unit_dir / f"test_{only_platform}.py"
        paths = [target] if target.exists() else []
        if not paths:
            print(f"  ⚠️ تست unit برای {only_platform} پیدا نشد: {target}")
            return (False, 0.0)
    else:
        paths = [unit_dir]

    ok, elapsed, _, _ = _run_pytest(paths)
    status = "✅" if ok else "❌"
    print(f"\n{status} UNIT | {elapsed:.1f}s")
    return (ok, elapsed)


def run_integration_tests(
    only_platform: str | None = None,
    headless: bool = False,
) -> tuple[bool, float]:
    """اجرای integration tests (tests/integration/)."""
    int_dir = _LAYER_DIRS["integration"]
    if not int_dir.exists():
        print(f"  ⚠️ پوشهٔ integration وجود ندارد: {int_dir}")
        return (False, 0.0)

    print()
    print("═" * 70)
    print("🔵 لایهٔ INTEGRATION (نیاز به session + شبکه)")
    print("═" * 70)

    if only_platform:
        # test_<platform>_full.py
        target = int_dir / f"test_{only_platform}_full.py"
        paths = [target] if target.exists() else []
        if not paths:
            print(f"  ⚠️ تست integration برای {only_platform} پیدا نشد: {target}")
            return (False, 0.0)
    else:
        paths = [int_dir]

    # integration نیاز به BALE_INTEGRATION=1 (و مشابه)
    ok, elapsed, _, _ = _run_pytest(
        paths,
        markers=["integration"],
        timeout=900,  # 15 دقیقه
    )
    status = "✅" if ok else "❌"
    print(f"\n{status} INTEGRATION | {elapsed:.1f}s")
    return (ok, elapsed)


def run_e2e_tests() -> tuple[bool, float]:
    """اجرای e2e tests (tests/e2e/)."""
    e2e_dir = _LAYER_DIRS["e2e"]
    if not e2e_dir.exists():
        print(f"  ⚠️ پوشهٔ e2e وجود ندارد: {e2e_dir}")
        return (True, 0.0)  # نبودنش خطا نیست

    print()
    print("═" * 70)
    print("🟣 لایهٔ E2E (کل پروژه)")
    print("═" * 70)

    ok, elapsed, _, _ = _run_pytest([e2e_dir], timeout=300)
    status = "✅" if ok else "❌"
    print(f"\n{status} E2E | {elapsed:.1f}s")
    return (ok, elapsed)


# ══════════════════════════════════════════════════════════════════════
# Legacy: tests/platform/test_*.py (سازگاری)
# ══════════════════════════════════════════════════════════════════════
def run_legacy_platform_test(
    name: str, headless: bool = False
) -> tuple[bool, float]:
    """اجرای تست قدیمی (tests/platform/test_<name>.py)."""
    script = _LEGACY_PLATFORM_DIR / f"test_{name}.py"
    if not script.exists():
        return (True, 0.0)  # نبودنش خطا نیست

    cmd = [
        sys.executable, "-m", "pytest",
        str(script), "-v", "-m", "platform",
    ]
    t0 = time.monotonic()
    try:
        rc = subprocess.call(cmd, cwd=str(ROOT))
        return (rc == 0, time.monotonic() - t0)
    except Exception:
        return (False, time.monotonic() - t0)


# ══════════════════════════════════════════════════════════════════════
# Healthcheck (اختیاری)
# ══════════════════════════════════════════════════════════════════════
def run_healthcheck() -> tuple[bool, float]:
    """اجرای Health Check جامع (اگر باشد)."""
    for candidate in (
        ROOT / "tests" / "test_healthcheck_full.py",
        ROOT / "tests" / "e2e" / "test_healthcheck_full.py",
    ):
        if candidate.exists():
            print()
            print("═" * 70)
            print("🔬 Health Check جامع")
            print("═" * 70)

            t0 = time.monotonic()
            try:
                rc = subprocess.call(
                    [sys.executable, str(candidate)],
                    cwd=str(ROOT),
                )
                return (rc == 0, time.monotonic() - t0)
            except Exception as e:
                print(f"  ❌ خطا: {e}")
                return (False, time.monotonic() - t0)

    return (True, 0.0)  # نبودنش خطا نیست


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="اجرای تست‌های پروژه — unit / integration / e2e",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
مثال‌ها:
  python tests/run_all_tests.py --unit
  python tests/run_all_tests.py --integration
  python tests/run_all_tests.py --all
  python tests/run_all_tests.py --unit --platform bale
  python tests/run_all_tests.py --all --headless
  python tests/run_all_tests.py --unit --only-enabled
        """,
    )

    # لایه‌ها
    parser.add_argument(
        "--unit", action="store_true",
        help="اجرای فقط unit tests (سریع، بدون شبکه)",
    )
    parser.add_argument(
        "--integration", action="store_true",
        help="اجرای فقط integration tests (نیاز به session)",
    )
    parser.add_argument(
        "--e2e", action="store_true",
        help="اجرای فقط e2e tests (کل پروژه)",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="اجرای همه لایه‌ها (unit + integration + e2e)",
    )

    # فیلترها
    parser.add_argument(
        "--platform", default=None,
        choices=PLATFORMS,
        help="فقط یک پلتفرم خاص",
    )
    parser.add_argument(
        "--only-enabled", action="store_true",
        help="فقط پلتفرم‌های env-enabled (youtube, facebook, ...)",
    )
    parser.add_argument(
        "--include-disabled", action="store_true",
        help="اجرای همه حتی خاموش‌ها",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="مرورگر در حالت headless",
    )
    parser.add_argument(
        "--healthcheck", action="store_true",
        help="اجرای Health Check در پایان",
    )

    args = parser.parse_args()

    # اگر هیچ لایه‌ای انتخاب نشد → پیش‌فرض unit
    if not (args.unit or args.integration or args.e2e or args.all):
        args.unit = True
        print("ℹ️  هیچ لایه‌ای انتخاب نشد — فقط unit اجرا می‌شود")
        print("   برای همه: python tests/run_all_tests.py --all")
        print()

    run_unit = args.unit or args.all
    run_integ = args.integration or args.all
    run_e2e = args.e2e or args.all

    # ─── اطلاعات اولیه ───
    print("=" * 70)
    print("🚀 تست‌های پروژه — Payesh Platform")
    print("=" * 70)
    print(f"  ROOT:     {ROOT}")
    print(f"  Python:   {sys.version.split()[0]}")
    print(f"  Layers:   unit={run_unit} | integration={run_integ} | e2e={run_e2e}")
    print(f"  Platform: {args.platform or 'همه'}")
    print(f"  Headless: {args.headless}")
    print("=" * 70)

    # ─── فیلتر پلتفرم ───
    if args.only_enabled and not args.include_disabled:
        enabled = [p for p in PLATFORMS if _is_platform_enabled(p)]
        print(f"\n📋 پلتفرم‌های فعال: {enabled}")
        if not enabled:
            print("⚠️ هیچ پلتفرم فعالی نیست — با --include-disabled امتحان کن")
            sys.exit(0)

    # ─── اجرا ───
    results: dict[str, tuple[bool, float]] = {}
    t_start = time.monotonic()

    # UNIT
    if run_unit:
        ok, elapsed = run_unit_tests(only_platform=args.platform)
        results["unit"] = (ok, elapsed)

    # INTEGRATION
    if run_integ:
        ok, elapsed = run_integration_tests(
            only_platform=args.platform,
            headless=args.headless,
        )
        results["integration"] = (ok, elapsed)

    # E2E
    if run_e2e:
        ok, elapsed = run_e2e_tests()
        results["e2e"] = (ok, elapsed)

    # ─── خلاصه ───
    total_elapsed = time.monotonic() - t_start

    print()
    print("=" * 70)
    print("📊 خلاصهٔ نهایی")
    print("=" * 70)

    for layer, (ok, elapsed) in results.items():
        status = "✅" if ok else "❌"
        print(f"  {status} {layer:12s} | {elapsed:6.1f}s")

    n_ok = sum(1 for ok, _ in results.values() if ok)
    n_fail = len(results) - n_ok

    print()
    print(f"  ✅ موفق:    {n_ok}/{len(results)}")
    print(f"  ❌ ناموفق:  {n_fail}/{len(results)}")
    print(f"  ⏱️  کل زمان: {total_elapsed:.1f}s")
    print("=" * 70)

    # ─── Health Check (اختیاری) ───
    hc_ok = True
    hc_elapsed = 0.0
    if args.healthcheck:
        hc_ok, hc_elapsed = run_healthcheck()
        hc_status = "✅" if hc_ok else "❌"
        print(f"\n  {hc_status} Health Check | {hc_elapsed:.1f}s")

    all_ok = (n_fail == 0) and hc_ok
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()