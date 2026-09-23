"""اجرای همه تست‌های پلتفرم‌ها."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
TESTS = ROOT / "tests"

# ─── پلتفرم‌ها ───
PLATFORMS = [
    ("bale",   TESTS / "test_bale.py"),
    ("eitaa",  TESTS / "test_eitaa.py"),
    ("rubika", TESTS / "test_rubika.py"),
    ("web",    TESTS / "test_web.py"),
]


def run_file(name: str, path: Path, extra_args=None):
    """اجرای یک فایل تست."""
    if not path.exists():
        print(f"⏭️  {name}: فایل نیست — رد شد")
        return None

    cmd = [
        sys.executable, "-m", "pytest",
        str(path),
        "-v", "--tb=short", "-q",
    ]
    if extra_args:
        cmd.extend(extra_args)

    print(f"\n{'='*55}")
    print(f"🔍 {name}")
    print(f"{'='*55}")

    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)

    # چاپ خطوط مهم
    for line in (r.stdout + r.stderr).splitlines():
        if any(x in line for x in (
            "PASSED", "FAILED", "ERROR", "SKIPPED",
            "passed", "failed", "error",
            "=====",
        )):
            print(line)

    return r.returncode == 0


def main():
    print("=" * 55)
    print("اجرای همه تست‌های پلتفرم‌ها")
    print("=" * 55)

    # آرگومان‌ها
    args = sys.argv[1:]
    fast_only = "--fast" in args

    results = {}

    for name, path in PLATFORMS:
        extra = ["-m", "not network"] if fast_only else None
        ok = run_file(name, path, extra)
        if ok is not None:
            results[name] = ok

    # ─── خلاصه ───
    print("\n" + "=" * 55)
    print("خلاصه")
    print("=" * 55)

    passed = [n for n, ok in results.items() if ok]
    failed = [n for n, ok in results.items() if not ok]

    if passed:
        print(f"✅ سالم: {', '.join(passed)}")
    if failed:
        print(f"❌ خراب: {', '.join(failed)}")
    if not results:
        print("⚠️ هیچ فایل تستی پیدا نشد")

    print("=" * 55)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())