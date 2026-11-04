"""تست دقیق پروژه — syntax + import + flake8 + BOM + runtime."""
import os
import sys
import subprocess


# ═══════════════════════════════════════════
# لیست کامل فایل‌های پایتون
# ═══════════════════════════════════════════
FILES = [
    # core/brain
    "core/brain/main.py",
    "core/brain/constants.py",
    "core/brain/utils.py",
    "core/brain/learning.py",
    "core/brain/memory.py",
    "core/brain/mixin_agent.py",
    "core/brain/mixin_files.py",
    "core/brain/mixin_info.py",
    "core/brain/mixin_platforms.py",
    "core/brain/mixin_reports.py",
    "core/brain/mixin_system.py",
    "core/brain/tools_audit.py",
    "core/brain/tools_meta.py",
    "core/brain/__init__.py",
    # core/kernel
    "core/kernel/logger.py",
    "core/kernel/event_bus.py",
    "core/kernel/models.py",
    "core/kernel/console_compat.py",
    # core/app
    "core/app/orchestrator.py",
    "core/app/plugin_loader.py",
    # core/database
    "core/database/base.py",
    "core/database/sqlite.py",
    "core/database/postgres.py",
    "core/database/factory.py",
    # core/adapters
    "core/adapters/command_parser.py",
    "core/adapters/messenger_registry.py",
    "core/adapters/translator.py",
    "core/adapters/formula_manager.py",
    "core/adapters/access_manager.py",
    "core/adapters/web_media.py",
    "core/adapters/browser_lock.py",
    # core/manual
    "core/manual/brain.py",
    "core/manual/_brain_core.py",
    "core/manual/_brain_rules.py",
    "core/manual/_brain_keywords.py",
    "core/manual/_brain_learning.py",
    "core/manual/_brain_settings.py",
    "core/manual/data_api.py",
    "core/manual/rules.py",
    # core/safe_brain
    "core/safe_brain/main.py",
    "core/safe_brain/constants.py",
    "core/safe_brain/storage.py",
    # core/services
    "core/services/ai_assistant.py",
    "core/services/daily_reporter.py",
    "core/services/crm_export.py",
    "core/services/backup_service.py",
    "core/services/cleanup_service.py",
    # core/ops
    "core/ops/health_check.py",
    "core/ops/monitor.py",
    "core/ops/obs_server.py",
    "core/ops/self_monitor.py",
    # core/contracts
    "core/contracts/base_collector.py",
    "core/contracts/base_handler.py",
    "core/contracts/base_analyzer.py",
    # handlers
    "handlers/__init__.py",
    "handlers/_ai_utils.py",
    "handlers/admin_actions.py",
    "handlers/admin_handler.py",
    "handlers/admin_memory.py",
    "handlers/auto_subscribe.py",
    "handlers/keyword_admin.py",
    "handlers/storage_handler.py",
    # analyzers
    "analyzers/keyword_finder.py",
    "analyzers/lead_scorer.py",
    "analyzers/priority_finder.py",
    "analyzers/target_filter.py",
    # collectors
    "collectors/bale.py",
    "collectors/eitaa.py",
    "collectors/rubika.py",
    "collectors/telegram.py",
    "collectors/whatsapp.py",
    "collectors/linkedin.py",
    "collectors/instagram.py",
    "collectors/web.py",
    "collectors/facebook.py",
    "collectors/bale_bot.py",
    "collectors/controller.py",
    "collectors/youtube.py",
    # root
    "main.py",
]


# ═══════════════════════════════════════════
# تست ۱ — syntax (py_compile)
# ═══════════════════════════════════════════
def test_syntax():
    print("\n" + "=" * 60)
    print("TEST 1: Syntax (py_compile)")
    print("=" * 60)
    errors = []
    for f in FILES:
        if not os.path.exists(f):
            continue
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", f],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            errors.append((f, r.stderr[:200]))
    if errors:
        print(f"  FAIL: {len(errors)} file(s) with syntax error")
        for f, err in errors:
            print(f"    {f}: {err}")
    else:
        print(f"  OK: {len(FILES)} files compiled")
    return len(errors) == 0


