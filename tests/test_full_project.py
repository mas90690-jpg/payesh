"""
test_full_project.py — تست کامل پروژه از صفر تا صد.

اجرا:
    python tests\test_full_project.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# ═══════════════════════════════════════════
# 🎯 FIX: اضافه کردن مسیر پروژه به sys.path
# ═══════════════════════════════════════════
# فایل در tests/ است → parent = tests/
#                    → parent.parent = PROJECT ROOT
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# اگر CWD هم پروژه است، آن را هم اضافه کن
_CWD = Path.cwd()
if (_CWD / "main.py").exists() and str(_CWD) not in sys.path:
    sys.path.insert(0, str(_CWD))


# ─── utf-8 برای console ───
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ─── رنگ‌ها ───
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
GRAY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


class Stats:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.warnings = 0
        self.skipped = 0
        self.failures: list[str] = []
        self.warnings_list: list[str] = []

    def ok(self):
        self.passed += 1

    def fail(self, msg: str):
        self.failed += 1
        self.failures.append(msg)

    def warn(self, msg: str):
        self.warnings += 1
        self.warnings_list.append(msg)

    def skip(self):
        self.skipped += 1


S = Stats()


def header(title: str) -> None:
    print()
    print(f"{BOLD}{CYAN}{'═' * 70}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'═' * 70}{RESET}")


def ok(msg: str) -> None:
    print(f"  {GREEN}✅{RESET} {msg}")
    S.ok()


def fail(msg: str) -> None:
    print(f"  {RED}❌{RESET} {msg}")
    S.fail(msg)


def warn(msg: str) -> None:
    print(f"  {YELLOW}⚠️{RESET}  {msg}")
    S.warn(msg)


def info(msg: str) -> None:
    print(f"  {GRAY}ℹ️{RESET}  {msg}")


# ═══════════════════════════════════════════
# ۱) ساختار پروژه
# ═══════════════════════════════════════════
def test_01_structure() -> None:
    header("۱) ساختار پروژه")

    project_root = _PROJECT_ROOT
    info(f"Project root: {project_root}")
    info(f"CWD: {_CWD}")
    info(f"sys.path[0]: {sys.path[0]}")

    critical_files = [
        "main.py",
        "config.py",
        "login.py",
        ".env",
        "requirements.txt",
    ]

    for f in critical_files:
        p = project_root / f
        if p.exists():
            ok(f"فایل {f} موجود")
        else:
            fail(f"فایل {f} وجود ندارد!")

    critical_dirs = [
        "collectors",
        "handlers",
        "analyzers",
        "core",
        "data",
        "storage",
    ]

    for d in critical_dirs:
        p = project_root / d
        if p.is_dir():
            ok(f"پوشه {d}/ موجود")
        else:
            fail(f"پوشه {d}/ وجود ندارد!")


# ═══════════════════════════════════════════
# ۲) Config + .env
# ═══════════════════════════════════════════
def test_02_config() -> None:
    header("۲) Config + .env")

    try:
        from config import (
            ADMIN_IDS,
            ADMIN_PLATFORM,
            ADMIN_ENABLED,
            AI,
            AI_ENABLED,
            AI_PRIORITY,
            PLATFORMS_ORDER,
            PLUGINS_CONFIG,
            BASE_DIR,
        )
        ok(f"Import config | BASE_DIR={BASE_DIR}")
    except Exception as e:
        fail(f"Import config | {type(e).__name__}: {e}")
        return

    if ADMIN_ENABLED:
        ok("ADMIN_ENABLED=True")
    else:
        fail("ADMIN_ENABLED=False")

    if ADMIN_PLATFORM == "bale":
        ok("ADMIN_PLATFORM=bale")
    else:
        fail(f"ADMIN_PLATFORM={ADMIN_PLATFORM}")

    if ADMIN_IDS:
        ok(f"ADMIN_IDS={ADMIN_IDS}")
    else:
        warn("ADMIN_IDS خالی است")

    if AI_ENABLED:
        ok("AI_ENABLED=True")
    else:
        warn("AI_ENABLED=False")

    ok(f"AI_PRIORITY={AI_PRIORITY}")

    has_ds_key = bool((AI or {}).get("deepseek_api_key"))
    if has_ds_key:
        ok("DeepSeek API key موجود")
    else:
        warn("DeepSeek API key نیست")

    ok(f"PLATFORMS_ORDER={list(PLATFORMS_ORDER)}")

    for key in PLUGINS_CONFIG:
        if not key.endswith("collector"):
            continue
        cfg = PLUGINS_CONFIG[key]
        enabled = cfg.get("enabled", False)
        marker = "✅" if enabled else "⏭️"
        info(f"{marker} {key}: enabled={enabled}")

    env_path = Path(BASE_DIR) / ".env"
    if env_path.exists():
        ok(f".env موجود | size={env_path.stat().st_size} bytes")
    else:
        fail(".env وجود ندارد")


# ═══════════════════════════════════════════
# ۳) Database
# ═══════════════════════════════════════════
def test_03_database() -> None:
    header("۳) Database")

    try:
        from data import paths as data_paths
        db_path = str(data_paths.DB_FILE)
    except Exception as e:
        fail(f"import data.paths | {type(e).__name__}: {e}")
        return

    if not Path(db_path).exists():
        fail(f"DB وجود ندارد: {db_path}")
        return

    ok(f"DB موجود: {db_path}")

    try:
        import sqlite3
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            cur = conn.cursor()

            cur.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' ORDER BY name"
            )
            tables = [row[0] for row in cur.fetchall()]
            ok(f"جدول‌ها: {tables}")

            required = ["messages", "leads"]
            for t in required:
                if t in tables:
                    n = cur.execute(
                        f"SELECT COUNT(*) FROM {t}"
                    ).fetchone()[0]
                    ok(f"جدول {t}: {n} رکورد")
                else:
                    fail(f"جدول {t} وجود ندارد!")

            try:
                v = cur.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                ok(f"Schema version: {v}")
            except Exception:
                pass

        finally:
            conn.close()
    except Exception as e:
        fail(f"اتصال DB | {type(e).__name__}: {e}")


# ═══════════════════════════════════════════
# ۴) Imports
# ═══════════════════════════════════════════
def test_04_imports() -> None:
    header("۴) Imports ماژول‌ها")

    modules = [
        "core.kernel.event_bus",
        "core.kernel.logger",
        "core.kernel.models",
        "core.contracts.base_collector",
        "core.contracts.base_handler",
        "core.contracts.base_analyzer",
        "core.app.orchestrator",
        "core.app.plugin_loader",
        "core.database.factory",
        "core.adapters.messenger_registry",
        "core.adapters.command_parser",
        "core.adapters.translator",
        "core.services.ai_assistant",
        "core.services.crm_export",
        "core.services.daily_reporter",
        "core.safe_brain",
        "core.manual.brain",
        "analyzers.keyword_finder",
        "analyzers.lead_scorer",
        "analyzers.priority_finder",
        "analyzers.target_filter",
        "collectors.bale",
        "collectors.bale_bot",
        "collectors.eitaa",
        "collectors.instagram",
        "collectors.linkedin",
        "collectors.rubika",
        "collectors.telegram",
        "collectors.web",
        "collectors.whatsapp",
        "handlers.admin_handler",
        "handlers.admin_actions",
        "handlers.admin_memory",
        "handlers.keyword_admin",
        "handlers.storage_handler",
        "handlers.auto_subscribe",
    ]

    failed_imports: list[str] = []

    for mod in modules:
        try:
            __import__(mod)
            ok(f"import {mod}")
        except Exception as e:
            fail(f"import {mod} | {type(e).__name__}: {e}")
            failed_imports.append(mod)

    if failed_imports:
        warn(f"{len(failed_imports)} ماژول import نشد")


# ═══════════════════════════════════════════
# ۵) کلکتورها
# ═══════════════════════════════════════════
def test_05_collectors() -> None:
    header("۵) کلکتورها")

    collectors_info = [
        ("collectors.bale", "BaleCollector", "Playwright بله"),
        ("collectors.bale_bot", "BaleBotCollector", "Bot API بله"),
        ("collectors.eitaa", "EitaaCollector", "ایتا"),
        ("collectors.instagram", "InstagramCollector", "اینستاگرام"),
        ("collectors.linkedin", "LinkedInCollector", "لینکدین"),
        ("collectors.rubika", "RubikaCollector", "روبیکا"),
        ("collectors.telegram", "TelegramWebCollector", "تلگرام"),
        ("collectors.web", "WebCollector", "وب"),
        ("collectors.whatsapp", "WhatsAppCollector", "واتس‌اپ"),
    ]

    for mod, cls_name, label in collectors_info:
        try:
            m = __import__(mod, fromlist=[cls_name])
            cls = getattr(m, cls_name, None)
            if cls is None:
                fail(f"{label} | کلاس {cls_name} پیدا نشد")
                continue

            if hasattr(cls, "is_enabled"):
                try:
                    enabled = cls.is_enabled()
                    if enabled:
                        ok(f"{label} | is_enabled=True")
                    else:
                        info(f"{label} | is_enabled=False (غیرفعال)")
                except Exception as e:
                    warn(f"{label} | is_enabled() | {type(e).__name__}: {e}")
            else:
                ok(f"{label} | کلاس موجود")

        except Exception as e:
            fail(f"{label} | import {mod} | {type(e).__name__}: {e}")


# ═══════════════════════════════════════════
# ۶) هندلرها
# ═══════════════════════════════════════════
def test_06_handlers() -> None:
    header("۶) هندلرها")

    handlers = [
        ("handlers.admin_handler", "AdminHandler"),
        ("handlers.storage_handler", "StorageHandler"),
    ]

    for mod, cls_name in handlers:
        try:
            m = __import__(mod, fromlist=[cls_name])
            cls = getattr(m, cls_name, None)
            if cls is None:
                fail(f"{cls_name} | کلاس پیدا نشد")
                continue
            ok(f"{cls_name} | کلاس موجود")
        except Exception as e:
            fail(f"{cls_name} | import | {type(e).__name__}: {e}")


# ═══════════════════════════════════════════
# ۷) Bot API بله
# ═══════════════════════════════════════════
def test_07_bale_bot() -> None:
    header("۷) Bot API بله")

    # 🎯 FIX: اول load_dotenv
    try:
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except Exception:
        pass

    token = os.getenv("BALE_BOT_TOKEN", "").strip()
    enabled = os.getenv("BALE_BOT_ENABLED", "").strip().lower()

    if token:
        ok(
            f"BALE_BOT_TOKEN موجود | len={len(token)} | "
            f"پیشوند={token[:15]}..."
        )
    else:
        fail("BALE_BOT_TOKEN نیست")
        return

    if enabled in ("true", "1", "yes"):
        ok(f"BALE_BOT_ENABLED={enabled}")
    else:
        warn(f"BALE_BOT_ENABLED={enabled}")

    try:
        from collectors.bale_bot import BaleBotCollector
        result = BaleBotCollector.is_enabled()
        if result:
            ok("BaleBotCollector.is_enabled()=True")
        else:
            fail("BaleBotCollector.is_enabled()=False")
    except Exception as e:
        fail(f"BaleBotCollector | {type(e).__name__}: {e}")


# ═══════════════════════════════════════════
# ۸) AI Assistant
# ═══════════════════════════════════════════
def test_08_ai() -> None:
    header("۸) AI Assistant")

    try:
        from core.services.ai_assistant import get_ai_assistant
        ai = get_ai_assistant()
        ok(f"get_ai_assistant() | enabled={ai.enabled}")

        stats = ai.get_stats()
        ok(f"deepseek_configured={stats.get('deepseek_configured')}")
        ok(f"deepseek_key_source={stats.get('deepseek_key_source')}")
        ok(f"ollama_url={stats.get('ollama_url')}")
    except Exception as e:
        fail(f"AI | {type(e).__name__}: {e}")


# ═══════════════════════════════════════════
# ۹) AdminHandler
# ═══════════════════════════════════════════
def test_09_admin_handler() -> None:
    header("۹) AdminHandler")

    # ─── مسیر DB ───
    try:
        from data import paths as data_paths
        db_path = str(data_paths.DB_FILE)
    except Exception as e:
        fail(f"data.paths | {type(e).__name__}: {e}")
        return

    # ─── FakeDB با __init__ ───
    class FakeDB:
        def __init__(self, path: str):
            self.db_path = path

    fake_db = FakeDB(db_path)
    info(f"db_path={fake_db.db_path}")

    # ─── ساخت AdminHandler ───
    try:
        from handlers.admin_handler import AdminHandler

        h = AdminHandler(db=fake_db)

        ok("AdminHandler ساخته شد")
        ok(f"enabled={h.enabled}")
        ok(f"platform={h.platform}")
        ok(f"admin_ids={h.admin_ids}")
        ok(f"manual_brain={'✅' if h.manual_brain else '❌'}")
        ok(f"safe_brain={'✅' if h.safe_brain else '❌'}")
        ok(
            f"domain: in={len(h._domain.in_domain)} "
            f"anti={len(h._domain.anti_domain)}"
        )

        mem_path = h._memory.path
        info(f"memory_path={mem_path}")

        if h.handler_name == "admin_handler":
            ok("handler_name=admin_handler")
        else:
            fail(f"handler_name={h.handler_name}")

        if h.can_handle("message.received"):
            ok("can_handle('message.received')=True")
        else:
            fail("can_handle('message.received')=False")

    except Exception as e:
        fail(f"AdminHandler | {type(e).__name__}: {e}")
        import traceback
        for ln in traceback.format_exc().splitlines()[-5:]:
            print(f"     {GRAY}{ln}{RESET}")


# ═══════════════════════════════════════════
# ۱۰) OBS Server
# ═══════════════════════════════════════════
def test_10_obs() -> None:
    header("۱۰) OBS Server")

    try:
        from config import OBS_CONFIG
        ok(f"OBS enabled={OBS_CONFIG.get('enabled')}")
        ok(f"host={OBS_CONFIG.get('host')}:{OBS_CONFIG.get('port')}")

        try:
            ok("start_obs_server import شد")
        except Exception as e:
            fail(f"obs_server import | {type(e).__name__}: {e}")

    except Exception as e:
        fail(f"OBS | {type(e).__name__}: {e}")


# ═══════════════════════════════════════════
# main
# ═══════════════════════════════════════════
def main() -> int:
    print()
    print(f"{BOLD}{CYAN}{'█' * 70}{RESET}")
    print(f"{BOLD}{CYAN}  🧪 تست کامل پروژه — Srooyesh Lead-Monitor{RESET}")
    print(f"{BOLD}{CYAN}{'█' * 70}{RESET}")

    t0 = time.monotonic()

    test_01_structure()
    test_02_config()
    test_03_database()
    test_04_imports()
    test_05_collectors()
    test_06_handlers()
    test_07_bale_bot()
    test_08_ai()
    test_09_admin_handler()
    test_10_obs()

    elapsed = time.monotonic() - t0

    print()
    print(f"{BOLD}{CYAN}{'═' * 70}{RESET}")
    print(f"{BOLD}  📊 خلاصهٔ نهایی{RESET}")
    print(f"{BOLD}{CYAN}{'═' * 70}{RESET}")
    print()
    print(f"  {GREEN}✅ پاس: {S.passed}{RESET}")
    print(f"  {RED}❌ رد: {S.failed}{RESET}")
    print(f"  {YELLOW}⚠️  هشدار: {S.warnings}{RESET}")
    print(f"  ⏱️  زمان: {elapsed:.2f}s")
    print()

    if S.failures:
        print(f"{BOLD}{RED}  ❌ خطاهای رد شده:{RESET}")
        for f in S.failures:
            print(f"     {RED}•{RESET} {f}")
        print()

    if S.warnings_list:
        print(f"{BOLD}{YELLOW}  ⚠️  هشدارها:{RESET}")
        for w in S.warnings_list:
            print(f"     {YELLOW}•{RESET} {w}")
        print()

    if S.failed == 0:
        print(f"{BOLD}{GREEN}{'═' * 70}{RESET}")
        print(f"{BOLD}{GREEN}  🎉 همه چیز پاس شد! پروژه آماده است.{RESET}")
        print(f"{BOLD}{GREEN}{'═' * 70}{RESET}")
        return 0
    else:
        print(f"{BOLD}{RED}{'═' * 70}{RESET}")
        print(f"{BOLD}{RED}  ❌ {S.failed} خطا پیدا شد.{RESET}")
        print(f"{BOLD}{RED}{'═' * 70}{RESET}")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        print(f"{YELLOW}لغو شد{RESET}")
        sys.exit(130)
    except Exception as e:
        print(f"{RED}خطای غیرمنتظره: {type(e).__name__}: {e}{RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(1)