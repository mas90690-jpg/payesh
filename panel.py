"""
Control panel for the Srooyesh lead-monitoring bot — numeric menu.

Run: double-click panel.bat  (or: python panel.py)

منو:
    1) Start              2) Restart            3) Stop
    4) Status & stats     5) Login              6) Backup
    7) Reports (platform) 8) CRM (mode)         9) Tests
    10) Cleanup           11) Push to GitHub    12) Health & metrics
    13) AI / Admin        14) Feature requests  15) Open local AI chat
    16) Recent reports    17) Pull from GitHub  18) Git status   0) Exit

نسخهٔ ۵ (این نسخه):
  - ⭐ NEW: github_pull حالا قبل از pull، فایل‌های trackشده‌ای که از
    دیسک پاک شده‌اند را (با تأیید کاربر) برمی‌گرداند — چون git pull
    فقط commit می‌آورد، نه فایل working directory پاک‌شده.
  - ⭐ NEW: github_status — نمایش کامل وضعیت گیت (branch، remote،
    ahead/behind، تغییرات uncommitted + خلاصه بر اساس نوع).
  - ⭐ NEW: گزینهٔ ۱۸ در منو — Git status.
  - 🔴 FIX امنیتی: _is_running حالا اول p.name() را چک می‌کند —
    جلوگیری از kill فرآیند اشتباه به‌خاطر PID recycling.
  - 🔴 FIX: _ask_platform لغو کاربر را از «همه پلتفرم‌ها» جدا می‌کند.
  - 🟠 FIX: _git حالا encoding="utf-8" + errors="replace" دارد.
  - 🟠 FIX: github_push حالا preview از git status می‌دهد.
  - 🎯 بهبود: start() ۱ ثانیه صبر می‌کند و بالا آمدن را تأیید می‌کند.
  - 🧹 ساختار مرتب: بخش‌بندی واضح، type hints کامل، docstring فارسی.
"""
from __future__ import annotations

import atexit
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from core.kernel.console_compat import patch_console

patch_console()


# ══════════════════════════════════════════════════════════════════════
#  مسیرها و ثابت‌ها
# ══════════════════════════════════════════════════════════════════════
ROOT = os.path.dirname(os.path.abspath(__file__))
PYEXE = sys.executable or "python"

PID_FILE = os.path.join(ROOT, "storage", "bot.pid")
DB_PATH = os.path.join(ROOT, "storage", "database", "messages.db")
BACKUP_DIR = os.path.join(ROOT, "storage", "backups")
REPORTS_DIR = os.path.join(ROOT, "storage", "reports")
LOG_FILE = os.path.join(ROOT, "storage", "logs", "app.jsonl")
REQUESTS_FILE = os.path.join(ROOT, "storage", "requests.jsonl")

# ─── timeoutها ───
_SQLITE_TIMEOUT_S = 5.0
_PYTEST_TIMEOUT_S = 300
_CLEANUP_TIMEOUT_S = 120

# ─── آستانه‌ها ───
_START_VERIFY_WAIT_S = 1.0
_LOG_PREVIEW_LEN = 80
_GIT_PREVIEW_LIMIT = 15
_GIT_HARD_LIMIT = 20

try:
    import psutil
except ImportError:
    psutil = None


# ══════════════════════════════════════════════════════════════════════
#  Exception برای لغو کاربر
# ══════════════════════════════════════════════════════════════════════
class _Cancelled(Exception):
    """کاربر ورودی را با Ctrl+C یا EOF لغو کرد."""
    pass


# ══════════════════════════════════════════════════════════════════════
#  پلتفرم‌ها — sync با daily_reporter.PLATFORM_ORDER
# ══════════════════════════════════════════════════════════════════════
_PLATFORM_LABELS = {
    "eitaa": "📱 ایتا",
    "bale": "📨 بله",
    "rubika": "🟣 روبیکا",
    "telegram": "✈️ تلگرام",
    "whatsapp": "🟢 واتساپ",
    "web": "🌐 وب",
    "linkedin": "💼 لینکدین",
}

try:
    from core.services.daily_reporter import PLATFORM_ORDER
    _PLATFORM_CHOICES: list[tuple[str, str]] = [
        (p, _PLATFORM_LABELS.get(p, p)) for p in PLATFORM_ORDER
    ]
except Exception:
    _PLATFORM_CHOICES = list(_PLATFORM_LABELS.items())


# ══════════════════════════════════════════════════════════════════════
#  helpers عمومی
# ══════════════════════════════════════════════════════════════════════
def _print(msg: str = "") -> None:
    """print امن (هرگز کرش نکند)."""
    try:
        print(msg)
    except Exception:
        pass


def _safe_str(val: Any) -> str:
    """None → ""، بقیه → str."""
    return "" if val is None else str(val)


