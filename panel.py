"""
Srooyesh Lead-Monitoring Bot — Control Panel
Version: 22.0 (Clean, Precise, Python 3.14+ Ready)
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

# ─── Console & Logging Setup ─────────────────────────────────────────────
try:
    from core.kernel.console_compat import patch_console
    patch_console()
except ImportError:
    pass

from core.kernel.logger import log_debug, log_error, log_info, log_warning

# ─── Paths & Constants ───────────────────────────────────────────────────
from data import paths as data_paths

ROOT = str(data_paths.PROJECT_ROOT)
PYEXE = sys.executable or "python"
PID_FILE = str(data_paths.BOT_PID)
DB_PATH = str(data_paths.DB_FILE)
BACKUP_DIR = str(data_paths.BACKUPS_DIR)
REPORTS_DIR = str(data_paths.REPORTS_DIR)
LOG_FILE = str(data_paths.LOG_FILE)
REQUESTS_FILE = str(data_paths.REQUESTS_FILE)
CONSOLE_LOGS_DIR = str(
    getattr(
        data_paths, "CONSOLE_LOGS",
        os.path.join(ROOT, "storage", "test_reports", "console_logs"),
    )
)

# Timeouts & Limits
_SQLITE_TIMEOUT_S = 5.0
_CLEANUP_TIMEOUT_S = 120
_START_VERIFY_WAIT_S = 1.0
_MENU_DB_TIMEOUT_S = 0.5
_GIT_PREVIEW_LIMIT = 15
_GIT_HARD_LIMIT = 20

# Git Remotes
_PUSH_REMOTES = ["origin", "payesh"]

# UI Layout
_HEADER_INNER_WIDTH = 66
_MENU_COLS = 4
_MENU_CELL_W = 17

# Regex
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")
_SAFE_FILENAME_RE_1 = re.compile(r"[^\w\s\-]+")
_SAFE_FILENAME_RE_2 = re.compile(r"[\s\-]+")

try:
    import psutil
except ImportError:
    psutil = None


# ══════════════════════════════════════════════════════════════════════════
#  🎨 Colors & UI Helpers
# ══════════════════════════════════════════════════════════════════════════
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GRAY = "\033[90m"


def _visible_len(s: str) -> int:
    return len(_ANSI_RE.sub("", s))


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _visible_len(s))


def _print(msg: str = "", color: str = "") -> None:
    with contextlib.suppress(Exception):
        log_info(f"{color}{msg}{C.RESET}" if color else msg)


def _cprint(color: str, msg: str) -> None:
    _print(msg, color)


def _clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _ask_input(prompt: str) -> str | None:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return None


def _confirm(prompt: str) -> bool:
    ans = _ask_input(prompt)
    return ans is not None and ans.strip().lower() in ("y", "yes")


def _safe_filename(s: str) -> str:
    s = s.lower().strip()
    s = _SAFE_FILENAME_RE_1.sub("", s)
    s = _SAFE_FILENAME_RE_2.sub("_", s)
    return s.strip("_")[:60] or "test"


def _open_path(path: str) -> bool:
    try:
        if os.name == "nt":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


class _Cancelled(Exception):
    pass


# ══════════════════════════════════════════════════════════════════════════
#  🎯 Platform Data
# ══════════════════════════════════════════════════════════════════════════
_PLATFORM_LABELS = {
    "eitaa": "Eitaa", "bale": "Bale", "rubika": "Rubika", "telegram": "Telegram",
    "whatsapp": "WhatsApp", "web": "Web", "linkedin": "LinkedIn", "instagram": "Instagram",
    "youtube": "YouTube", "facebook": "Facebook",
}

_PLATFORM_SHORT = {
    "eitaa": "Eit", "bale": "Bal", "rubika": "Rub", "telegram": "Tel",
    "whatsapp": "WA", "web": "Web", "linkedin": "LI", "instagram": "IG",
    "youtube": "YT", "facebook": "FB",
}

try:
    from config import PLATFORMS_ORDER, PLUGINS_CONFIG
    _order = list(PLATFORMS_ORDER)
    for _extra in ("youtube", "facebook"):
        if _extra not in _order:
            _order.append(_extra)
    _PLATFORM_CHOICES = [(p, _PLATFORM_LABELS.get(p, p)) for p in _order]
except ImportError:
    PLUGINS_CONFIG = {}
    _PLATFORM_CHOICES = list(_PLATFORM_LABELS.items())


def _get_platforms_state() -> dict[str, bool]:
    state = {}
    env_based = {"youtube", "facebook"}
    for name, _ in _PLATFORM_CHOICES:
        if name in env_based:
            raw = os.getenv(f"{name.upper()}_ENABLED", "").strip().lower()
            if raw:
                state[name] = raw in ("true", "1", "yes", "on")
            else:
                state[name] = bool(PLUGINS_CONFIG.get(f"{name}collector", {}).get("enabled", False))
        else:
            state[name] = bool(PLUGINS_CONFIG.get(f"{name}collector", {}).get("enabled", False))
    return state


# ══════════════════════════════════════════════════════════════════════════
#  🗄️ Database & System Helpers
# ══════════════════════════════════════════════════════════════════════════
def _sqlite_connect(path: str, timeout: float = _SQLITE_TIMEOUT_S) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=timeout)
    with contextlib.suppress(Exception):
        conn.execute("PRAGMA busy_timeout = ?", (int(timeout * 1000),))
    return conn


def _menu_stats() -> dict[str, int | str]:
    if not os.path.exists(DB_PATH):
        return {"leads": 0, "hot": 0, "total": 0, "status": "no_db"}
    try:
        conn = _sqlite_connect(DB_PATH, _MENU_DB_TIMEOUT_S)
        with conn:
            cur = conn.cursor()
            total = cur.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            leads = cur.execute("SELECT COUNT(*) FROM messages WHERE keywords != ''").fetchone()[0]
            hot = cur.execute(
                "SELECT COUNT(*) FROM messages WHERE keywords != '' AND opportunity_type = 'داغ'"
            ).fetchone()[0]
        conn.close()
        return {"leads": int(leads), "hot": int(hot), "total": int(total), "status": "ok"}
    except Exception as e:
        return {"leads": 0, "hot": 0, "total": 0, "status": f"err:{type(e).__name__}"}


def _read_pid() -> int | None:
    try:
        with open(PID_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return None


def _write_pid(pid: int) -> None:
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def _clear_pid() -> None:
    with contextlib.suppress(Exception):
        os.remove(PID_FILE)


def _is_running() -> bool:
    pid = _read_pid()
    if not pid:
        return False
    if psutil is None:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False
    try:
        p = psutil.Process(pid)
        if not p.is_running() or "python" not in (p.name() or "").lower():
            return False
        cmdline = p.cmdline() or []
        return any("main.py" in arg or "python" in arg for arg in cmdline) if cmdline else True
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
        return False


def _bot_uptime_str() -> str:
    pid = _read_pid()
    if not pid or not psutil:
        return "—"
    try:
        delta = time.time() - psutil.Process(pid).create_time()
        if delta < 60:
            return f"{int(delta)}s"
        if delta < 3600:
            return f"{int(delta / 60)}m"
        if delta < 86400:
            return f"{int(delta / 3600)}h{int((delta % 3600) / 60)}m"
        return f"{int(delta / 86400)}d{int((delta % 86400) / 3600)}h"
    except Exception:
        return "—"


# ══════════════════════════════════════════════════════════════════════════
#  🎮 Actions — Bot Control
# ══════════════════════════════════════════════════════════════════════════
def start() -> None:
    if _is_running():
        _cprint(C.YELLOW, f"  ! Bot is already running (PID {_read_pid()})")
        return
    main_py = os.path.join(ROOT, "main.py")
    if not os.path.exists(main_py):
        _cprint(C.RED, f"  ✗ main.py not found: {main_py}")
        return

    kwargs: dict[str, Any] = {"cwd": ROOT}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen([PYEXE, main_py], **kwargs)
        _write_pid(proc.pid)
        _cprint(C.GREEN, f"  ✓ Bot started (PID {proc.pid}) — verifying...")
        time.sleep(_START_VERIFY_WAIT_S)
        if proc.poll() is not None:
            _cprint(C.RED, f"  ✗ Bot exited immediately (rc={proc.returncode})")
            _clear_pid()
            return
        _cprint(C.GREEN, "  ✓ Verified — bot is running.")
    except Exception as e:
        _cprint(C.RED, f"  ✗ Failed to start: {e}")


def stop() -> None:
    pid = _read_pid()
    if not _is_running():
        _cprint(C.GRAY, "  i) Bot is not running.")
        _clear_pid()
        return
    if pid is None:
        _cprint(C.YELLOW, "  ! PID missing — clearing stale file.")
        _clear_pid()
        return

    try:
        if psutil:
            p = psutil.Process(pid)
            if os.name == "nt":
                try:
                    p.send_signal(signal.CTRL_BREAK_EVENT)
                except Exception:
                    p.terminate()
            else:
                p.terminate()

            try:
                p.wait(timeout=15)
                _cprint(C.GREEN, "  ✓ Bot stopped cleanly.")
            except psutil.TimeoutExpired:
                _cprint(C.YELLOW, "  ! Timeout — forcing kill...")
                for child in p.children(recursive=True):
                    with contextlib.suppress(Exception):
                        child.kill()
                p.kill()
                _cprint(C.GREEN, "  ✓ Bot killed.")
        else:
            os.kill(pid, signal.SIGTERM)
            _cprint(C.GREEN, "  ✓ Bot stopped.")
    except Exception as e:
        _cprint(C.RED, f"  ✗ Error while stopping: {e}")
    _clear_pid()


def restart() -> None:
    _cprint(C.CYAN, "  ..) Restarting...")
    stop()
    _clear_pid()
    time.sleep(2)
    start()


def status() -> None:
    running = _is_running()
    if running:
        _cprint(C.GREEN, f"  ● Bot is RUNNING (PID {_read_pid()}) | uptime={_bot_uptime_str()}")
    else:
        _cprint(C.YELLOW, "  ● Bot is STOPPED")

    if not os.path.exists(DB_PATH):
        _cprint(C.GRAY, "  (No database yet — run the bot once.)")
        return

    try:
        conn = _sqlite_connect(DB_PATH)
        cur = conn.cursor()
        total = cur.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        leads = cur.execute("SELECT COUNT(*) FROM messages WHERE keywords!=''").fetchone()[0]
        hot = cur.execute("SELECT COUNT(*) FROM messages WHERE opportunity_type='داغ'").fetchone()[0]

        log_info('')
        _cprint(C.DIM, "  ─── Overall stats ───")
        _cprint(C.WHITE, f"    Total messages : {total:,}")
        _cprint(C.WHITE, f"    Leads          : {leads:,}")
        _cprint(C.WHITE, f"    Hot            : {hot:,}")

        rows = list(cur.execute(
            "SELECT platform, COUNT(*) FROM messages WHERE keywords!='' "
            "GROUP BY platform ORDER BY 2 DESC"
        ))
        if rows:
            log_info('')
            _cprint(C.DIM, "  ─── By platform ───")
            for p, n in rows:
                _cprint(C.WHITE, f"    {p:12s} : {n:,}")

        log_info('')
        _cprint(C.DIM, "  ─── Platforms state ───")
        state = _get_platforms_state()
        for name, enabled in state.items():
            label = _PLATFORM_LABELS.get(name, name)
            _cprint(C.GREEN if enabled else C.GRAY, f"    {'✅' if enabled else '⏹ '} {label}")
        conn.close()
    except Exception as e:
        _cprint(C.RED, f"  ✗ Error reading stats: {e}")


# ══════════════════════════════════════════════════════════════════════════
#  📊 Actions — Data & Reports
# ══════════════════════════════════════════════════════════════════════════
def login() -> None:
    _cprint(C.CYAN, "  ..) Interactive platform login...")
    try:
        subprocess.call([PYEXE, os.path.join(ROOT, "login.py")], cwd=ROOT)
    except Exception as e:
        _cprint(C.RED, f"  ✗ Login failed: {e}")


def backup() -> None:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if not os.path.exists(DB_PATH):
        _cprint(C.YELLOW, "  ! No database to back up.")
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"manual_messages_{stamp}.db")
    try:
        src = _sqlite_connect(DB_PATH)
        dst = sqlite3.connect(dest)
        src.backup(dst)
        dst.close()
        src.close()
        _cprint(C.GREEN, f"  ✓ Backup saved: {dest}")
    except Exception as e:
        _cprint(C.RED, f"  ✗ Backup failed: {e}")


def _ask_days(default: int = 30) -> int:
    s = _ask_input(f"  Days [{default}] (0 = all): ")
    return max(0, int(s.strip())) if s and s.strip().isdigit() else default


def _ask_platform() -> str | None:
    log_info('')
    _cprint(C.DIM, "  ─── Select platform ───")
    _cprint(C.GRAY, "    [0] All platforms (default)")
    for i, (_code, label) in enumerate(_PLATFORM_CHOICES, 1):
        _print(f"    [{i}] {label}")

    c = _ask_input("  Number: ")
    if c is None:
        raise _Cancelled()
    c = c.strip()
    if not c or c == "0":
        return None
    try:
        idx = int(c) - 1
        if 0 <= idx < len(_PLATFORM_CHOICES):
            return _PLATFORM_CHOICES[idx][0]
    except ValueError:
        pass
    _cprint(C.YELLOW, "  ! Invalid selection → all platforms")
    return None


def _ask_crm_mode() -> tuple[str | None, bool, bool] | None:
    log_info('')
    _cprint(C.DIM, "  ─── CRM export mode ───")
    _cprint(C.GRAY, "    [1] All platforms in one file (default)")
    _cprint(C.GRAY, "    [2] Single platform only")
    _cprint(C.GRAY, "    [3] One file per platform (split)")
    _cprint(C.GRAY, "    [4] Single Excel with multiple sheets")

    c = _ask_input("  Number [1]: ")
    if c is None:
        return None
    c = (c or "1").strip() or "1"
    if c == "2":
        try:
            return _ask_platform(), False, False
        except _Cancelled:
            return None
    if c == "3":
        return None, True, False
    if c == "4":
        return None, False, True
    return None, False, False


def report() -> None:
    days = _ask_days(default=7)
    try:
        platform = _ask_platform()
    except _Cancelled:
        return
    label = platform or "all platforms"
    _cprint(C.CYAN, f"  ..) Generating reports (days={days or 'all'}, {label})...")
    date_from = (
        (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        if days and days > 0 else None
    )
    try:
        from core.services.daily_reporter import (
            generate_detailed_report,
            generate_executive_report,
        )
        exec_path = generate_executive_report(date_from=date_from, date_to=None, platform=platform)
        detail_path = generate_detailed_report(platform=platform, date_from=date_from, limit=200)
        if exec_path or detail_path:
            _cprint(C.GREEN, f"  ✓ Reports generated ({label})")
            if exec_path:
                _cprint(C.WHITE, f"      • Executive : {exec_path}")
            if detail_path:
                _cprint(C.WHITE, f"      • Detailed  : {detail_path}")
        else:
            _cprint(C.YELLOW, "  ! No reports generated.")
    except Exception as e:
        _cprint(C.RED, f"  ✗ Error: {type(e).__name__}: {e}")


def crm() -> None:
    days = _ask_days(default=30)
    mode = _ask_crm_mode()
    if mode is None:
        return
    platform, split, multi_sheet = mode
    mode_label = "split" if split else "multi_sheet" if multi_sheet else (platform or "all")
    _cprint(C.CYAN, f"  ..) Exporting CRM (days={days or 'all'}, mode={mode_label})...")
    try:
        from core.services.crm_export import export_crm
        res = export_crm(days=days, platform=platform, split=split, multi_sheet=multi_sheet)
        if not res:
            _cprint(C.YELLOW, "  ! No export generated.")
            return

        if split or "by_platform" in res:
            by = res.get("by_platform") or {}
            if not by:
                _cprint(C.GRAY, "  i) No platform had any leads.")
                return
            _cprint(C.GREEN, f"\n  ✓ {res.get('count', 0)} leads across {len(by)} platforms:")
            for p, info in by.items():
                _cprint(C.WHITE, f"      • {p}: {info.get('count', 0)} leads")
                if info.get("csv"):
                    _cprint(C.GRAY, f"        CSV : {info['csv']}")
                if info.get("xlsx"):
                    _cprint(C.GRAY, f"        XLSX: {info['xlsx']}")
        else:
            label = res.get("platform") or "all"
            _cprint(C.GREEN, f"\n  ✓ {res.get('count', 0)} leads ({label}):")
            for k in ("csv", "json", "xlsx"):
                if res.get(k):
                    _cprint(C.WHITE, f"      {k.upper():5s}: {res[k]}")
    except Exception as e:
        _cprint(C.RED, f"  ✗ Error: {type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════════════════════
#  🔧 Actions — System & Logs
# ══════════════════════════════════════════════════════════════════════════
def _run_script(filename: str, label: str) -> bool:
    path = os.path.join(ROOT, "tests", filename)
    if not os.path.exists(path):
        _cprint(C.RED, f"  ✗ Not found: {path}")
        return False

    log_path = os.path.join(
        CONSOLE_LOGS_DIR,
        f"{_safe_filename(label)}_{datetime.now():%Y%m%d_%H%M%S}.log",
    )
    os.makedirs(CONSOLE_LOGS_DIR, exist_ok=True)

    log_info('')
    _cprint(C.CYAN, f"  ▶  {label}")
    _cprint(C.DIM, "  " + "─" * 56)
    _cprint(C.GRAY, f"  📁 Log: {os.path.relpath(log_path, ROOT)}")
    log_info('')

    rc = -1
    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as logf:
            proc = subprocess.Popen(
                [PYEXE, path], cwd=ROOT, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1,
            )
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                logf.write(line)
                logf.flush()
            rc = proc.wait()
    except KeyboardInterrupt:
        _cprint(C.GRAY, "\n  i) Cancelled.")
        return False
    except Exception as e:
        _cprint(C.RED, f"\n  ✗ {label} failed: {type(e).__name__}: {e}")
        return False

    if rc == 0:
        _cprint(C.GREEN, f"\n  ✓ {label} PASSED.")
        return True
    _cprint(C.YELLOW, f"\n  ! {label} FAILED (rc={rc}).")
    return False


def tests() -> None:
    log_info('')
    _cprint(C.DIM, "  ─── Test options ───")
    _cprint(C.GRAY, "    [ 0] All platforms + Storage + DB + Health")
    _print("    [ 1] Bale         [ 2] Eitaa         [ 3] Rubika       [ 4] Telegram")
    _print("    [ 5] WhatsApp     [ 6] LinkedIn      [ 7] Web          [ 8] Health Check")
    _print("    [ 9] Storage      [10] Database      [11] Instagram    [12] YouTube")
    _print("    [13] Facebook")
    log_info('')
    choice = _ask_input("\n  Choice [0-13] (Enter = all): ") or "0"
    choice = choice.strip()

    if choice == "8":
        _run_script("test_healthcheck_full.py", "Health Check")
        return
    if choice == "9":
        _run_script("test_storage_full.py", "StorageHandler")
        return
    if choice == "10":
        _run_script("test_db_full.py", "Database")
        return

    _TEST_PLATFORMS = {
        "0": None, "1": ["bale"], "2": ["eitaa"], "3": ["rubika"],
        "4": ["telegram"], "5": ["whatsapp"], "6": ["linkedin"],
        "7": ["web"], "11": ["instagram"], "12": ["youtube"],
        "13": ["facebook"],
    }
    if choice not in _TEST_PLATFORMS:
        _cprint(C.RED, f"  ✗ Invalid choice: {choice}")
        return

    platforms = _TEST_PLATFORMS[choice]
    if platforms is None:
        _run_script("run_all_tests.py", "All Enabled Platforms")
        _run_script("test_storage_full.py", "StorageHandler")
        _run_script("test_db_full.py", "Database")
        return

    runner = os.path.join(ROOT, "tests", "run_all_tests.py")
    if not os.path.exists(runner):
        _cprint(C.RED, f"  ✗ Test runner not found: {runner}")
        return

    _cprint(C.CYAN, f"\n  ..) Running tests for: {', '.join(platforms)}...")
    _cprint(C.GRAY, "      (may take a few minutes)")
    subprocess.call([PYEXE, runner, "--only"] + platforms, cwd=ROOT)


def cleanup() -> None:
    _cprint(C.CYAN, "  ..) Cleaning up old files...")
    try:
        import asyncio

        from core.database import create_database
        from core.services.cleanup_service import CleanupService

        async def _run() -> None:
            db = create_database()
            await db.init()
            try:
                svc = CleanupService(db)
                await asyncio.wait_for(svc.run_once(), timeout=_CLEANUP_TIMEOUT_S)
            finally:
                await db.close()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())
        loop.close()
        _cprint(C.GREEN, "  ✓ Cleanup complete.")
    except TimeoutError:
        _cprint(C.RED, f"  ✗ Timeout after {_CLEANUP_TIMEOUT_S}s")
    except Exception as e:
        _cprint(C.RED, f"  ✗ Error: {type(e).__name__}: {e}")


def health() -> None:
    _cprint(C.CYAN, "  ..) Health & metrics")
    port = os.getenv("OBS_PORT", "8787")
    _cprint(C.DIM, "  ─── Endpoints ───")
    _cprint(C.WHITE, f"    http://localhost:{port}/health")
    _cprint(C.WHITE, f"    http://localhost:{port}/metrics")
    try:
        import psutil as _ps
        log_info('')
        _cprint(C.DIM, "  ─── System resources ───")
        _cprint(C.WHITE, f"    CPU  : {_ps.cpu_percent(interval=0.3)}%")
        _cprint(C.WHITE, f"    RAM  : {_ps.virtual_memory().percent}%")
        _cprint(C.WHITE, f"    Disk : {_ps.disk_usage('C:\\' if os.name == 'nt' else '/').percent}%")
    except Exception:
        pass
    log_info('')
    status()


def ai_status() -> None:
    _cprint(C.CYAN, "  ..) AI + Admin status")
    import urllib.request
    try:
        from config import AI
    except Exception as e:
        _cprint(C.RED, f"  ✗ config error: {e}")
        return
    try:
        with urllib.request.urlopen(AI["url"].rstrip("/") + "/api/tags", timeout=4) as r:
            tags = json.loads(r.read().decode("utf-8"))
        _cprint(C.GREEN, f"  ✓ Ollama UP at {AI['url']}")
        _cprint(C.WHITE, f"    model: {AI['model']}")
        models = [
            m.get("name", "?")
            for m in tags.get("models", [])
            if m.get("name")
        ]
        if models:
            _cprint(C.WHITE, f"    available: {', '.join(models[:5])}")
    except Exception:
        _cprint(C.YELLOW, "  ! Ollama DOWN")
    ids = PLUGINS_CONFIG.get("adminhandler", {}).get("admin_ids", [])
    _cprint(C.WHITE if ids else C.GRAY, f"  Admin IDs: {', '.join(ids) if ids else '(empty)'}")


def open_chat() -> None:
    port = os.getenv("OBS_PORT", "8787")
    url = f"http://localhost:{port}/chat"
    _cprint(C.CYAN, f"  ..) Opening local AI chat: {url}")
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


def requests_and_errors() -> None:
    _cprint(C.CYAN, "  ..) Feature requests + recent errors")
    if os.path.exists(REQUESTS_FILE):
        try:
            with open(REQUESTS_FILE, encoding="utf-8") as f:
                reqs = [json.loads(line) for line in f if line.strip()]
            _cprint(C.WHITE, f"  feature requests: {len(reqs)}")
        except Exception:
            pass
    else:
        _cprint(C.GRAY, "  feature requests: 0")

    errs = 0
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                for line in f.readlines()[-500:]:
                    try:
                        if json.loads(line).get("level") in ("ERROR", "CRITICAL"):
                            errs += 1
                    except Exception:
                        pass
        except Exception:
            pass
    _cprint(C.WHITE, f"  recent errors: {errs}")


def recent_reports() -> None:
    if not os.path.isdir(REPORTS_DIR):
        _cprint(C.YELLOW, "  ! Reports folder does not exist.")
        return
    files = []
    for name in os.listdir(REPORTS_DIR):
        if name.endswith(".tmp") or name.startswith("."):
            continue
        p = os.path.join(REPORTS_DIR, name)
        if os.path.isfile(p):
            with contextlib.suppress(OSError):
                files.append((os.path.getmtime(p), name, os.path.getsize(p)))

    files.sort(reverse=True)
    if not files:
        _cprint(C.GRAY, "  i) No files in reports.")
        return

    _cprint(C.CYAN, f"\n  Latest {min(15, len(files))} files:")
    for mtime, name, size in files[:15]:
        _cprint(
            C.WHITE,
            f"    [{datetime.fromtimestamp(mtime):%Y-%m-%d %H:%M}] "
            f"{name:45s}  ({size/1024:.1f} KB)",
        )


def console_logs() -> None:
    if not os.path.isdir(CONSOLE_LOGS_DIR):
        _cprint(C.YELLOW, "  ! No console logs yet.")
        return
    files = []
    for name in os.listdir(CONSOLE_LOGS_DIR):
        p = os.path.join(CONSOLE_LOGS_DIR, name)
        if os.path.isfile(p) and name.endswith(".log"):
            with contextlib.suppress(OSError):
                files.append((os.path.getmtime(p), name, os.path.getsize(p)))
    files.sort(reverse=True)
    if not files:
        _cprint(C.GRAY, "  i) No .log files.")
        return

    _cprint(C.CYAN, "\n  📁 Console Logs")
    for mtime, name, size in files[:20]:
        _cprint(
            C.WHITE,
            f"    [{datetime.fromtimestamp(mtime):%Y-%m-%d %H:%M:%S}] "
            f"{size/1024:>7.1f} KB  {name}",
        )

    log_info('')
    _cprint(C.WHITE, "    [o] Open folder | [l] View latest | [c] Clear | [Enter] Back")
    action = (_ask_input("  Action: ") or "").strip().lower()
    if action == "o":
        _open_path(CONSOLE_LOGS_DIR)
    elif action == "l" and files:
        _open_path(os.path.join(CONSOLE_LOGS_DIR, files[0][1]))
    elif action == "c" and _confirm("  Clear all logs? [y/N]: "):
        for _, name, _ in files:
            with contextlib.suppress(Exception):
                os.remove(os.path.join(CONSOLE_LOGS_DIR, name))
        _cprint(C.GREEN, "  ✓ Logs cleared.")


# ══════════════════════════════════════════════════════════════════════════
#  🌿 Git Management
# ══════════════════════════════════════════════════════════════════════════
def _git(args: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["git"] + args, cwd=ROOT, capture_output=True, text=True,
            timeout=120, encoding="utf-8", errors="replace", check=False,
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return 127, "git is not installed"
    except subprocess.TimeoutExpired:
        return 124, "git timed out"
    except Exception as e:
        return 1, str(e)


def _git_is_repo() -> bool:
    return _git(["rev-parse", "--is-inside-work-tree"])[0] == 0


def github_push() -> None:
    _cprint(C.CYAN, "  ..) Push to GitHub")
    if not _git_is_repo():
        _cprint(C.RED, "  ✗ Not a git repository.")
        return
    if ".env" in _git(["ls-files", ".env"])[1]:
        _cprint(C.RED, "  ✗ ABORT: .env is tracked!")
        return

    rc, status_out = _git(["status", "--short"])
    if status_out.strip():
        _cprint(C.CYAN, "    Pending changes:")
        for line in status_out.strip().splitlines()[:10]:
            _cprint(C.WHITE, f"      {line}")
    else:
        _cprint(C.GRAY, "    (no changes)")

    if not _confirm("\n  Continue? [y/N]: "):
        return
    msg = (
        (_ask_input("  Commit message (Enter = auto): ") or "").strip()
        or f"Update via panel {datetime.now():%Y-%m-%d %H:%M}"
    )

    _git(["add", "-A"])
    rc, out = _git(["commit", "-m", msg])
    if rc != 0 and "nothing to commit" not in out.lower():
        _cprint(C.RED, f"  ✗ Commit failed:\n{out}")
        return
    _cprint(C.GREEN, "  ✓ Committed.")

    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])[1].strip() or "main"
    for remote in [r for r in _PUSH_REMOTES if r in _git(["remote"])[1]]:
        _cprint(C.CYAN, f"\n  ..) Pushing to {remote}/{branch}...")
        rc, out = _git(["push", "-u", remote, branch])
        if rc == 0:
            _cprint(C.GREEN, f"  ✓ {remote}/{branch}")
        else:
            _cprint(C.RED, f"  ✗ {remote}:\n      {out.strip()[:300]}")


def github_pull() -> None:
    _cprint(C.CYAN, "  ..) Pull from GitHub")
    if not _git_is_repo() or "origin" not in _git(["remote"])[1]:
        _cprint(C.RED, "  ✗ Not a git repo or no origin remote.")
        return
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])[1].strip() or "main"

    _cprint(C.CYAN, f"\n  ..) Fetching origin/{branch}...")
    if _git(["fetch", "origin", branch])[0] != 0:
        _cprint(C.RED, "  ✗ Fetch failed.")
        return

    ahead = int(_git(["rev-list", "--count", f"origin/{branch}..HEAD"])[1].strip() or 0)
    behind = int(_git(["rev-list", "--count", f"HEAD..origin/{branch}"])[1].strip() or 0)
    _cprint(C.WHITE, f"    {ahead} ahead, {behind} behind")
    if behind == 0:
        _cprint(C.GRAY, "  i) Nothing new to pull.")
        return

    if _confirm(f"\n  Pull {behind} commit(s)? [y/N]: "):
        rc, out = _git(["pull", "--no-rebase", "origin", branch])
        _cprint(C.GREEN if rc == 0 else C.RED, f"  {'✓ Pulled successfully.' if rc == 0 else '✗ Pull failed:\n' + out}")


def github_status() -> None:
    _cprint(C.CYAN, "  ..) Git status")
    if not _git_is_repo():
        _cprint(C.RED, "  ✗ Not a git repository.")
        return
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])[1].strip() or "main"
    _cprint(C.BOLD + C.CYAN, f"\n  Branch: {branch}")

    for line in _git(["remote", "-v"])[1].strip().splitlines()[:5]:
        _cprint(C.WHITE, f"      {line}")
    last = _git(["log", "-1", "--pretty=%h  %s  (%cr)"])[1].strip()
    if last:
        _cprint(C.WHITE, f"  Last commit: {last}")

    _rc, status_out = _git(["status", "--short"])
    if not status_out.strip():
        _cprint(C.GREEN, "\n  ✓ Working tree clean.")
        return
    lines = status_out.strip().splitlines()
    _cprint(C.YELLOW, f"\n  ! {len(lines)} uncommitted change(s):")
    for line in lines[:15]:
        _cprint(C.WHITE, f"      {line}")


# ══════════════════════════════════════════════════════════════════════════
#  🖥️ UI & Menu Rendering
# ══════════════════════════════════════════════════════════════════════════
def _show_header() -> None:
    running = _is_running()
    stats = _menu_stats()
    W = _HEADER_INNER_WIDTH

    line1 = "  Srooyesh Lead-Monitor (Smart Edition)"

    if running:
        status = (
            f"{C.GREEN}●{C.RESET} RUNNING  ·  PID {_read_pid()}  ·  uptime {_bot_uptime_str()}"
        )
    else:
        status = f"{C.YELLOW}○{C.RESET} STOPPED"
    line2 = f"  {status}"

    leads, hot, total = stats.get("leads", 0), stats.get("hot", 0), stats.get("total", 0)
    line3 = (
        f"  Leads: {leads:,}  ·  Hot: {hot:,}  ·  Total: {total:,}"
        if isinstance(leads, int) and isinstance(total, int) else "  (no DB yet)"
    )

    state = _get_platforms_state()
    active = sum(1 for v in state.values() if v)
    parts = []
    for name, enabled in state.items():
        short = _PLATFORM_SHORT.get(name, name[:4])
        parts.append(
            f"{C.GREEN}{short}{C.RESET}" if enabled
            else f"{C.GRAY}{short}{C.RESET}"
        )
    line4 = f"  Platforms: {active}/{len(state)}  [ " + " · ".join(parts) + " ]"
    if _visible_len(line4) > W:
        line4 = f"  Platforms: {active}/{len(state)} فعال"

    _cprint(C.BOLD + C.MAGENTA, "╭" + "─" * W + "╮")
    _cprint(C.WHITE, f"│{_pad(line1, W)}│")
    _cprint(C.WHITE, f"│{_pad(line2, W)}│")
    _cprint(C.WHITE, f"│{_pad(line3, W)}│")
    _cprint(C.CYAN,  f"│{_pad(line4, W)}│")
    _cprint(C.BOLD + C.MAGENTA, "╰" + "─" * W + "╯")


def _section(title: str, items: list[tuple[str, str]]) -> None:
    log_info('')
    _cprint(C.BOLD + C.CYAN, f"  {title}")
    cells = [f"{C.GRAY}[{num:>2}]{C.RESET} {label}" for num, label in items]
    for i in range(0, len(cells), _MENU_COLS):
        _print("    " + " ".join(_pad(c, _MENU_CELL_W) for c in cells[i:i + _MENU_COLS]).rstrip())


def show_menu() -> None:
    log_info('')
    _show_header()
    _section("🎮  Bot Control", [("1", "Start"), ("2", "Restart"), ("3", "Stop"), ("4", "Status")])
    _section("📊  Data & Reports", [("5", "Login"), ("6", "Backup"), ("7", "Reports"), ("8", "CRM"), ("9", "Tests")])
    _section("🔧  System", [
        ("10", "Cleanup"), ("12", "Health"), ("13", "AI/Admin"),
        ("14", "Requests"), ("15", "AI Chat"), ("16", "Recent"),
        ("19", "Logs"),
    ])
    _section("🌿  Git", [("11", "Push"), ("17", "Pull"), ("18", "Git status")])
    log_info('')
    _print(f"    {C.GRAY}[ 0]{C.RESET}  Exit")
    log_info('')


# ══════════════════════════════════════════════════════════════════════════
#  🚀 Main Loop
# ══════════════════════════════════════════════════════════════════════════
_ACTIONS: dict[str, Callable[[], None]] = {
    "1": start, "2": restart, "3": stop, "4": status,
    "5": login, "6": backup, "7": report, "8": crm, "9": tests,
    "10": cleanup, "11": github_push, "12": health,
    "13": ai_status, "14": requests_and_errors, "15": open_chat,
    "16": recent_reports, "17": github_pull, "18": github_status,
    "19": console_logs,
}


def main() -> None:
    while True:
        try:
            _clear_screen()
            show_menu()

            choice = _ask_input("  → ")
            if choice is None or choice.strip() == "0":
                _cprint(C.GRAY, "\n  Bye.")
                break

            choice = choice.strip()
            if not choice:
                continue

            fn = _ACTIONS.get(choice)
            if not fn:
                _cprint(C.RED, "  ✗ Invalid choice. Please enter a number between 0 and 19.")
                time.sleep(1.5)
                continue

            fn()

        except KeyboardInterrupt:
            _cprint(C.GRAY, "\n  i) Cancelled.")
            continue
        except Exception as e:
            _cprint(C.RED, f"\n  ✗ Unexpected Error: {type(e).__name__}: {e}")
            _cprint(C.GRAY, "     Press Enter to continue...")
            _ask_input("")
        finally:
            if 'choice' in locals() and choice not in (None, "0"):
                _cprint(C.DIM, "\n  (Press Enter to return to menu...)")
                _ask_input("")


if __name__ == "__main__":
    main()