# -*- coding: utf-8 -*-
"""
Control panel for the Srooyesh lead-monitoring bot — numeric menu.

Run: double-click panel.bat  (or: python panel.py)

IMPORTANT (project rule): EVERY capability of this project must be reachable
from this panel. When a new feature/script is added, also add a menu entry here.

Each action has a number:
    1) Start    2) Restart   3) Stop        4) Status & stats
    5) Login    6) Backup    7) Reports     8) Export CRM (CSV/Excel)
    9) Tests    10) Cleanup  11) Push to GitHub      0) Exit

"Start" launches the bot in a separate window; its PID is saved to
storage/bot.pid so Stop/Restart work precisely (child browsers are killed too).
"""
import os
import sys
import time
import json
import shutil
import sqlite3
import subprocess
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
PYEXE = sys.executable or "python"
PID_FILE = os.path.join(ROOT, "storage", "bot.pid")
DB_PATH = os.path.join(ROOT, "storage", "database", "messages.db")
BACKUP_DIR = os.path.join(ROOT, "storage", "backups")

try:
    import psutil
except ImportError:
    psutil = None


# ==================================================
# Bot process management (start / stop / restart / status)
# ==================================================
def _read_pid():
    try:
        with open(PID_FILE, "r") as f:
            return int(f.read().strip())
    except Exception:
        return None


def _write_pid(pid):
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def _clear_pid():
    try:
        os.remove(PID_FILE)
    except Exception:
        pass


def _is_running():
    pid = _read_pid()
    if not pid:
        return False
    if psutil:
        try:
            p = psutil.Process(pid)
            return p.is_running() and "python" in (p.name() or "").lower()
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False


def start():
    if _is_running():
        print("[!] Bot is already running (PID:", _read_pid(), ")")
        return
    main_py = os.path.join(ROOT, "main.py")
    kwargs = {"cwd": ROOT}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([PYEXE, main_py], **kwargs)
    _write_pid(proc.pid)
    print(f"[OK] Bot started (PID: {proc.pid}). Logs in the new window/background.")


def stop():
    pid = _read_pid()
    if not _is_running():
        print("[i] Bot is not running.")
        _clear_pid()
        return
    try:
        if psutil:
            p = psutil.Process(pid)
            for child in p.children(recursive=True):   # also kill child browsers/clients
                try:
                    child.terminate()
                except Exception:
                    pass
            p.terminate()
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()
        else:
            os.kill(pid, 9)
        print("[OK] Bot stopped.")
    except Exception as e:
        print(f"[X] Error while stopping: {e}")
    _clear_pid()


def restart():
    print("[..] Restarting...")
    stop()
    time.sleep(2)
    start()


def status():
    running = _is_running()
    print("Bot status: " + ("RUNNING (PID " + str(_read_pid()) + ")" if running else "STOPPED"))
    if os.path.exists(DB_PATH):
        try:
            c = sqlite3.connect(DB_PATH)
            cur = c.cursor()
            total = cur.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            leads = cur.execute("SELECT COUNT(*) FROM messages WHERE keywords!=''").fetchone()[0]
            hot = cur.execute("SELECT COUNT(*) FROM messages WHERE opportunity_type='داغ'").fetchone()[0]
            print("--- Leads by platform ---")
            for row in cur.execute("SELECT platform, COUNT(*) FROM messages WHERE keywords!='' GROUP BY platform"):
                print(f"   {row[0]}: {row[1]}")
            print(f"[#] Total messages: {total} | Leads: {leads} | Hot: {hot}")
            c.close()
        except Exception as e:
            print(f"(could not read stats: {e})")
    else:
        print("(No database yet — run the bot once.)")


# ==================================================
# Tools (login / backup / report / crm / tests / cleanup / github)
# ==================================================
def login():
    print("[..] Platform login (interactive)...")
    subprocess.call([PYEXE, os.path.join(ROOT, "login.py")], cwd=ROOT)


def backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if not os.path.exists(DB_PATH):
        print("[!] No database to back up.")
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"manual_messages_{stamp}.db")
    shutil.copy2(DB_PATH, dest)
    print(f"[OK] Backup saved: {dest}")


def report():
    print("[..] Generating executive + detailed reports...")
    try:
        from core.daily_reporter import generate_executive_report, generate_detailed_report
        generate_executive_report()
        generate_detailed_report(limit=200)
    except Exception as e:
        print(f"[X] Report error: {e}")


def crm():
    print("[..] Exporting CRM (CSV + JSON + Excel)...")
    try:
        from core.crm_export import export_crm
        export_crm(30)
    except Exception as e:
        print(f"[X] CRM export error: {e}")


def tests():
    print("[..] Running tests...")
    subprocess.call([PYEXE, "-m", "pytest", "-q"], cwd=ROOT)


def cleanup():
    print("[..] Cleaning old files...")
    try:
        import asyncio
        from core.cleanup_service import CleanupService
        asyncio.run(CleanupService(None).run_once())
        print("[OK] Cleanup done.")
    except Exception as e:
        print(f"[X] Cleanup error: {e}")


def health():
    print("[..] Health & metrics")
    port = os.getenv("OBS_PORT", "8787")
    print(f"  Endpoints (while bot is running):")
    print(f"    http://localhost:{port}/health   (JSON health)")
    print(f"    http://localhost:{port}/metrics  (Prometheus)")
    try:
        import psutil
        print(f"  CPU: {psutil.cpu_percent(interval=0.3)}%  |  "
              f"RAM: {psutil.virtual_memory().percent}%  |  "
              f"Disk: {psutil.disk_usage('/').percent}%")
    except Exception:
        pass
    status()


