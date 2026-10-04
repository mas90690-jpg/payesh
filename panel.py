"""
Control panel for the Srooyesh lead-monitoring bot — numeric menu.

Run: double-click panel.bat  (or: python panel.py)

نسخهٔ ۱۲ (این نسخه):
  - 🌐 همهٔ متن‌ها انگلیسی (کاملاً انگلیسی).
  - 🎨 منوی جمع‌وجور شبکه‌ای (grid, 4 columns).
  - 🎨 تراز کامل با _visible_len + _pad (ANSI-safe).
  - 🎯 نوار وضعیت زنده در هدر.
  - ✅ حفظ همهٔ ۱۸ عملکرد (سازگاری عقب‌رو).
"""
from __future__ import annotations

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


# ══════════════════════════════════════════════════════════════════════
#  🎨 ANSI setup (must run first)
# ══════════════════════════════════════════════════════════════════════
def _init_ansi() -> bool:
    """Enable ANSI on Windows CMD."""
    if os.name != "nt":
        return True
    try:
        import colorama  # type: ignore
        colorama.just_fix_windows_console()
        return True
    except Exception:
        pass
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            if kernel32.SetConsoleMode(handle, mode.value | 0x0004):
                return True
    except Exception:
        pass
    return False


_ANSI_OK = _init_ansi()

try:
    from core.kernel.console_compat import patch_console
    patch_console()
except Exception:
    pass


# ══════════════════════════════════════════════════════════════════════
#  Paths & constants
# ══════════════════════════════════════════════════════════════════════
ROOT = os.path.dirname(os.path.abspath(__file__))
PYEXE = sys.executable or "python"

PID_FILE = os.path.join(ROOT, "storage", "bot.pid")
DB_PATH = os.path.join(ROOT, "storage", "database", "messages.db")
BACKUP_DIR = os.path.join(ROOT, "storage", "backups")
REPORTS_DIR = os.path.join(ROOT, "storage", "reports")
LOG_FILE = os.path.join(ROOT, "storage", "logs", "app.jsonl")
REQUESTS_FILE = os.path.join(ROOT, "storage", "requests.jsonl")

_SQLITE_TIMEOUT_S = 5.0
_CLEANUP_TIMEOUT_S = 120
_START_VERIFY_WAIT_S = 1.0
_LOG_PREVIEW_LEN = 80
_GIT_PREVIEW_LIMIT = 15
_GIT_HARD_LIMIT = 20
_MENU_DB_TIMEOUT_S = 0.5

_PUSH_REMOTES = ["origin", "backup"]

# ─── Menu layout ───
_HEADER_INNER_WIDTH = 56
_MENU_COLS = 4
_MENU_CELL_W = 17

try:
    import psutil
except ImportError:
    psutil = None


# ══════════════════════════════════════════════════════════════════════
#  🎨 Colors
# ══════════════════════════════════════════════════════════════════════
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


def _enable_colors() -> bool:
    if not _ANSI_OK:
        return False
    if os.getenv("NO_COLOR"):
        return False
    return True


_COLORS_OK = _enable_colors()

if not _COLORS_OK:
    for _attr in (
        "RESET", "BOLD", "DIM", "RED", "GREEN", "YELLOW",
        "BLUE", "MAGENTA", "CYAN", "WHITE", "GRAY",
    ):
        setattr(C, _attr, "")


# ══════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _visible_len(s: str) -> int:
    """Visible length (ignores ANSI codes)."""
    return len(_ANSI_RE.sub("", s))


def _pad(s: str, width: int) -> str:
    """Right-pad s to width, taking ANSI into account."""
    return s + " " * max(0, width - _visible_len(s))


def _print(msg: str = "") -> None:
    try:
        print(msg)
    except Exception:
        pass


def _cprint(color: str, msg: str) -> None:
    if _COLORS_OK and color:
        _print(f"{color}{msg}{C.RESET}")
    else:
        _print(msg)


class _Cancelled(Exception):
    """User cancelled with Ctrl+C or EOF."""
    pass


