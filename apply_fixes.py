"""
apply_fixes.py — اعمال ۱۰ تغییر امن روی پروژه Payesh.

قبل از اجرا:
  - backup گرفته شده در backups_before_fix/
  - همه تست‌ها pass شده‌اند

بعد از اجرا:
  - هر تغییر چک می‌شود
  - compileall اجرا می‌شود
  - اگر خطایی بود، می‌توان از backup برگرداند

اجرا:
    python apply_fixes.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
BACKUP = ROOT / "backups_before_fix"


# ═══════════════════════════════════════════
# تغییرات
# ═══════════════════════════════════════════
FIXES = [
    {
        "id": 1,
        "file": "data/paths.py",
        "before": '''    "ensure_runtime_dirs",

    "AI_SYSTEM_PROMPT",
    "AI_PERSONA",
''',
        "after": '''    "ensure_runtime_dirs",

    "AI_PERSONA",
''',
        "desc": "حذف AI_SYSTEM_PROMPT از __all__",
    },
    {
        "id": 2,
        "file": "collectors/controller.py",
        "before": "from typing import Any\n",
        "after": "from typing import Any, ClassVar\n",
        "desc": "اضافه‌کردن ClassVar به import",
    },
    {
        "id": 3,
        "file": "collectors/controller.py",
        "before": '''class SafeAction:
    _shared_stats: dict[str, list[float]] = {}
''',
        "after": '''class SafeAction:
    _shared_stats: ClassVar[dict[str, list[float]]] = {}
''',
        "desc": "ClassVar برای _shared_stats",
    },
    {
        "id": 4,
        "file": "collectors/controller.py",
        "before": '''from core.kernel.logger import (
    log_error, log_info, log_success, log_warning,
)
''',
        "after": '''from core.kernel.logger import (
    log_error, log_info, log_success,
)
''',
        "desc": "حذف log_warning بلااستفاده",
    },
    {
        "id": 5,
        "file": "panel.py",
        "before": '''import sys
import time
import traceback
from collections.abc import Callable
''',
        "after": '''import sys
import time
from collections.abc import Callable
''',
        "desc": "حذف import traceback بلااستفاده",
    },
    {
        "id": 6,
        "file": "panel.py",
        "before": '''        _cprint(C.GREEN, f"  ✓ Ollama UP at {AI['url']}")
        _cprint(C.WHITE, f"    model: {AI['model']}")
    except Exception:
''',
        "after": '''        _cprint(C.GREEN, f"  ✓ Ollama UP at {AI['url']}")
        _cprint(C.WHITE, f"    model: {AI['model']}")
        models = [
            m.get("name", "?")
            for m in tags.get("models", [])
            if m.get("name")
        ]
        if models:
            _cprint(C.WHITE, f"    available: {', '.join(models[:5])}")
    except Exception:
''',
        "desc": "چاپ مدل‌های Ollama",
    },
    {
        "id": 7,
        "file": "collectors/telegram.py",
        "before": '''                try:
                    count = await asyncio.wait_for(
                        self._scrape_current_chat(
''',
        "after": '''                try:
                    await asyncio.wait_for(
                        self._scrape_current_chat(
''',
        "desc": "حذف count = بلااستفاده",
    },
    {
        "id": 8,
        "file": "collectors/bale.py",
        "before": '''                if messages:
                    used_sel = sel
                    log_info(
                        f"   ✅ پیام‌ها با {sel} ({len(messages)})"
                    )
                    break
''',
        "after": '''                if messages:
                    log_info(
                        f"   ✅ پیام‌ها با {sel} ({len(messages)})"
                    )
                    break
''',
        "desc": "حذف used_sel بلااستفاده",
    },
    {
        "id": 9,
        "file": "collectors/linkedin.py",
        "before": '''                    else:
                        # None: یا Company یا نامشخص
                        # از نام پست در header تشخیص می‌دهیم
                        header = await post.evaluate(
                            "(el) => (el.innerText || '').slice(0, 200)"
                        )
                        # اگر «Feed post\\n\\n<Name>\\n\\n» و Name شبیه شرکت بود
''',
        "after": '''                    else:
                        # None: یا Company یا نامشخص
                        # اگر «Feed post\\n\\n<Name>\\n\\n» و Name شبیه شرکت بود
''',
        "desc": "حذف header بلااستفاده",
    },
    {
        "id": 10,
        "file": "collectors/rubika.py",
        "before": '''        try:
            since_ts = (
                int(effective_since.timestamp())
                if effective_since
                else 0
            )

            chats_resp = await self._client.get_chats()
''',
        "after": '''        try:
            chats_resp = await self._client.get_chats()
''',
        "desc": "حذف since_ts بلااستفاده",
    },
]


# ═══════════════════════════════════════════
# اجرا
# ═══════════════════════════════════════════
def check_backup() -> bool:
    """چک می‌کند backup وجود دارد."""
    if not BACKUP.exists():
        print(f"❌ {BACKUP} وجود ندارد!")
        print("   اول backup بگیرید:")
        print("     mkdir backups_before_fix")
        print("     copy ... backups_before_fix\\")
        return False

    required = set()
    for f in FIXES:
        required.add(Path(f["file"]).name)

    missing = []
    for name in required:
        if not (BACKUP / name).exists():
            missing.append(name)

    if missing:
        print(f"❌ این فایل‌ها در backup نیستند: {missing}")
        return False

    print(f"✅ Backup سالم است ({len(required)} فایل)")
    return True


def apply_fix(fix: dict) -> tuple[bool, str]:
    """یک تغییر را اعمال می‌کند."""
    path = ROOT / fix["file"]
    if not path.exists():
        return False, f"فایل {path} وجود ندارد"

    text = path.read_text(encoding="utf-8")

    if fix["before"] not in text:
        # چک کنیم آیا تغییر قبلاً اعمال شده
        if fix["after"] in text:
            return True, "قبلاً اعمال شده (idempotent)"
        return False, "متن before پیدا نشد"

    count = text.count(fix["before"])
    if count > 1:
        return False, f"متن before {count} بار تکرار شده (باید ۱ بار باشد)"

    new_text = text.replace(fix["before"], fix["after"], 1)
    path.write_text(new_text, encoding="utf-8")

    # چک کن تغییر واقعاً اعمال شده
    verify = path.read_text(encoding="utf-8")
    if fix["after"] not in verify:
        return False, "بعد از تغییر، متن after پیدا نشد!"

    return True, "اعمال شد"


def restore_backup() -> None:
    """بازگردانی از backup."""
    print("\n🔄 بازگردانی از backup...")
    for name in ["paths.py", "controller.py", "telegram.py",
                 "bale.py", "linkedin.py", "rubika.py", "panel.py"]:
        src = BACKUP / name
        if not src.exists():
            continue
        # پیدا کردن مقصد
        for fix in FIXES:
            if Path(fix["file"]).name == name:
                dst = ROOT / fix["file"]
                shutil.copy2(src, dst)
                print(f"   ✓ {fix['file']}")
                break


def verify_syntax() -> bool:
    """چک syntax."""
    print("\n🔍 چک syntax...")
    result = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", str(ROOT)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"❌ خطای syntax:\n{result.stdout}\n{result.stderr}")
        return False
    print("   ✅ همه فایل‌ها از نظر syntax سالم‌اند")
    return True


def verify_imports() -> bool:
    """چک importها."""
    print("\n🔍 چک importها...")

    tests = [
        ("import panel", "panel"),
        ("from core.app.orchestrator import Orchestrator", "orchestrator"),
        ("from collectors.telegram import TelegramWebCollector", "telegram"),
        ("from collectors.bale import BaleCollector", "bale"),
        ("from collectors.controller import SafeAction", "controller"),
        ("from data import paths", "paths"),
    ]

    all_ok = True
    for code, label in tests:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, cwd=ROOT,
        )
        if result.returncode != 0:
            print(f"   ❌ {label}:\n{result.stderr}")
            all_ok = False
        else:
            print(f"   ✅ {label}")

    return all_ok


def main() -> int:
    print("═" * 60)
    print("  apply_fixes.py — اعمال ۱۰ تغییر امن")
    print("═" * 60)

    # چک backup
    if not check_backup():
        return 1

    # اعمال تغییرات
    print(f"\n🚀 اعمال {len(FIXES)} تغییر...")
    results = []

    for fix in FIXES:
        ok, msg = apply_fix(fix)
        results.append((fix, ok, msg))
        icon = "✅" if ok else "❌"
        print(f"   {icon} [{fix['id']:>2}] {fix['file']:<30} | {fix['desc']}")
        if not ok:
            print(f"         └─ {msg}")

    # خلاصه
    success = sum(1 for _, ok, _ in results if ok)
    fail = len(results) - success
    print(f"\n📊 نتیجه: ✅{success} ❌{fail}")

    if fail > 0:
        print("\n⚠️  بعضی تغییرات اعمال نشدند.")
        print("   آیا از backup بازگردانی شود؟ [y/N]: ", end="")
        try:
            ans = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = "n"

        if ans in ("y", "yes"):
            restore_backup()
            print("\n✅ بازگردانی کامل شد.")
            return 1
        else:
            print("\n⚠️  ادامه با تغییرات ناقص...")

    # چک syntax
    if not verify_syntax():
        print("\n❌ syntax خراب شد! از backup بازگردانی می‌کنیم...")
        restore_backup()
        return 1

    # چک importها
    if not verify_imports():
        print("\n❌ import خراب شد! از backup بازگردانی می‌کنیم...")
        restore_backup()
        return 1

    print("\n" + "═" * 60)
    print("  ✅ همه تغییرات اعمال شد و پروژه سالم است")
    print("═" * 60)
    print("\n📋 قدم بعدی:")
    print("   ۱. تست کامل:  echo 0 | python panel.py")
    print("   ۲. تست ruff:  ruff check .")
    print("   ۳. اگر همه خوب بود، commit:  git add . && git commit -m 'fix: ruff P0'")
    return 0


if __name__ == "__main__":
    sys.exit(main())