def ai_status():
    print("[..] AI assistant (Ollama) & admins")
    import urllib.request
    from config import AI, PLUGINS_CONFIG
    try:
        with urllib.request.urlopen(AI["url"] + "/api/tags", timeout=4) as r:
            tags = json.loads(r.read().decode("utf-8"))
        models = [m.get("name") for m in tags.get("models", [])]
        print(f"  Ollama: UP at {AI['url']}  |  model in use: {AI['model']}")
        print(f"  installed models: {', '.join(models) or 'none'}")
    except Exception as e:
        print(f"  Ollama: DOWN ({e}) — launch Ollama or check OLLAMA_URL")
    ids = PLUGINS_CONFIG.get("adminhandler", {}).get("admin_ids", [])
    if ids:
        print(f"  Admins (ADMIN_IDS): {', '.join(ids)}")
    else:
        print("  Admins: NONE — DM the bot on Bale; it logs your id (pairing) → put it in ADMIN_IDS")


def open_chat():
    port = os.getenv("OBS_PORT", "8787")
    url = f"http://localhost:{port}/chat"
    print(f"[..] Local AI chat (works offline, no internet/Bale needed):")
    print(f"     {url}")
    print("     (ربات باید روشن باشد — گزینهٔ ۱)")
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


def requests_and_errors():
    print("[..] Feature requests & recent errors")
    rq = os.path.join(ROOT, "storage", "requests.jsonl")
    if os.path.exists(rq):
        reqs = [json.loads(l) for l in open(rq, encoding="utf-8") if l.strip()]
        print(f"  feature requests logged: {len(reqs)}")
        for r in reqs[-5:]:
            print("   - " + str(r.get("request", ""))[:80])
    else:
        print("  feature requests: none")
    lg = os.path.join(ROOT, "storage", "logs", "app.jsonl")
    errs = 0
    if os.path.exists(lg):
        for l in open(lg, encoding="utf-8").readlines()[-500:]:
            try:
                if json.loads(l).get("level") in ("ERROR", "CRITICAL"):
                    errs += 1
            except Exception:
                pass
    print(f"  recent errors in log: {errs}")
    print("  (self-monitor auto-alerts admins on Bale while the bot runs)")


def _git(args):
    """Run a git command in the project root; return (rc, stdout)."""
    try:
        r = subprocess.run(["git"] + args, cwd=ROOT, capture_output=True, text=True)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return 127, "git is not installed"


def github_push():
    """Stage, commit and push to GitHub. Safe: refuses if .env is tracked."""
    print("[..] Push to GitHub")
    # Safety guard: never push secrets
    rc, tracked = _git(["ls-files", ".env"])
    if ".env" in (tracked or ""):
        print("[X] ABORT: .env is tracked by git — pushing would leak secrets. "
              "Run: git rm --cached .env")
        return
    rc, _ = _git(["rev-parse", "--is-inside-work-tree"])
    if rc != 0:
        print("[X] Not a git repository.")
        return
    rc, remotes = _git(["remote"])
    if "origin" not in (remotes or ""):
        print("[X] No 'origin' remote. Add it once:\n"
              "    git remote add origin https://github.com/<you>/<repo>.git")
        return

    try:
        msg = input("Commit message (Enter = auto): ").strip()
    except (EOFError, KeyboardInterrupt):
        msg = ""
    if not msg:
        msg = "Update via panel " + datetime.now().strftime("%Y-%m-%d %H:%M")

    _git(["add", "-A"])
    rc, out = _git(["commit", "-m", msg])
    if rc != 0 and "nothing to commit" in out.lower():
        print("[i] Nothing new to commit; pushing current state...")
    elif rc != 0:
        print(f"[X] Commit failed:\n{out}")
        return
    else:
        print("[OK] Committed.")

    rc, branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    branch = (branch or "main").strip() or "main"
    rc, out = _git(["push", "-u", "origin", branch])
    if rc == 0:
        print(f"[OK] Pushed to origin/{branch}.")
    else:
        print(f"[X] Push failed (check internet/credentials):\n{out}")


# ==================================================
# Menu
# ==================================================
MENU = [
    ("1", "Start the bot", start),
    ("2", "Restart", restart),
    ("3", "Stop", stop),
    ("4", "Status & stats", status),
    ("5", "Login to platforms", login),
    ("6", "Backup database", backup),
    ("7", "Generate reports", report),
    ("8", "Export CRM (CSV/Excel)", crm),
    ("9", "Run tests", tests),
    ("10", "Cleanup old files", cleanup),
    ("11", "Push to GitHub", github_push),
    ("12", "Health & metrics", health),
    ("13", "AI / Admin status", ai_status),
    ("14", "Feature requests & errors", requests_and_errors),
    ("15", "Open local AI chat (offline)", open_chat),
]


def show_menu():
    print("\n" + "=" * 50)
    print("   Srooyesh Lead-Monitor  |  Control Panel")
    print("=" * 50)
    for num, label, _ in MENU:
        print(f"  [{num}] {label}")
    print("  [0] Exit")
    print("-" * 50)


def main():
    actions = {num: fn for num, _, fn in MENU}
    while True:
        show_menu()
        try:
            choice = input("Enter a number: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break
        if choice == "0":
            print("Bye.")
            break
        fn = actions.get(choice)
        if not fn:
            print("[X] Invalid number.")
            continue
        try:
            fn()
        except Exception as e:
            print(f"[X] Error: {e}")
        if os.name == "nt":
            os.system("pause >nul")


if __name__ == "__main__":
    main()