# ══════════════════════════════════════════════════════════════════════
#  Platform labels (English)
# ══════════════════════════════════════════════════════════════════════
_PLATFORM_LABELS: dict[str, str] = {
    "eitaa": "Eitaa",
    "bale": "Bale",
    "rubika": "Rubika",
    "telegram": "Telegram",
    "whatsapp": "WhatsApp",
    "web": "Web",
    "linkedin": "LinkedIn",
    "instagram": "Instagram",   # ← اضافه
}

try:
    from core.services.daily_reporter import PLATFORM_ORDER
    _PLATFORM_CHOICES: list[tuple[str, str]] = [
        (p, _PLATFORM_LABELS.get(p, p)) for p in PLATFORM_ORDER
    ]
except Exception:
    _PLATFORM_CHOICES = list(_PLATFORM_LABELS.items())


# ══════════════════════════════════════════════════════════════════════
#  SQLite + input helpers
# ══════════════════════════════════════════════════════════════════════
def _sqlite_connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=_SQLITE_TIMEOUT_S)
    try:
        conn.execute(
            "PRAGMA busy_timeout = ?",
            (int(_SQLITE_TIMEOUT_S * 1000),),
        )
    except Exception:
        pass
    return conn


def _menu_lead_count() -> str:
    if not os.path.exists(DB_PATH):
        return "—"
    try:
        conn = sqlite3.connect(DB_PATH, timeout=_MENU_DB_TIMEOUT_S)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM messages WHERE keywords != ''"
        )
        row = cur.fetchone()
        conn.close()
        n = int(row[0]) if row else 0
        return f"{n:,}"
    except Exception:
        return "?"


def _ask_input(prompt: str) -> str | None:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return None


def _confirm(prompt: str) -> bool:
    ans = _ask_input(prompt)
    if ans is None:
        return False
    return ans.strip().lower() in ("y", "yes")