def _sqlite_connect(path: str) -> sqlite3.Connection:
    """اتصال SQLite با timeout (جلوگیری از قفل دائمی)."""
    conn = sqlite3.connect(path, timeout=_SQLITE_TIMEOUT_S)
    try:
        conn.execute(
            "PRAGMA busy_timeout = ?",
            (int(_SQLITE_TIMEOUT_S * 1000),),
        )
    except Exception:
        pass
    return conn


def _ask_input(prompt: str) -> str | None:
    """input امن — None در صورت EOF/Ctrl+C."""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return None


def _confirm(prompt: str) -> bool:
    """پرسش بله/خیر — True اگه کاربر «y»/«yes»/«بله» بزند."""
    ans = _ask_input(prompt)
    if ans is None:
        return False
    return ans.strip().lower() in ("y", "yes", "بله")


# ══════════════════════════════════════════════════════════════════════
#  مدیریت فرآیند ربات
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
    """آیا فرآیند ربات در حال اجراست؟

    🔴 FIX: اول `p.name()` را چک می‌کند — جلوگیری از kill اشتباه
    به‌خاطر PID recycling.
    """
    pid = _read_pid()
    if not pid:
        return False

    # ─── بدون psutil (fallback) ───
    if psutil is None:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False
        except Exception:
            return False

    # ─── با psutil ───
    try:
        p = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.AccessDenied:
        _print(
            f"[!] PID {pid} قابل بررسی نیست (AccessDenied) — "
            f"به‌عنوان «نامعلوم» در نظر گرفته شد"
        )
        return False
    except Exception:
        return False

    try:
        if not p.is_running():
            return False
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False

    # ─── ۱) نام فرآیند ───
    try:
        proc_name = (p.name() or "").lower()
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.AccessDenied:
        proc_name = ""
    except Exception:
        proc_name = ""

    if proc_name and "python" not in proc_name:
        _print(
            f"[!] PID {pid} متعلق به «{proc_name}» است (نه python) "
            f"— احتمالاً PID recycling"
        )
        return False

    # ─── ۲) cmdline ───
    try:
        cmdline = p.cmdline() or []
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.AccessDenied:
        return True
    except Exception:
        return True

    if cmdline:
        cmd_str = " ".join(cmdline).lower()
        if "main.py" in cmd_str:
            return True
        if "python" in cmd_str:
            return True
        return False

    return True


def start() -> None:
    """شروع ربات در پنجره/سشن جدید — با تأیید راه‌اندازی."""
    if _is_running():
        _print(f"[!] Bot is already running (PID: {_read_pid()})")
        return

    main_py = os.path.join(ROOT, "main.py")
    if not os.path.exists(main_py):
        _print(f"[X] main.py not found: {main_py}")
        return

    kwargs: dict[str, Any] = {"cwd": ROOT}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_CONSOLE", 0
        )
    else:
        kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen([PYEXE, main_py], **kwargs)
        _write_pid(proc.pid)
        _print(f"[OK] Bot started (PID: {proc.pid}). Verifying...")

        time.sleep(_START_VERIFY_WAIT_S)
        if proc.poll() is not None:
            _print(
                f"[!] Bot exited immediately (rc={proc.returncode}) "
                f"— check logs in storage/logs/app.jsonl"
            )
            _clear_pid()
            return

        _print("[OK] Verified — bot is running.")
    except Exception as e:
        _print(f"[X] Failed to start bot: {e}")


def stop() -> None:
    """توقف ربات — ابتدا SIGTERM، سپس SIGKILL بعد از timeout."""
    pid = _read_pid()
    if not _is_running():
        _print("[i] Bot is not running.")
        _clear_pid()
        return

    if pid is None:
        _print("[!] PID missing — clearing stale file.")
        _clear_pid()
        return

    try:
        if psutil:
            p = psutil.Process(pid)
            try:
                children = p.children(recursive=True)
            except Exception:
                children = []

            for child in children:
                try:
                    child.terminate()
                except Exception:
                    pass

            try:
                p.terminate()
            except Exception:
                pass

            try:
                p.wait(timeout=10)
            except psutil.TimeoutExpired:
                try:
                    p.kill()
                except Exception:
                    pass
                for child in children:
                    try:
                        if child.is_running():
                            child.kill()
                    except Exception:
                        pass
            _print("[OK] Bot stopped.")
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
            _print("[OK] Bot stopped.")
    except Exception as e:
        _print(f"[X] Error while stopping: {e}")

    _clear_pid()


def restart() -> None:
    """ری‌استارت — با پاک‌سازی PID قبل از start."""
    _print("[..] Restarting...")
    stop()
    _clear_pid()
    time.sleep(2)
    start()


