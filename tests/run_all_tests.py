# tests/run_all_tests.py
"""
اجرای همه تست‌های پلتفرم + StorageHandler.

اجرا:
  python tests/run_all_tests.py
  python tests/run_all_tests.py --only bale eitaa rubika
  python tests/run_all_tests.py --headless
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


PLATFORMS = [
    "bale",       # بله
    "eitaa",      # ایتا
    "rubika",     # روبیکا
    "telegram",   # تلگرام
    "whatsapp",   # واتساپ
    "linkedin",   # لینکدین
    "web",        # وب
]


def run_test(name: str, headless: bool = False) -> tuple[bool, float]:
    """اجرای یک تست در subprocess جدا."""
    script = os.path.join(ROOT, "tests", f"test_{name}_full.py")
    if not os.path.exists(script):
        print(f"  ⚠️ تست {name} وجود ندارد: {script}")
        return (False, 0.0)

    cmd = [sys.executable, script]
    if headless and name in ("bale", "eitaa", "linkedin"):
        cmd.append("--headless")

    t0 = time.monotonic()
    try:
        rc = subprocess.call(cmd, cwd=ROOT)
        return (rc == 0, time.monotonic() - t0)
    except Exception as e:
        print(f"  ❌ خطا در اجرای {name}: {e}")
        return (False, time.monotonic() - t0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None,
                        help="فقط این پلتفرم‌ها")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    tests = args.only if args.only else PLATFORMS

    print("=" * 70)
    print(f"🚀 اجرای {len(tests)} تست پلتفرم (Pipeline end-to-end)")
    print(f"   tests={tests} | headless={args.headless}")
    print("=" * 70)

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

    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()