def _run_script(filename: str, label: str) -> bool:
    path = os.path.join(ROOT, "tests", filename)
    if not os.path.exists(path):
        _cprint(C.RED, f"  ✗ Not found: {path}")
        return False

    print()
    _cprint(C.CYAN, f"  ▶  {label}")
    _cprint(C.DIM, "  " + "─" * 56)

    try:
        rc = subprocess.call([PYEXE, path], cwd=ROOT)
        if rc == 0:
            _cprint(C.GREEN, f"\n  ✓ {label} PASSED.")
            return True
        _cprint(C.YELLOW, f"\n  ! {label} FAILED (rc={rc}).")
        return False
    except KeyboardInterrupt:
        _cprint(C.GRAY, "\n  i) Cancelled.")
        return False
    except Exception as e:
        _cprint(C.RED, f"\n  ✗ {label} failed: {type(e).__name__}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════
#  Bot process management
# ══════════════════════════════════════════════════════════════════════
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
    try:
        os.remove(PID_FILE)
    except Exception:
        pass


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
        except Exception:
            return False

    try:
        p = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.AccessDenied:
        return False
    except Exception:
        return False

    try:
        if not p.is_running():
            return False
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False

    try:
        proc_name = (p.name() or "").lower()
    except Exception:
        proc_name = ""

    if proc_name and "python" not in proc_name:
        return False

    try:
        cmdline = p.cmdline() or []
    except Exception:
        return True

    if cmdline:
        cmd_str = " ".join(cmdline).lower()
        return "main.py" in cmd_str or "python" in cmd_str

    return True


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
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
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
                    try:
                        p.terminate()
                    except Exception:
                        pass
            else:
                try:
                    p.terminate()
                except Exception:
                    pass

            try:
                p.wait(timeout=15)
                _cprint(C.GREEN, "  ✓ Bot stopped cleanly.")
            except psutil.TimeoutExpired:
                _cprint(C.YELLOW, "  ! Timeout — forcing kill...")
                try:
                    for child in p.children(recursive=True):
                        try:
                            child.kill()
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    p.kill()
                except Exception:
                    pass
                _cprint(C.GREEN, "  ✓ Bot killed.")
        else:
            try:
                os.kill(pid, signal.SIGTERM)
                for _ in range(10):
                    time.sleep(0.5)
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        break
                else:
                    os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
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
    pid_str = str(_read_pid()) if running else "—"

    if running:
        _cprint(C.GREEN, f"  ● Bot is RUNNING (PID {pid_str})")
    else:
        _cprint(C.YELLOW, "  ● Bot is STOPPED")

    if not os.path.exists(DB_PATH):
        _cprint(C.GRAY, "  (No database yet — run the bot once.)")
        return

    conn = None
    try:
        conn = _sqlite_connect(DB_PATH)
        cur = conn.cursor()

        total = cur.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        leads = cur.execute(
            "SELECT COUNT(*) FROM messages WHERE keywords!=''"
        ).fetchone()[0]
        hot = cur.execute(
            "SELECT COUNT(*) FROM messages "
            "WHERE opportunity_type='داغ'"
        ).fetchone()[0]

        print()
        _cprint(C.DIM, "  ─── Overall stats ───")
        _cprint(C.WHITE, f"    Total messages : {total:,}")
        _cprint(C.WHITE, f"    Leads          : {leads:,}")
        _cprint(C.WHITE, f"    Hot            : {hot:,}")

        rows = list(cur.execute(
            "SELECT platform, COUNT(*) FROM messages "
            "WHERE keywords!='' GROUP BY platform ORDER BY 2 DESC"
        ))
        if rows:
            print()
            _cprint(C.DIM, "  ─── By platform ───")
            for p, n in rows:
                _cprint(C.WHITE, f"    {p:12s} : {n:,}")
    except Exception as e:
        _cprint(C.RED, f"  ✗ Error reading stats: {e}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════
#  Interactive inputs
# ══════════════════════════════════════════════════════════════════════
def _ask_days(default: int = 30) -> int:
    s = _ask_input(f"  Days [{default}] (0 = all): ")
    if s is None:
        return default
    s = s.strip()
    if not s:
        return default
    try:
        return max(0, int(s))
    except ValueError:
        _cprint(C.YELLOW, f"  ! Invalid value → {default}")
        return default


def _ask_platform() -> str | None:
    print()
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
    print()
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


# ══════════════════════════════════════════════════════════════════════
#  Tools — Data
# ══════════════════════════════════════════════════════════════════════
def login() -> None:
    _cprint(C.CYAN, "  ..) Interactive platform login...")
    try:
        subprocess.call(
            [PYEXE, os.path.join(ROOT, "login.py")],
            cwd=ROOT,
        )
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
        try:
            dst = sqlite3.connect(dest)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        _cprint(C.GREEN, f"  ✓ Backup saved: {dest}")
    except Exception as e:
        _cprint(C.RED, f"  ✗ Backup failed: {e}")


def report() -> None:
    days = _ask_days(default=7)

    try:
        platform = _ask_platform()
    except _Cancelled:
        _cprint(C.GRAY, "  i) Cancelled.")
        return

    label = platform or "all platforms"
    _cprint(C.CYAN, f"  ..) Generating reports (days={days or 'all'}, {label})...")

    date_from = None
    if days and days > 0:
        date_from = (
            datetime.now() - timedelta(days=days)
        ).strftime("%Y-%m-%d")

    try:
        from core.services.daily_reporter import (
            generate_detailed_report,
            generate_executive_report,
        )

        exec_path = generate_executive_report(
            date_from=date_from, date_to=None, platform=platform,
        )
        detail_path = generate_detailed_report(
            platform=platform, date_from=date_from, limit=200,
        )
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
        _cprint(C.GRAY, "  i) Cancelled.")
        return

    platform, split, multi_sheet = mode

    mode_label = (
        "split" if split
        else "multi_sheet" if multi_sheet
        else (platform or "all")
    )
    _cprint(C.CYAN, f"  ..) Exporting CRM (days={days or 'all'}, mode={mode_label})...")

    try:
        from core.services.crm_export import export_crm

        res = export_crm(
            days=days, platform=platform,
            split=split, multi_sheet=multi_sheet,
        )
        if not res:
            _cprint(C.YELLOW, "  ! No export generated.")
            return
        _print_crm_result(res, split)
    except Exception as e:
        _cprint(C.RED, f"  ✗ Error: {type(e).__name__}: {e}")


def _print_crm_result(res: dict[str, Any], split: bool) -> None:
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


def recent_reports() -> None:
    if not os.path.isdir(REPORTS_DIR):
        _cprint(C.YELLOW, "  ! Reports folder does not exist.")
        return

    files: list[tuple[float, str, int]] = []
    for name in os.listdir(REPORTS_DIR):
        if name.endswith(".tmp") or name.startswith("."):
            continue
        p = os.path.join(REPORTS_DIR, name)
        if not os.path.isfile(p):
            continue
        try:
            files.append((os.path.getmtime(p), name, os.path.getsize(p)))
        except OSError:
            continue

    files.sort(reverse=True)
    if not files:
        _cprint(C.GRAY, "  i) No files in reports.")
        return

    _cprint(C.CYAN, f"\n  Latest {min(15, len(files))} files:")
    for mtime, name, size in files[:15]:
        ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        _cprint(C.WHITE, f"    [{ts}] {name:45s}  ({size/1024:.1f} KB)")


def tests() -> None:
    print()
    _cprint(C.DIM, "  ─── Test options ───")
    _cprint(C.GRAY, "    [ 0] All (8 platforms + Storage + DB + Health)")
    _print("    [ 1] Bale         [ 2] Eitaa")
    _print("    [ 3] Rubika       [ 4] Telegram")
    _print("    [ 5] WhatsApp     [ 6] LinkedIn")
    _print("    [ 7] Web          [ 8] Health Check")
    _print("    [ 9] Storage      [10] Database")
    _print("    [11] Instagram")
    
    choice = _ask_input("\n  Choice [0-10] (Enter = all): ")
    if choice is None:
        _cprint(C.GRAY, "  i) Cancelled.")
        return
    choice = (choice or "0").strip() or "0"

    if choice == "8":
        _run_script("test_healthcheck_full.py", "Health Check")
        return
    if choice == "9":
        _run_script("test_storage_full.py", "StorageHandler")
        return
    if choice == "10":
        _run_script("test_db_full.py", "Database")
        return

    if choice == "11":
        _run_script("test_instagram_full.py", "Instagram")
        return
        
        return
    _TEST_PLATFORMS: dict[str, list[str] | None] = {
        "0": None,
        "1": ["bale"], "2": ["eitaa"], "3": ["rubika"],
        "4": ["telegram"], "5": ["whatsapp"],
        "6": ["linkedin"], "7": ["web"],
        "11": ["instagram"],   # ← اضافه
    }

    if choice not in _TEST_PLATFORMS:
        _cprint(C.RED, f"  ✗ Invalid choice: {choice}")
        return

    platforms = _TEST_PLATFORMS[choice]

    if platforms is None:
        _cprint(C.CYAN, "\n  ..) Mode [ALL] — running all tests")
        _run_script("run_all_tests.py", "8 platforms + Health Check")
        _run_script("test_storage_full.py", "StorageHandler")
        _run_script("test_db_full.py", "Database")
        return

    runner = os.path.join(ROOT, "tests", "run_all_tests.py")
    if not os.path.exists(runner):
        _cprint(C.RED, f"  ✗ Test runner not found: {runner}")
        return

    cmd = [PYEXE, runner, "--only"] + platforms
    _cprint(C.CYAN, f"\n  ..) Running tests for: {', '.join(platforms)}...")
    _cprint(C.GRAY, "      (may take a few minutes)\n")

    try:
        rc = subprocess.call(cmd, cwd=ROOT)
        if rc == 0:
            _cprint(C.GREEN, "\n  ✓ Tests PASSED.")
        else:
            _cprint(C.YELLOW, f"\n  ! Tests FAILED (rc={rc}).")
    except KeyboardInterrupt:
        _cprint(C.GRAY, "\n  i) Cancelled.")
    except Exception as e:
        _cprint(C.RED, f"\n  ✗ Error: {type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════════════════
#  Tools — System
# ══════════════════════════════════════════════════════════════════════
def cleanup() -> None:
    _cprint(C.CYAN, "  ..) Cleaning up old files...")

    loop = None
    try:
        import asyncio
        from core.database import create_database
        from core.services.cleanup_service import CleanupService

        async def _run() -> None:
            db = create_database()
            await db.init()
            try:
                svc = CleanupService(db)
                await asyncio.wait_for(
                    svc.run_once(), timeout=_CLEANUP_TIMEOUT_S
                )
            finally:
                await db.close()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())
        _cprint(C.GREEN, "  ✓ Cleanup complete.")
    except Exception as e:
        name = type(e).__name__
        if name == "TimeoutError":
            _cprint(C.RED, f"  ✗ Timeout after {_CLEANUP_TIMEOUT_S}s")
        else:
            _cprint(C.RED, f"  ✗ Error: {name}: {e}")
    finally:
        if loop is not None:
            try:
                loop.close()
            except Exception:
                pass


def health() -> None:
    _cprint(C.CYAN, "  ..) Health & metrics")
    port = os.getenv("OBS_PORT", "8787")
    _cprint(C.DIM, "  ─── Endpoints ───")
    _cprint(C.WHITE, f"    http://localhost:{port}/health")
    _cprint(C.WHITE, f"    http://localhost:{port}/metrics")

    try:
        import psutil as _ps
        disk_path = "C:\\" if os.name == "nt" else "/"
        print()
        _cprint(C.DIM, "  ─── System resources ───")
        _cprint(C.WHITE, f"    CPU  : {_ps.cpu_percent(interval=0.3)}%")
        _cprint(C.WHITE, f"    RAM  : {_ps.virtual_memory().percent}%")
        _cprint(C.WHITE, f"    Disk : {_ps.disk_usage(disk_path).percent}%")
    except Exception:
        pass

    print()
    status()


def ai_status() -> None:
    _cprint(C.CYAN, "  ..) AI + Admin status")
    import urllib.request

    try:
        from config import AI, PLUGINS_CONFIG
    except Exception as e:
        _cprint(C.RED, f"  ✗ config error: {type(e).__name__}: {e}")
        return

    try:
        url = AI["url"].rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=4) as r:
            tags = json.loads(r.read().decode("utf-8"))
        models = [m.get("name") for m in tags.get("models", [])]
        _cprint(C.GREEN, f"  ✓ Ollama UP at {AI['url']}")
        _cprint(C.WHITE, f"    model: {AI['model']}")
        _cprint(C.GRAY, f"    installed: {', '.join(models) or 'none'}")
    except Exception:
        _cprint(C.YELLOW, "  ! Ollama DOWN")

    ids = PLUGINS_CONFIG.get("adminhandler", {}).get("admin_ids", [])
    if ids:
        _cprint(C.WHITE, f"  Admin IDs: {', '.join(ids)}")
    else:
        _cprint(C.GRAY, "  Admin IDs: (empty)")


def open_chat() -> None:
    port = os.getenv("OBS_PORT", "8787")
    url = f"http://localhost:{port}/chat"
    _cprint(C.CYAN, f"  ..) Opening local AI chat: {url}")
    _cprint(C.GRAY, "      (bot must be running)")
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
            for r in reqs[-5:]:
                _cprint(C.GRAY, "    - " + str(r.get("request", ""))[:80])
        except Exception as e:
            _cprint(C.RED, f"  ✗ reading requests: {e}")
    else:
        _cprint(C.GRAY, "  feature requests: 0")

    errs = 0
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                lines = f.readlines()[-500:]
            for line in lines:
                try:
                    if json.loads(line).get("level") in ("ERROR", "CRITICAL"):
                        errs += 1
                except Exception:
                    pass
        except Exception as e:
            _cprint(C.RED, f"  ✗ reading log: {e}")
    _cprint(C.WHITE, f"  recent errors: {errs}")


# ══════════════════════════════════════════════════════════════════════
#  Git helpers
# ══════════════════════════════════════════════════════════════════════
def _git(args: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["git"] + args, cwd=ROOT,
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return 127, "git is not installed"
    except subprocess.TimeoutExpired:
        return 124, "git timed out"
    except Exception as e:
        return 1, f"{type(e).__name__}: {e}"


def _git_branch() -> str:
    rc, out = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    return (out or "main").strip() or "main" if rc == 0 else "main"


def _git_has_remote(name: str) -> bool:
    rc, out = _git(["remote"])
    return rc == 0 and name in (out or "")


def _git_is_repo() -> bool:
    rc, _ = _git(["rev-parse", "--is-inside-work-tree"])
    return rc == 0


def _git_ahead_behind(branch: str) -> tuple[int, int]:
    _, behind = _git(["rev-list", "--count", f"HEAD..origin/{branch}"])
    _, ahead = _git(["rev-list", "--count", f"origin/{branch}..HEAD"])
    try:
        behind_n = int((behind or "0").strip() or "0")
    except ValueError:
        behind_n = 0
    try:
        ahead_n = int((ahead or "0").strip() or "0")
    except ValueError:
        ahead_n = 0
    return ahead_n, behind_n


def _git_deleted_tracked_files() -> list[str]:
    rc, out = _git(["diff", "--name-only", "--diff-filter=D", "HEAD"])
    if rc != 0:
        return []
    return [f.strip() for f in (out or "").splitlines() if f.strip()]


def _git_restore_files(files: list[str]) -> int:
    if not files:
        return 0
    rc, _ = _git(["restore"] + files)
    if rc == 0:
        return len(files)
    rc, _ = _git(["checkout", "HEAD", "--"] + files)
    if rc == 0:
        return len(files)
    restored = 0
    for f in files:
        rc, _ = _git(["restore", f])
        if rc == 0:
            restored += 1
            continue
        rc, _ = _git(["checkout", "HEAD", "--", f])
        if rc == 0:
            restored += 1
    return restored


def _git_print_status_lines(status_out: str, limit: int = _GIT_PREVIEW_LIMIT) -> None:
    lines = [ln for ln in (status_out or "").strip().splitlines() if ln]
    for line in lines[:limit]:
        _cprint(C.WHITE, f"      {line}")
    if len(lines) > limit:
        _cprint(C.GRAY, f"      ... and {len(lines) - limit} more")


# ══════════════════════════════════════════════════════════════════════
#  Git — Push / Pull / Status
# ══════════════════════════════════════════════════════════════════════
def github_push() -> None:
    _cprint(C.CYAN, "  ..) Push to GitHub")

    rc, tracked = _git(["ls-files", ".env"])
    if ".env" in (tracked or ""):
        _cprint(C.RED, "  ✗ ABORT: .env is tracked by git — pushing would leak secrets")
        _cprint(C.GRAY, "      Fix: git rm --cached .env")
        return

    if not _git_is_repo():
        _cprint(C.RED, "  ✗ Not a git repository.")
        return

    available_remotes = [r for r in _PUSH_REMOTES if _git_has_remote(r)]
    if not available_remotes:
        _cprint(C.RED, "  ✗ No remote found.")
        return

    _cprint(C.WHITE, f"    Remotes: {', '.join(available_remotes)}")

    rc, status_out = _git(["status", "--short"])
    if status_out.strip():
        _cprint(C.CYAN, "    Pending changes:")
        _git_print_status_lines(status_out, _GIT_HARD_LIMIT)
    else:
        _cprint(C.GRAY, "    (no changes)")

    if not _confirm("\n  Continue? [y/N]: "):
        _cprint(C.GRAY, "  i) Cancelled.")
        return

    msg = _ask_input("  Commit message (Enter = auto): ")
    if msg is None:
        _cprint(C.GRAY, "  i) Cancelled.")
        return
    msg = msg.strip() or f"Update via panel {datetime.now():%Y-%m-%d %H:%M}"

    _git(["add", "-A"])
    rc, out = _git(["commit", "-m", msg])
    if rc != 0 and "nothing to commit" in out.lower():
        _cprint(C.YELLOW, "    Nothing new to commit.")
    elif rc != 0:
        _cprint(C.RED, f"  ✗ Commit failed:\n{out}")
        return
    else:
        _cprint(C.GREEN, "  ✓ Committed.")

    branch = _git_branch()
    results: dict[str, bool] = {}

    for remote in available_remotes:
        _cprint(C.CYAN, f"\n  ..) Pushing to {remote}/{branch}...")
        rc, out = _git(["push", "-u", remote, branch])
        if rc == 0:
            _cprint(C.GREEN, f"  ✓ {remote}/{branch}")
            results[remote] = True
        else:
            _cprint(C.RED, f"  ✗ {remote}:")
            _cprint(C.GRAY, f"      {out.strip()[:300]}")
            results[remote] = False

    ok = [r for r, v in results.items() if v]
    fail = [r for r, v in results.items() if not v]
    print()
    _cprint(C.DIM, "  ─── Summary ───")
    if ok:
        _cprint(C.GREEN, f"    ✓ {', '.join(ok)}")
    if fail:
        _cprint(C.RED, f"    ✗ {', '.join(fail)}")
        _cprint(C.YELLOW, "    💡 If SSL error, turn on VPN and retry.")


def github_pull() -> None:
    _cprint(C.CYAN, "  ..) Pull from GitHub")

    if not _git_is_repo():
        _cprint(C.RED, "  ✗ Not a git repository.")
        return
    if not _git_has_remote("origin"):
        _cprint(C.RED, "  ✗ No 'origin' remote.")
        return

    branch = _git_branch()

    deleted = _git_deleted_tracked_files()
    if deleted:
        _cprint(C.YELLOW, f"\n  ! {len(deleted)} deleted files:")
        for f in deleted[:_GIT_PREVIEW_LIMIT]:
            _cprint(C.WHITE, f"      D  {f}")
        if _confirm("\n  Restore them? [y/N]: "):
            n = _git_restore_files(deleted)
            _cprint(C.GREEN if n else C.RED, f"  {'✓' if n else '✗'} {n} files")

    _cprint(C.CYAN, f"\n  ..) Fetching origin/{branch}...")
    rc, out = _git(["fetch", "origin", branch])
    if rc != 0:
        _cprint(C.RED, f"  ✗ Fetch failed:\n{out}")
        return

    ahead_n, behind_n = _git_ahead_behind(branch)
    _cprint(C.WHITE, f"    {ahead_n} ahead, {behind_n} behind")

    if behind_n == 0:
        _cprint(C.GRAY, "  i) Nothing new to pull.")
        return

    if not _confirm(f"\n  Pull {behind_n} commit(s)? [y/N]: "):
        _cprint(C.GRAY, "  i) Cancelled.")
        return

    _cprint(C.CYAN, f"  ..) Pulling origin/{branch}...")
    rc, out = _git(["pull", "--no-rebase", "origin", branch])
    if rc == 0:
        _cprint(C.GREEN, f"  ✓ {behind_n} commit(s) applied.")
    else:
        _cprint(C.RED, f"  ✗ Pull failed:\n{out}")


def github_status() -> None:
    _cprint(C.CYAN, "  ..) Git status")

    if not _git_is_repo():
        _cprint(C.RED, "  ✗ Not a git repository.")
        return

    branch = _git_branch()
    _cprint(C.BOLD + C.CYAN, f"\n  Branch: {branch}")

    _, remotes = _git(["remote", "-v"])
    if (remotes or "").strip():
        for line in remotes.strip().splitlines()[:6]:
            _cprint(C.WHITE, f"      {line.strip()}")
    else:
        _cprint(C.GRAY, "      (none)")

    _, last = _git(["log", "-1", "--pretty=%h  %s  (%cr)"])
    if (last or "").strip():
        _cprint(C.WHITE, f"  Last commit: {last.strip()}")

    if _git_has_remote("origin"):
        _cprint(C.CYAN, f"\n  ..) Fetching origin/{branch}...")
        rc, _ = _git(["fetch", "origin", branch])
        if rc == 0:
            ahead_n, behind_n = _git_ahead_behind(branch)
            if ahead_n == 0 and behind_n == 0:
                _cprint(C.GREEN, "  ✓ 0 ahead, 0 behind")
            else:
                _cprint(C.YELLOW, f"  ! {ahead_n} ahead, {behind_n} behind")

    rc, status_out = _git(["status", "--short"])
    if not status_out.strip():
        _cprint(C.GREEN, "\n  ✓ Working tree clean.")
        return

    lines = [ln for ln in status_out.strip().splitlines() if ln]
    _cprint(C.YELLOW, f"\n  ! {len(lines)} uncommitted change(s):")
    _git_print_status_lines(status_out, _GIT_HARD_LIMIT)


# ══════════════════════════════════════════════════════════════════════
#  Menu — compact grid layout
# ══════════════════════════════════════════════════════════════════════
_ACTIONS: dict[str, Callable[[], None]] = {
    "1": start, "2": restart, "3": stop, "4": status,
    "5": login, "6": backup, "7": report, "8": crm, "9": tests,
    "10": cleanup, "11": github_push, "12": health,
    "13": ai_status, "14": requests_and_errors, "15": open_chat,
    "16": recent_reports, "17": github_pull, "18": github_status,
}


def _show_header() -> None:
    """Compact header with live status."""
    running = _is_running()
    leads = _menu_lead_count()

    if running:
        dot = f"{C.GREEN}●{C.RESET}" if _COLORS_OK else "●"
        status_str = f"{dot} RUNNING"
    else:
        dot = f"{C.YELLOW}○{C.RESET}" if _COLORS_OK else "○"
        status_str = f"{dot} STOPPED"

    W = _HEADER_INNER_WIDTH
    title_line = "  Srooyesh Lead-Monitor"
    status_line = f"  {status_str}     Leads: {leads}"

    _cprint(C.BOLD + C.MAGENTA, "╭" + "─" * W + "╮")
    _cprint(C.WHITE, f"│{_pad(title_line, W)}│")
    _cprint(C.GRAY, f"│{_pad(status_line, W)}│")
    _cprint(C.BOLD + C.MAGENTA, "╰" + "─" * W + "╯")


def _section(title: str, items: list[tuple[str, str]]) -> None:
    """Render a section as a 4-column grid."""
    print()
    _cprint(C.BOLD + C.CYAN, f"  {title}")

    cells: list[str] = []
    for num, label in items:
        if _COLORS_OK:
            cells.append(f"{C.GRAY}[{num:>2}]{C.RESET} {label}")
        else:
            cells.append(f"[{num:>2}] {label}")

    for i in range(0, len(cells), _MENU_COLS):
        row = cells[i:i + _MENU_COLS]
        line = "    " + " ".join(
            _pad(c, _MENU_CELL_W) for c in row
        ).rstrip()
        _print(line)


def show_menu() -> None:
    """Compact, clean menu."""
    print()
    _show_header()

    _section("🎮  Bot Control", [
        ("1", "Start"),
        ("2", "Restart"),
        ("3", "Stop"),
        ("4", "Status"),
    ])

    _section("📊  Data & Reports", [
        ("5", "Login"),
        ("6", "Backup"),
        ("7", "Reports"),
        ("8", "CRM"),
        ("9", "Tests"),
    ])

    _section("🔧  System", [
        ("10", "Cleanup"),
        ("12", "Health"),
        ("13", "AI/Admin"),
        ("14", "Requests"),
        ("15", "AI Chat"),
        ("16", "Recent"),
    ])

    _section("🌿  Git", [
        ("11", "Push"),
        ("17", "Pull"),
        ("18", "Git status"),
    ])

    print()
    if _COLORS_OK:
        _print(f"    {C.GRAY}[ 0]{C.RESET}  Exit")
    else:
        _print("    [ 0]  Exit")
    print()


def main() -> None:
    """Main loop."""
    while True:
        show_menu()

        choice = _ask_input("  → ")
        if choice is None:
            _cprint(C.GRAY, "\n  Bye.")
            break
        choice = choice.strip()

        if choice == "0":
            _cprint(C.GRAY, "  Bye.")
            break

        fn = _ACTIONS.get(choice)
        if not fn:
            _cprint(C.RED, "  ✗ Invalid choice.")
            continue

        try:
            fn()
        except KeyboardInterrupt:
            _cprint(C.GRAY, "\n  i) Cancelled.")
            continue
        except Exception as e:
            _cprint(C.RED, f"  ✗ Error: {type(e).__name__}: {e}")

        if os.name == "nt":
            try:
                os.system("pause >nul")
            except Exception:
                pass


if __name__ == "__main__":
    main()