def status() -> None:
    """نمایش وضعیت + آمار."""
    running = _is_running()
    pid_str = str(_read_pid()) if running else "—"
    _print(
        f"Bot status: "
        f"{'RUNNING (PID ' + pid_str + ')' if running else 'STOPPED'}"
    )

    if not os.path.exists(DB_PATH):
        _print("(No database yet — run the bot once.)")
        return

    conn = None
    try:
        conn = _sqlite_connect(DB_PATH)
        cur = conn.cursor()

        row = cur.execute("SELECT COUNT(*) FROM messages").fetchone()
        total = row[0] if row else 0

        row = cur.execute(
            "SELECT COUNT(*) FROM messages WHERE keywords!=''"
        ).fetchone()
        leads = row[0] if row else 0

        row = cur.execute(
            "SELECT COUNT(*) FROM messages "
            "WHERE opportunity_type='داغ'"
        ).fetchone()
        hot = row[0] if row else 0

        _print("--- Leads by platform ---")
        for row in cur.execute(
            "SELECT platform, COUNT(*) FROM messages "
            "WHERE keywords!='' GROUP BY platform"
        ):
            _print(f"   {row[0]}: {row[1]}")

        _print(
            f"[#] Total messages: {total} | Leads: {leads} | Hot: {hot}"
        )
    except Exception as e:
        _print(f"(could not read stats: {e})")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════
#  ورودی‌های تعاملی
# ══════════════════════════════════════════════════════════════════════
def _ask_days(default: int = 30) -> int:
    """بازهٔ روز را می‌پرسد. «۰» = همه. پیش‌فرض = default."""
    s = _ask_input(f"بازه روز [{default}] (۰ = همه): ")
    if s is None:
        return default
    s = s.strip()
    if not s:
        return default
    try:
        return max(0, int(s))
    except ValueError:
        _print(f"[i] مقدار نامعتبر → {default}")
        return default


def _ask_platform() -> str | None:
    """زیرمنوی انتخاب پلتفرم.

    Returns:
        str: کد پلتفرم انتخاب‌شده
        None: «همه پلتفرم‌ها»

    Raises:
        _Cancelled: کاربر لغو کرد
    """
    _print("\n  انتخاب پلتفرم:")
    _print("    [0] همه پلتفرم‌ها (پیش‌فرض)")
    for i, (_code, label) in enumerate(_PLATFORM_CHOICES, 1):
        _print(f"    [{i}] {label}")

    c = _ask_input("شماره: ")
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

    _print("[i] انتخاب نامعتبر → همه پلتفرم‌ها")
    return None


def _ask_crm_mode() -> tuple[str | None, bool, bool] | None:
    """حالت خروجی CRM: (platform, split, multi_sheet).

    Returns:
        tuple: (platform, split, multi_sheet)
        None: لغو کاربر
    """
    _print("\n  حالت خروجی CRM:")
    _print("    [1] همه پلتفرم‌ها در یک فایل (پیش‌فرض)")
    _print("    [2] فقط یک پلتفرم مشخص")
    _print("    [3] هر پلتفرم، فایل جدا (split)")
    _print("    [4] یک فایل Excel با شیت جدا برای هر پلتفرم (multi_sheet)")

    c = _ask_input("شماره [1]: ")
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
#  ابزارها: Start/Login/Backup/Reports/CRM/Tests/Cleanup/Health/AI
# ══════════════════════════════════════════════════════════════════════
def login() -> None:
    """ورود تعاملی به پلتفرم‌ها."""
    _print("[..] Platform login (interactive)...")
    try:
        subprocess.call(
            [PYEXE, os.path.join(ROOT, "login.py")],
            cwd=ROOT,
        )
    except Exception as e:
        _print(f"[X] Login failed: {e}")


def backup() -> None:
    """پشتیبان امن از DB با sqlite3.backup() (نه copy فایل)."""
    os.makedirs(BACKUP_DIR, exist_ok=True)

    if not os.path.exists(DB_PATH):
        _print("[!] No database to back up.")
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
        _print(f"[OK] Backup saved: {dest}")
    except Exception as e:
        _print(f"[X] Backup failed: {e}")


def report() -> None:
    """گزارش‌گیری با پلتفرم اختیاری."""
    days = _ask_days(default=7)

    try:
        platform = _ask_platform()
    except _Cancelled:
        _print("[i] لغو شد.")
        return

    label = platform or "همه پلتفرم‌ها"
    _print(
        f"[..] Generating reports "
        f"(بازه={days or 'همه'} روز، {label})..."
    )

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
            _print(
                f"[OK] گزارش‌ها ساخته شدند "
                f"(platform={platform or 'all'})"
            )
            if exec_path:
                _print(f"     • مدیریتی: {exec_path}")
            if detail_path:
                _print(f"     • تخصصی : {detail_path}")
        else:
            _print("[!] هیچ گزارشی ساخته نشد (شاید سرنخی نیست).")
    except Exception as e:
        _print(f"[X] Report error: {type(e).__name__}: {e}")