# ═══════════════════════════════════════════
# تست ۲ — BOM
# ═══════════════════════════════════════════
def test_bom():
    print("\n" + "=" * 60)
    print("TEST 2: BOM (byte order mark)")
    print("=" * 60)
    errors = []
    for f in FILES:
        if not os.path.exists(f):
            continue
        with open(f, "rb") as fh:
            raw = fh.read(3)
        if raw == b"\xef\xbb\xbf":
            errors.append(f)
    if errors:
        print(f"  FAIL: {len(errors)} file(s) with BOM")
        for f in errors:
            print(f"    {f}")
    else:
        print(f"  OK: {len(FILES)} files without BOM")
    return len(errors) == 0


# ═══════════════════════════════════════════
# تست ۳ — flake8
# ═══════════════════════════════════════════
def test_flake8():
    print("\n" + "=" * 60)
    print("TEST 3: flake8")
    print("=" * 60)
    r = subprocess.run(
        [sys.executable, "-m", "flake8",
         "core/", "handlers/", "analyzers/",
         "collectors/", "main.py",
         "--max-line-length=120", "--select=E,F,W"],
        capture_output=True, text=True,
    )
    if r.stdout.strip():
        lines = r.stdout.strip().split("\n")
        print(f"  FAIL: {len(lines)} error(s)")
        for ln in lines[:20]:
            print(f"    {ln}")
        if len(lines) > 20:
            print(f"    ... +{len(lines) - 20} more")
    else:
        print("  OK: 0 errors")
    return not r.stdout.strip()


# ═══════════════════════════════════════════
# تست ۴ — pyflakes
# ═══════════════════════════════════════════
def test_pyflakes():
    print("\n" + "=" * 60)
    print("TEST 4: pyflakes")
    print("=" * 60)
    r = subprocess.run(
        [sys.executable, "-m", "pyflakes",
         "core/", "handlers/", "analyzers/",
         "collectors/", "main.py"],
        capture_output=True, text=True,
    )
    if r.stdout.strip() or r.stderr.strip():
        lines = (r.stdout + r.stderr).strip().split("\n")
        print(f"  FAIL: {len(lines)} error(s)")
        for ln in lines[:20]:
            print(f"    {ln}")
    else:
        print("  OK: 0 errors")
    return not (r.stdout.strip() or r.stderr.strip())


# ═══════════════════════════════════════════
# تست ۵ — import
# ═══════════════════════════════════════════
def test_import():
    print("\n" + "=" * 60)
    print("TEST 5: Import (main modules)")
    print("=" * 60)
    modules = [
        "core.brain.main",
        "core.brain.memory",
        "core.brain.learning",
        "core.kernel.logger",
        "core.kernel.event_bus",
        "core.database.sqlite",
        "core.adapters.access_manager",
        "core.manual.data_api",
        "core.safe_brain.main",
        "handlers.storage_handler",
        "handlers.admin_handler",
        "analyzers.keyword_finder",
        "analyzers.lead_scorer",
        "collectors.bale",
        "collectors.eitaa",
        "collectors.facebook",
    ]
    errors = []
    for mod in modules:
        try:
            __import__(mod)
            print(f"  OK: {mod}")
        except Exception as e:
            errors.append((mod, f"{type(e).__name__}: {e}"))
            print(f"  FAIL: {mod} | {type(e).__name__}: {e}")
    return len(errors) == 0


# ═══════════════════════════════════════════
# تست ۶ — runtime (Brain)
# ═══════════════════════════════════════════
def test_runtime():
    print("\n" + "=" * 60)
    print("TEST 6: Runtime (Brain)")
    print("=" * 60)
    try:
        from core.brain.main import get_brain
        brain = get_brain(read_only=True)
        print(f"  OK: Brain connected | model={brain.model}")
        print(f"  OK: tools={len(brain.TOOL_MAP)}")
        print(f"  OK: client={'ready' if brain.client else 'offline'}")
        return True
    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════
# Main
# ═══════════════════════════════════════════
def main():
    print("=" * 60)
    print("DEEP TEST — payesh plat")
    print("=" * 60)
    print(f"Python: {sys.version.split()[0]}")
    print(f"CWD: {os.getcwd()}")
    print(f"Files: {len(FILES)}")

    results = {
        "syntax": test_syntax(),
        "bom": test_bom(),
        "flake8": test_flake8(),
        "pyflakes": test_pyflakes(),
        "import": test_import(),
        "runtime": test_runtime(),
    }

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_ok = True
    for name, ok in results.items():
        status = "OK" if ok else "FAIL"
        print(f"  {name:12s}: {status}")
        if not ok:
            all_ok = False

    print("\n" + "=" * 60)
    if all_ok:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
    print("=" * 60)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