def crm() -> None:
    """خروجی CRM با حالت‌های چندگانه."""
    days = _ask_days(default=30)

    mode = _ask_crm_mode()
    if mode is None:
        _print("[i] لغو شد.")
        return

    platform, split, multi_sheet = mode

    mode_label = (
        "split" if split
        else "multi_sheet" if multi_sheet
        else (platform or "همه")
    )
    _print(
        f"[..] Exporting CRM "
        f"(بازه={days or 'همه'} روز، حالت={mode_label})..."
    )

    try:
        from core.services.crm_export import export_crm

        res = export_crm(
            days=days, platform=platform,
            split=split, multi_sheet=multi_sheet,
        )
        if not res:
            _print("[!] خروجی ساخته نشد (شاید سرنخی نیست).")
            return
        _print_crm_result(res, split)
    except Exception as e:
        _print(f"[X] CRM export error: {type(e).__name__}: {e}")


def _print_crm_result(res: dict[str, Any], split: bool) -> None:
    """نمایش خروجی CRM — چه split چه عادی."""
    if split or "by_platform" in res:
        by = res.get("by_platform") or {}
        if not by:
            _print("[i] هیچ پلتفرمی سرنخ نداشت.")
            return
        _print(
            f"\n[OK] {res.get('count', 0)} سرنخ در {len(by)} پلتفرم:"
        )
        for p, info in by.items():
            _print(f"   • {p}: {info.get('count', 0)} سرنخ")
            if info.get("csv"):
                _print(f"     CSV : {info['csv']}")
            if info.get("xlsx"):
                _print(f"     XLSX: {info['xlsx']}")
    else:
        label = res.get("platform") or "همه پلتفرم‌ها"
        _print(f"\n[OK] {res.get('count', 0)} سرنخ ({label}):")
        if res.get("csv"):
            _print(f"   • CSV : {res['csv']}")
        if res.get("json"):
            _print(f"   • JSON: {res['json']}")
        if res.get("xlsx"):
            _print(f"   • XLSX: {res['xlsx']}")


def recent_reports() -> None:
    """نمایش آخرین فایل‌های تولیدشده در storage/reports."""
    if not os.path.isdir(REPORTS_DIR):
        _print("[!] پوشهٔ reports وجود ندارد.")
        return

    files: list[tuple[float, str, int]] = []
    for name in os.listdir(REPORTS_DIR):
        if name.endswith(".tmp") or name.startswith("."):
            continue
        p = os.path.join(REPORTS_DIR, name)
        if not os.path.isfile(p):
            continue
        try:
            files.append(
                (os.path.getmtime(p), name, os.path.getsize(p))
            )
        except OSError:
            continue

    files.sort(reverse=True)
    if not files:
        _print("[i] هیچ فایلی در reports نیست.")
        return

    _print(f"\n  آخرین {min(15, len(files))} فایل در storage/reports:")
    for mtime, name, size in files[:15]:
        ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        kb = size / 1024
        _print(f"   [{ts}] {name:50s}  ({kb:.1f} KB)")


def tests() -> None:
    """اجرای تست‌ها."""
    _print("[..] Running tests...")
    try:
        rc = subprocess.call(
            [PYEXE, "-m", "pytest", "-q"],
            cwd=ROOT,
            timeout=_PYTEST_TIMEOUT_S,
        )
        if rc == 0:
            _print("[OK] Tests passed.")
        else:
            _print(f"[!] Tests failed (rc={rc}).")
    except subprocess.TimeoutExpired:
        _print(f"[X] Tests timed out after {_PYTEST_TIMEOUT_S}s")
    except Exception as e:
        _print(f"[X] Tests failed: {e}")


def cleanup() -> None:
    """پاک‌سازی خودکار — با asyncio.new_event_loop (امن برای Windows)."""
    _print("[..] Cleaning old files...")

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
        _print("[OK] Cleanup done.")
    except Exception as e:
        name = type(e).__name__
        if name == "TimeoutError":
            _print(f"[X] Cleanup timed out after {_CLEANUP_TIMEOUT_S}s")
        else:
            _print(f"[X] Cleanup error: {name}: {e}")
    finally:
        if loop is not None:
            try:
                loop.close()
            except Exception:
                pass


def health() -> None:
    """وضعیت سلامت + متریک‌ها."""
    _print("[..] Health & metrics")
    port = os.getenv("OBS_PORT", "8787")
    _print("  Endpoints (while bot is running):")
    _print(f"    http://localhost:{port}/health   (JSON health)")
    _print(f"    http://localhost:{port}/metrics  (Prometheus)")

    try:
        import psutil as _ps
        disk_path = "C:\\" if os.name == "nt" else "/"
        _print(
            f"  CPU: {_ps.cpu_percent(interval=0.3)}%  |  "
            f"RAM: {_ps.virtual_memory().percent}%  |  "
            f"Disk: {_ps.disk_usage(disk_path).percent}%"
        )
    except Exception:
        pass

    status()


def ai_status() -> None:
    """وضعیت AI (Ollama) + adminها."""
    _print("[..] AI assistant (Ollama) & admins")
    import urllib.request

    try:
        from config import AI, PLUGINS_CONFIG
    except Exception as e:
        _print(f"[X] config error: {type(e).__name__}: {e}")
        return

    try:
        url = AI["url"].rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=4) as r:
            tags = json.loads(r.read().decode("utf-8"))
        models = [m.get("name") for m in tags.get("models", [])]
        _print(
            f"  Ollama: UP at {AI['url']}  |  "
            f"model in use: {AI['model']}"
        )
        _print(f"  installed models: {', '.join(models) or 'none'}")
    except Exception as e:
        _print(
            f"  Ollama: DOWN ({type(e).__name__}: {e}) — "
            f"launch Ollama or check OLLAMA_URL"
        )

    ids = PLUGINS_CONFIG.get("adminhandler", {}).get(
        "admin_ids", []
    )
    if ids:
        _print(f"  Admins (ADMIN_IDS): {', '.join(ids)}")
    else:
        _print(
            "  Admins: NONE — DM the bot on Bale; it logs your "
            "id (pairing) → put it in ADMIN_IDS"
        )


def open_chat() -> None:
    """باز کردن چت AI لوکال در مرورگر."""
    port = os.getenv("OBS_PORT", "8787")
    url = f"http://localhost:{port}/chat"
    _print(
        "[..] Local AI chat (works offline, no internet/Bale needed):"
    )
    _print(f"     {url}")
    _print("     (ربات باید روشن باشد — گزینهٔ ۱)")
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


def requests_and_errors() -> None:
    """نمایش درخواست‌های قابلیت + خطاهای اخیر."""
    _print("[..] Feature requests & recent errors")

    if os.path.exists(REQUESTS_FILE):
        try:
            with open(REQUESTS_FILE, encoding="utf-8") as f:
                reqs = [
                    json.loads(line) for line in f if line.strip()
                ]
            _print(f"  feature requests logged: {len(reqs)}")
            for r in reqs[-5:]:
                _print(
                    "   - "
                    + str(r.get("request", ""))[:_LOG_PREVIEW_LEN]
                )
        except Exception as e:
            _print(f"  [X] could not read requests: {e}")
    else:
        _print("  feature requests: none")

    errs = 0
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                lines = f.readlines()[-500:]
            for line in lines:
                try:
                    if json.loads(line).get("level") in (
                        "ERROR", "CRITICAL"
                    ):
                        errs += 1
                except Exception:
                    pass
        except Exception as e:
            _print(f"  [X] could not read log: {e}")
    _print(f"  recent errors in log: {errs}")
    _print(
        "  (self-monitor auto-alerts admins on Bale "
        "while the bot runs)"
    )


# ══════════════════════════════════════════════════════════════════════
#  Git helpers
# ══════════════════════════════════════════════════════════════════════
def _git(args: list[str]) -> tuple[int, str]:
    """اجرای دستور git در ریشهٔ پروژه.

    🟠 FIX: encoding="utf-8" + errors="replace" — خروجی فارسی git روی
    Windows دیگر خراب نمی‌شود.

    Returns:
        (returncode, stdout+stderr)
    """
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return 127, "git is not installed"
    except subprocess.TimeoutExpired:
        return 124, "git timed out"
    except Exception as e:
        return 1, f"{type(e).__name__}: {e}"


def _git_branch() -> str:
    """نام شاخهٔ فعلی (پیش‌فرض: main)."""
    rc, out = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    if rc != 0:
        return "main"
    return (out or "main").strip() or "main"


def _git_has_remote(name: str = "origin") -> bool:
    """آیا remote با این نام وجود دارد؟"""
    rc, out = _git(["remote"])
    return rc == 0 and name in (out or "")


def _git_is_repo() -> bool:
    """آیا در یک مخزن گیت هستیم؟"""
    rc, _ = _git(["rev-parse", "--is-inside-work-tree"])
    return rc == 0


def _git_ahead_behind(branch: str) -> tuple[int, int]:
    """تعداد commitهای (ahead, behind) نسبت به origin/<branch>."""
    rc, behind = _git([
        "rev-list", "--count", f"HEAD..origin/{branch}"
    ])
    try:
        behind_n = int((behind or "0").strip() or "0")
    except ValueError:
        behind_n = 0

    rc, ahead = _git([
        "rev-list", "--count", f"origin/{branch}..HEAD"
    ])
    try:
        ahead_n = int((ahead or "0").strip() or "0")
    except ValueError:
        ahead_n = 0

    return ahead_n, behind_n


def _git_deleted_tracked_files() -> list[str]:
    """فایل‌های trackشده‌ای که از working directory پاک شده‌اند.

    مقایسهٔ HEAD با working tree — هر دو حالت staged و unstaged.

    Returns:
        لیست نام فایل‌ها (نسبی به ROOT).
    """
    rc, out = _git([
        "diff", "--name-only", "--diff-filter=D", "HEAD"
    ])
    if rc != 0:
        return []
    return [
        f.strip() for f in (out or "").splitlines() if f.strip()
    ]


def _git_restore_files(files: list[str]) -> int:
    """برگرداندن فایل‌های پاک‌شده از HEAD به working directory.

    سه لایهٔ fallback:
      ۱) git restore (Git 2.23+)
      ۲) git checkout HEAD -- (قدیمی)
      ۳) تکتک (اگر دسته‌ای خطا داد)

    Returns:
        تعداد فایل‌های برگردانده‌شده.
    """
    if not files:
        return 0

    # ─── لایهٔ ۱: git restore ───
    rc, _ = _git(["restore"] + files)
    if rc == 0:
        return len(files)

    # ─── لایهٔ ۲: git checkout ───
    rc, _ = _git(["checkout", "HEAD", "--"] + files)
    if rc == 0:
        return len(files)

    # ─── لایهٔ ۳: تکتک ───
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


def _git_print_status_lines(
    status_out: str, limit: int = _GIT_PREVIEW_LIMIT
) -> None:
    """نمایش خطوط status --short با بریدن امن."""
    lines = [ln for ln in (status_out or "").strip().splitlines() if ln]
    for line in lines[:limit]:
        _print(f"    {line}")
    if len(lines) > limit:
        _print(f"    ... و {len(lines) - limit} خط دیگر")


# ══════════════════════════════════════════════════════════════════════
#  Git — Push
# ══════════════════════════════════════════════════════════════════════
def github_push() -> None:
    """Stage, commit and push to GitHub.

    امنیت: اگر .env توسط git track شده باشد، abort می‌کند.
    """
    _print("[..] Push to GitHub")

    # ─── امنیت: هرگز secrets push نشود ───
    rc, tracked = _git(["ls-files", ".env"])
    if ".env" in (tracked or ""):
        _print(
            "[X] ABORT: .env is tracked by git — pushing would "
            "leak secrets. Run: git rm --cached .env"
        )
        return

    if not _git_is_repo():
        _print("[X] Not a git repository.")
        return

    if not _git_has_remote("origin"):
        _print("[X] No 'origin' remote. Add it once:")
        _print(
            "    git remote add origin "
            "https://github.com/<you>/<repo>.git"
        )
        return

    # ─── preview تغییرات ───
    rc, status_out = _git(["status", "--short"])
    if status_out.strip():
        _print("  تغییرات pending:")
        _git_print_status_lines(status_out, _GIT_HARD_LIMIT)
    else:
        _print("  (بدون تغییر — فقط push حالت فعلی)")

    # ─── تأیید ───
    if not _confirm("\n  ادامه؟ [y/N]: "):
        _print("[i] لغو شد.")
        return

    # ─── پیام commit ───
    msg = _ask_input("Commit message (Enter = auto): ")
    if msg is None:
        _print("[i] لغو شد.")
        return
    msg = msg.strip()
    if not msg:
        msg = (
            "Update via panel "
            + datetime.now().strftime("%Y-%m-%d %H:%M")
        )

    _git(["add", "-A"])
    rc, out = _git(["commit", "-m", msg])
    if rc != 0 and "nothing to commit" in out.lower():
        _print("[i] Nothing new to commit; pushing current state...")
    elif rc != 0:
        _print(f"[X] Commit failed:\n{out}")
        return
    else:
        _print("[OK] Committed.")

    branch = _git_branch()
    rc, out = _git(["push", "-u", "origin", branch])
    if rc == 0:
        _print(f"[OK] Pushed to origin/{branch}.")
    else:
        _print(
            f"[X] Push to origin failed (check internet/credentials):\n{out}"
        )

    # ── پوش به رپوی دوم (backup) ──
    if _git_has_remote("backup"):
        rc2, out2 = _git(["push", "-u", "backup", branch])
        if rc2 == 0:
            _print(f"[OK] Pushed to backup/{branch}.")
        else:
            _print(
                f"[X] Push to backup failed:\n{out2}"
            )


# ══════════════════════════════════════════════════════════════════════
#  Git — Pull (با بازیابی فایل‌های پاک‌شده)
# ══════════════════════════════════════════════════════════════════════
def github_pull() -> None:
    """Pull از GitHub.

    ⭐ NEW: قبل از pull، فایل‌های trackشده‌ای که از working directory
    پاک شده‌اند را (با تأیید کاربر) برمی‌گرداند. دلیل: git pull فقط
    commitهای remote را می‌آورد — نه فایل‌های working directory.

    محافظ: اگر تغییرات uncommitted یا commitهای محلی جلوتر بود،
    هشدار می‌دهد و گزینهٔ stash ارائه می‌کند.
    """
    _print("[..] Pull from GitHub")

    if not _git_is_repo():
        _print("[X] Not a git repository.")
        return

    if not _git_has_remote("origin"):
        _print("[X] No 'origin' remote. Add it once:")
        _print(
            "    git remote add origin "
            "https://github.com/<you>/<repo>.git"
        )
        return

    branch = _git_branch()

    # ─── ۱) بازیابی فایل‌های پاک‌شده (قبل از pull) ───
    deleted = _git_deleted_tracked_files()
    if deleted:
        _print(
            f"\n  ⚠️ {len(deleted)} فایل trackشده که از دیسک "
            f"پاک شده‌اند:"
        )
        for f in deleted[:_GIT_PREVIEW_LIMIT]:
            _print(f"    D  {f}")
        if len(deleted) > _GIT_PREVIEW_LIMIT:
            _print(
                f"    ... و {len(deleted) - _GIT_PREVIEW_LIMIT} "
                f"فایل دیگر"
            )

        if _confirm("\n  برگردانم؟ [y/N]: "):
            n = _git_restore_files(deleted)
            if n:
                _print(f"[OK] {n} فایل برگشت.")
            else:
                _print("[X] بازیابی ناموفق (شاید مشکل permission).")
        else:
            _print("[i] بازیابی رد شد.")

    # ─── ۲) fetch ───
    _print(f"\n  [..] Fetching origin/{branch}...")
    rc, out = _git(["fetch", "origin", branch])
    if rc != 0:
        _print(f"[X] Fetch failed:\n{out}")
        return

    # ─── ۳) ahead / behind ───
    ahead_n, behind_n = _git_ahead_behind(branch)
    _print(f"  وضعیت: {ahead_n} commit جلوتر، {behind_n} commit عقب‌تر")

    if behind_n == 0:
        _print("[i] چیز جدیدی برای pull نیست (local و remote یکسان).")
        return

    # ─── ۴) تغییرات uncommitted ───
    rc, status_out = _git(["status", "--short"])
    has_local_changes = bool(status_out.strip())

    if has_local_changes:
        _print("\n  ⚠️ تغییرات محلی uncommitted:")
        _git_print_status_lines(status_out, _GIT_PREVIEW_LIMIT)

    if ahead_n > 0:
        _print(
            f"\n  ⚠️ شما {ahead_n} commit محلی دارید که در GitHub نیست."
        )
        _print("     Pull ممکن است باعث merge یا conflict شود.")

    # ─── ۵) تأیید pull ───
    if not _confirm(
        f"\n  Pull {behind_n} commit از origin/{branch}؟ [y/N]: "
    ):
        _print("[i] لغو شد.")
        return

    # ─── ۶) stash اختیاری ───
    stashed = False
    if has_local_changes and _confirm(
        "  تغییرات محلی را موقتاً stash کنم؟ [y/N]: "
    ):
        rc, out = _git([
            "stash", "push", "-u", "-m",
            f"panel-pull-{datetime.now():%Y%m%d-%H%M}",
        ])
        if rc == 0:
            stashed = True
            _print("[OK] تغییرات محلی stash شد.")
        else:
            _print(f"[X] Stash failed:\n{out}")
            return

    # ─── ۷) pull ───
    _print(f"  [..] Pulling origin/{branch}...")
    rc, out = _git(["pull", "--no-rebase", "origin", branch])
    if rc == 0:
        _print(f"[OK] Pull موفق — {behind_n} commit به‌روز شد.")
        if stashed:
            _print(
                "  💡 تغییرات شما در stash است. "
                "برای برگرداندن: git stash pop"
            )
    else:
        _print(f"[X] Pull failed:\n{out}")
        if stashed:
            _print(
                "  💡 تغییرات شما در stash است. "
                "برای برگرداندن: git stash pop"
            )


# ══════════════════════════════════════════════════════════════════════
#  Git — Status
# ══════════════════════════════════════════════════════════════════════
def github_status() -> None:
    """نمایش وضعیت کامل گیت.

    شامل:
      - branch فعلی
      - remoteها
      - آخرین commit
      - ahead/behind نسبت به origin
      - تغییرات uncommitted + خلاصه بر اساس نوع (D/M/A)
    """
    _print("[..] Git status")

    if not _git_is_repo():
        _print("[X] Not a git repository.")
        return

    # ─── ۱) شاخه ───
    branch = _git_branch()
    _print(f"\n  🌿 Branch: {branch}")

    # ─── ۲) remote ───
    rc, remotes = _git(["remote", "-v"])
    if (remotes or "").strip():
        for line in remotes.strip().splitlines()[:4]:
            _print(f"  🔗 {line.strip()}")
    else:
        _print("  🔗 Remote: (none)")

    # ─── ۳) آخرین commit ───
    rc, last = _git(["log", "-1", "--pretty=%h  %s  (%cr)"])
    if (last or "").strip():
        _print(f"  📌 Last commit: {last.strip()}")

    # ─── ۴) fetch + ahead/behind ───
    if _git_has_remote("origin"):
        _print(f"\n  [..] Fetching origin/{branch}...")
        rc, _ = _git(["fetch", "origin", branch])
        if rc == 0:
            ahead_n, behind_n = _git_ahead_behind(branch)
            icon = (
                "✅" if (ahead_n == 0 and behind_n == 0) else "⚠️"
            )
            _print(
                f"  {icon} {ahead_n} ahead، {behind_n} "
                f"behind origin/{branch}"
            )
        else:
            _print("  [!] Fetch ناموفق (اینترنت/اعتبارسنجی؟)")

    # ─── ۵) تغییرات uncommitted ───
    rc, status_out = _git(["status", "--short"])
    if not status_out.strip():
        _print(
            "\n  ✅ Working tree clean "
            "(هیچ تغییر uncommitted نیست)."
        )
        return

    lines = [
        ln for ln in status_out.strip().splitlines() if ln
    ]
    _print(f"\n  📝 تغییرات uncommitted ({len(lines)} فایل):")
    _git_print_status_lines(status_out, _GIT_HARD_LIMIT)

    # ─── ۶) خلاصه بر اساس نوع ───
    deleted: list[str] = []
    modified = 0
    added = 0

    for line in lines:
        if len(line) < 4:
            continue
        xy = line[:2]
        path = line[3:].strip()
        if "D" in xy:
            deleted.append(path)
        elif "?" in xy or "A" in xy:
            added += 1
        elif "M" in xy or "R" in xy:
            modified += 1

    _print("\n  خلاصه:")
    if deleted:
        _print(f"    ❌ {len(deleted)} فایل پاک‌شده (trackشده)")
        for f in deleted[:5]:
            _print(f"        D  {f}")
        if len(deleted) > 5:
            _print(f"        ... و {len(deleted) - 5} فایل دیگر")
        _print("    💡 برای بازیابی: گزینهٔ ۱۷ (Pull from GitHub)")
    if modified:
        _print(f"    📝 {modified} فایل تغییرکرده")
    if added:
        _print(f"    ➕ {added} فایل جدید")


# ══════════════════════════════════════════════════════════════════════
#  منو
# ══════════════════════════════════════════════════════════════════════
MENU: list[tuple[str, str, Callable[[], None]]] = [
    ("1", "Start the bot", start),
    ("2", "Restart", restart),
    ("3", "Stop", stop),
    ("4", "Status & stats", status),
    ("5", "Login to platforms", login),
    ("6", "Backup database", backup),
    ("7", "Generate reports (platform)", report),
    ("8", "Export CRM (platform/split/sheet)", crm),
    ("9", "Run tests", tests),
    ("10", "Cleanup old files", cleanup),
    ("11", "Push to GitHub", github_push),
    ("12", "Health & metrics", health),
    ("13", "AI / Admin status", ai_status),
    ("14", "Feature requests & errors", requests_and_errors),
    ("15", "Open local AI chat (offline)", open_chat),
    ("16", "Recent reports & CRM files", recent_reports),
    ("17", "Pull from GitHub (restore + pull)", github_pull),
    ("18", "Git status (branch/changes)", github_status),
]


def show_menu() -> None:
    """نمایش منو — شماره‌های دو رقمی راست‌چین."""
    _print("\n" + "=" * 55)
    _print("   Srooyesh Lead-Monitor  |  Control Panel")
    _print("=" * 55)
    for num, label, _ in MENU:
        _print(f"  [{num:>2}] {label}")
    _print("  [ 0] Exit")
    _print("-" * 55)


def _cleanup_on_exit() -> None:
    """atexit: فقط برای رعایت اصول (ربات مستقل است)."""
    pass


def main() -> None:
    """حلقهٔ اصلی منو."""
    atexit.register(_cleanup_on_exit)
    actions = {num: fn for num, _, fn in MENU}

    while True:
        show_menu()

        choice = _ask_input("Enter a number: ")
        if choice is None:
            _print("\nBye.")
            break
        choice = choice.strip()

        if choice == "0":
            _print("Bye.")
            break

        fn = actions.get(choice)
        if not fn:
            _print("[X] Invalid number.")
            continue

        try:
            fn()
        except KeyboardInterrupt:
            _print("\n[i] لغو شد.")
            continue
        except Exception as e:
            _print(f"[X] Error: {type(e).__name__}: {e}")

        if os.name == "nt":
            try:
                os.system("pause >nul")
            except Exception:
                pass


if __name__ == "__main__":
    main()