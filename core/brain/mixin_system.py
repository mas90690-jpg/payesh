"""ابزارهای سیستم — git, command, processes, tests, python."""
from __future__ import annotations

import ast
import asyncio
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

from core.adapters.access_manager import get_access_manager
from core.brain.constants import (
    BANDIT_TIMEOUT_S,
    DEFAULT_TIMEOUT_S,
    LOG_PREVIEW_LEN,
    MAX_CONTENT_LENGTH,
    PROJECT_BASE,
    PY_EXEC_TIMEOUT_S,
    PYTEST_COVERAGE_TIMEOUT_S,
    TESTS_TIMEOUT_S,
)
from core.brain.utils import (
    SAFE_PREAMBLE,
    _load_access,
    human_bytes,
    sanitize_console_output,
)
from core.kernel.logger import log_error, log_success, log_warning

# ─── الگوهای shell که در دستور ممنوع هستند ───
# هر یک از این‌ها می‌تواند برای زنجیره‌کردن دستور استفاده شود
_SHELL_METACHAR_REGEX = (
    r"(?:[;&|`$<>]|\|\||&&|>>|<<|\$\()"
)

# کاراکترهای کنترلی که در دستور مجاز نیستند
_FORBIDDEN_CHARS = ("\n", "\r", "\x00", "\x1a", "\x1b")


class SystemMixin:
    """متدهای سیستم — به Brain اضافه می‌شود."""

    # ─── Git ───
    async def _git_status(self) -> str:
        try:
            r = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, cwd=PROJECT_BASE,
            )
            return r.stdout or "✅ Working tree clean"
        except Exception as e:
            log_error(f"❌ [BRAIN:git] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    async def _git_diff(self, path: str = "") -> str:
        try:
            cmd = ["git", "diff"] + (["--", path] if path else [])
            r = subprocess.run(
                cmd, capture_output=True, text=True, cwd=PROJECT_BASE
            )
            return r.stdout[:MAX_CONTENT_LENGTH] or "✅ بدون تغییر"
        except Exception as e:
            log_error(f"❌ [BRAIN:git] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    async def _git_commit(self, message: str) -> str:
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"
        try:
            subprocess.run(
                ["git", "add", "."],
                cwd=PROJECT_BASE,
                check=True,
                capture_output=True,
            )
            r = subprocess.run(
                ["git", "commit", "-m", message],
                capture_output=True, text=True, cwd=PROJECT_BASE,
            )
            log_success(
                f"✅ [BRAIN:git:commit] "
                f"{message[:LOG_PREVIEW_LEN]!r}"
            )
            return r.stdout[:2000] or "✅ Commit انجام شد."
        except Exception as e:
            log_error(f"❌ [BRAIN:git] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    async def _git_log(self, n: int = 10) -> str:
        try:
            r = subprocess.run(
                ["git", "log", f"-{n}", "--oneline", "--decorate"],
                capture_output=True, text=True, cwd=PROJECT_BASE,
            )
            return r.stdout or "📭 هیچ کامیتی یافت نشد."
        except Exception as e:
            log_error(f"❌ [BRAIN:git] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    async def _git_branch(
        self, name: str, action: str = "create"
    ) -> str:
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"
        try:
            cmd = (
                ["git", "checkout", "-b", name]
                if action == "create"
                else ["git", "checkout", name]
            )
            r = subprocess.run(
                cmd, capture_output=True, text=True, cwd=PROJECT_BASE
            )
            return r.stdout[:2000] or f"✅ برنچ {name}"
        except Exception as e:
            log_error(f"❌ [BRAIN:git] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════════════════════
    # Execute command — SECURITY FIXED
    # ═══════════════════════════════════════════════════════
    #
    # 🐛 FIX بحرانی: قبلاً این متد از `shell=True` استفاده می‌کرد و
    # فقط با `startswith` چک می‌کرد. یعنی اگه «git status» مجاز بود،
    # دستور «git status; rm -rf /» هم قبول می‌شد → خطرناک.
    #
    # حالا:
    #   ۱) کاراکترهای کنترلی چک می‌شوند (\n، \r، \x00، ...)
    #   ۲) shell metacharacters چک می‌شوند (;، &، |، `، $، <، >، ...)
    #   ۳) دستور با shlex.split به توکن‌ها تقسیم می‌شود
    #   ۴) با shell=False اجرا می‌شود (بدون shell)
    #   ۵) هر توکن جداگانه در برابر deny-list چک می‌شود
    #   ۶) اولین توکن باید در whitelist باشد
    # ═══════════════════════════════════════════════════════
    async def _execute_command(self, command: str) -> str:
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"

        # ─── اعتبارسنجی اولیه ───
        if not command or not isinstance(command, str):
            return "⛔ دستور خالی است."

        cmd = command.strip()
        if not cmd:
            return "⛔ دستور خالی است."

        # ۱) کاراکترهای کنترلی (newline، tab، null، escape)
        for ch in _FORBIDDEN_CHARS:
            if ch in cmd:
                log_warning(
                    f"⛔ [BRAIN:exec] کاراکتر کنترلی | "
                    f"cmd={cmd[:60]!r}"
                )
                return "⛔ دستور شامل کاراکتر غیرمجاز است."

        # ۲) shell metacharacters — جلوگیری از زنجیره‌کردن دستور
        import re as _re
        if _re.search(_SHELL_METACHAR_REGEX, cmd):
            log_warning(
                f"⛔ [BRAIN:exec] متاکاراکتر shell | "
                f"cmd={cmd[:60]!r}"
            )
            return (
                "⛔ دستور شامل کاراکتر ویژهٔ shell است "
                "(; & | ` $ < > ...)."
            )

        # ۳) تجزیه با shlex (بدون shell)
        try:
            if os.name == "nt":
                # Windows: shlex با posix=False سازگار است
                tokens = shlex.split(cmd, posix=False)
            else:
                tokens = shlex.split(cmd, posix=True)
        except ValueError as e:
            log_warning(
                f"⛔ [BRAIN:exec] shlex ناموفق | "
                f"{type(e).__name__}: {e} | cmd={cmd[:60]!r}"
            )
            return f"⛔ دستور نامعتبر: {e}"

        if not tokens:
            return "⛔ دستور خالی است."

        # ۴) بررسی AccessManager روی دستور اصلی
        ok, reason = get_access_manager().can_run_command(cmd)
        if not ok:
            return f"⛔ دستور ممنوع: {reason}"

        # ۵) deny-list روی هر توکن (نه فقط شروع)
        access = _load_access()
        deny_list = access.get("execute", {}).get("deny", []) or []
        cmd_lower = cmd.lower()
        for denied in deny_list:
            d = str(denied).strip().lower()
            if not d:
                continue
            # اگه deny یک دستور کامل است، فقط در شروع چک کن
            # اگه فقط یک کلمه است، در همهٔ توکن‌ها چک کن
            if " " in d:
                if cmd_lower.startswith(d):
                    log_warning(
                        f"⛔ [BRAIN:exec] deny-prefix | "
                        f"cmd={cmd[:60]!r} ← {d!r}"
                    )
                    return f"⛔ دستور ممنوعه: {denied}"
            else:
                # توکن تکی — در همهٔ توکن‌ها جستجو کن
                for tok in tokens:
                    if tok.lower() == d:
                        log_warning(
                            f"⛔ [BRAIN:exec] deny-token | "
                            f"cmd={cmd[:60]!r} ← {d!r}"
                        )
                        return f"⛔ دستور ممنوعه: {denied}"

        # ۶) whitelist: اولین توکن باید با یکی از allowها شروع شود
        allow_list = access.get("execute", {}).get("allow", []) or []
        if allow_list:
            first_token = tokens[0].lower()
            matched = False
            for a in allow_list:
                a_low = str(a).strip().lower()
                if not a_low:
                    continue
                # allow می‌تونه یک دستور کامل باشه یا فقط اسم برنامه
                a_tokens = a_low.split()
                if not a_tokens:
                    continue
                # مقایسه توکن‌به‌توکن
                if len(a_tokens) == 1:
                    if first_token == a_tokens[0]:
                        matched = True
                        break
                else:
                    # allow چندکلمه‌ای — باید همه توکن‌ها منطبق باشند
                    if tokens[:len(a_tokens)] == a_tokens:
                        matched = True
                        break
            if not matched:
                log_warning(
                    f"⛔ [BRAIN:exec] در whitelist نیست | "
                    f"first={first_token!r} cmd={cmd[:60]!r}"
                )
                return (
                    f"⛔ دستور '{cmd[:40]}' در whitelist نیست."
                )

        # ۷) اجرا — shell=False (امن)
        try:
            timeout = access.get("safety", {}).get(
                "max_command_timeout_sec", DEFAULT_TIMEOUT_S
            )
            try:
                timeout = int(timeout)
            except (TypeError, ValueError):
                timeout = int(DEFAULT_TIMEOUT_S)

            r = subprocess.run(
                tokens,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
                cwd=PROJECT_BASE,
            )
            out = (r.stdout or "") + (r.stderr or "")
            out = sanitize_console_output(out)
            return out[:MAX_CONTENT_LENGTH] or "(اجرا شد)"
        except subprocess.TimeoutExpired:
            return "⏰ زمان اجرا به پایان رسید."
        except FileNotFoundError as e:
            log_warning(
                f"⚠️ [BRAIN:exec] دستور پیدا نشد | {e}"
            )
            return f"⛔ دستور پیدا نشد: {tokens[0]}"
        except Exception as e:
            log_error(f"❌ [BRAIN:exec] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    # ─── System info / processes / ping ───
    async def _get_system_info(self) -> str:
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.3)
            ram = psutil.virtual_memory()
            disk_path = "C:\\" if os.name == "nt" else "/"
            disk = psutil.disk_usage(disk_path)
            boot = datetime.fromtimestamp(psutil.boot_time())
            return (
                f"🔹 CPU: {cpu}%\n"
                f"🔹 RAM: {ram.percent}%\n"
                f"🔹 دیسک: {disk.percent}%\n"
                f"🔹 پردازه‌ها: {len(psutil.pids())}\n"
                f"🔹 روشن از: {boot.strftime('%Y-%m-%d %H:%M')}\n"
                f"🔹 سیستم: {platform.system()} {platform.release()}"
            )
        except ImportError:
            return "⚠️ psutil نصب نیست."
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _list_processes(self, limit: int = 20) -> str:
        try:
            import psutil
            procs = []
            for p in psutil.process_iter(
                ["pid", "name", "cpu_percent", "memory_percent"]
            ):
                try:
                    procs.append(p.info)
                except Exception:
                    continue
            procs.sort(
                key=lambda x: x.get("cpu_percent", 0), reverse=True
            )
            lines = [
                f"{p['pid']:>6}  {p['name'][:25]:<25} "
                f"CPU:{p.get('cpu_percent',0):>5.1f}%"
                for p in procs[:limit]
            ]
            return "\n".join(lines) or "📭 پردازه‌ای نیست."
        except ImportError:
            return "⚠️ psutil نصب نیست."
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _kill_process(self, pid: int) -> str:
        """
        کشتن پردازه با PID.

        🐛 FIX: قبلاً `time.sleep(1)` استفاده می‌کرد که کل event loop را
        ۱ ثانیه قفل می‌کرد. حالا `await asyncio.sleep(1)` است.
        """
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"
        try:
            import psutil
            p = psutil.Process(pid)
            name = p.name()
            p.terminate()
            await asyncio.sleep(1)   # ← FIX: بلاک نمی‌کند
            if p.is_running():
                p.kill()
            return f"✅ پردازه {pid} ({name}) کشته شد."
        except ImportError:
            return "⚠️ psutil نصب نیست."
        except Exception as e:
            log_error(f"❌ [BRAIN:kill_process] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    async def _ping_host(self, host: str) -> str:
        try:
            param = "-n" if os.name == "nt" else "-c"
            r = subprocess.run(
                ["ping", param, "2", host],
                capture_output=True, text=True, timeout=10,
            )
            return r.stdout[:3000] or f"✅ {host} پاسخ داد."
        except Exception as e:
            return f"❌ خطا: {e}"

    # ─── Tests ───
    async def _run_tests(self, path: str = "") -> str:
        try:
            cmd = (
                f"python -m pytest {path} -v --tb=short -x"
                if path
                else "python -m pytest -v --tb=short -x"
            )
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=TESTS_TIMEOUT_S, cwd=PROJECT_BASE,
            )
            return (
                r.stdout[:4000]
                or r.stderr[:4000]
                or "✅ تست‌ها اجرا شدند."
            )
        except subprocess.TimeoutExpired:
            return "⏰ زمان تست به پایان رسید."
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _run_tests_with_coverage(self, path: str) -> str:
        try:
            cmd = f"pytest {path} --cov=. --cov-report=html"
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=PYTEST_COVERAGE_TIMEOUT_S, cwd=PROJECT_BASE,
            )
            return (
                f"✅ گزارش پوشش در htmlcov/ ذخیره شد.\n"
                f"{r.stdout[:4000]}"
            )
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _analyze_security(self, path: str) -> str:
        try:
            r = subprocess.run(
                ["bandit", "-r", path, "-f", "txt"],
                capture_output=True, text=True,
                timeout=BANDIT_TIMEOUT_S,
            )
            return (
                r.stdout
                or "✅ آسیب‌پذیری یافت نشد."
            )
        except FileNotFoundError:
            return "⚠️ bandit نصب نیست."
        except subprocess.TimeoutExpired:
            return "⏰ زمان بررسی به پایان رسید."
        except Exception as e:
            return f"❌ خطا: {e}"

    # ─── Backup / execute_python / analyze_code ───
    async def _backup_project(
        self, destination: str = None
    ) -> str:
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"
        if not destination:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            destination = f"Project_Backup_{ts}.zip"
        try:
            dest_path = self._safepath(destination)
            shutil.make_archive(
                str(dest_path).replace(".zip", ""),
                "zip",
                PROJECT_BASE,
            )
            size = (
                os.path.getsize(dest_path)
                if dest_path.exists() else 0
            )
            return (
                f"✅ بکاپ در {dest_path} ذخیره شد "
                f"({human_bytes(size)})."
            )
        except Exception as e:
            return f"❌ خطا: {e}"

    async def _execute_python(self, code: str) -> str:
        """
        اجرای کد پایتون در sandbox.

        🐛 FIX: regex «pathlib» و «open» حذف شدن چون:
          - SAFE_PREAMBLE خودش از pathlib استفاده می‌کنه
          - کاربر با محدودیت open دیگه نمیتونه فایل بخونه (که مجازه)
        """
        import re
        dangerous_patterns = [
            # سیستم — ممنوع
            r"\bos\.(system|popen|fork|exec|spawn)\b",
            r"\bsubprocess\b",
            r"\bpty\.spawn\b",
            r"\bmultiprocessing\b",
            r"\b__import__\b",
            r"\beval\b",
            r"\bexec\b",
            r"\bcompile\b",
            # shutil خطرناک (بجز disk_usage که بی‌خطره)
            r"\bshutil\.(rmtree|remove|move|copy|copy2|copyfile)\b",
            # نوشتن روی فایل‌های اصلی (SAFE_PREAMBLE خودش محافظت می‌کنه)
            # ⛔ حذف شد: \bopen\s*\( , \bpathlib\b
            # چون SAFE_PREAMBLE خودش از open و pathlib استفاده میکنه
            # و کاربر مجازه بخونه (فقط نوشتن محدوده)
            r"\bio\.open\b",
            r"\bimportlib\b",
            r"\bsys\.modules\b",
            r"\bsocket\b",
            r"\brequests\b",
            r"\burllib\b",
            r"\bhttpx\b",
            r"\bos\.putenv\b",
            # نوشتن مستقیم به pathlib (SAFE_PREAMBLE wrap میکنه)
            # ⛔ حذف شد: \.write_text\b , \.write_bytes\b , \.touch\b , \.unlink\b
            # چون SAFE_PREAMBLE خودش این‌ها رو wrap کرده
        ]
        for pat in dangerous_patterns:
            if re.search(pat, code, re.IGNORECASE):
                log_warning(
                    f"⛔ [BRAIN:py] الگوی ممنوعه | {pat}"
                )
                return f"⛔ کد شامل عملیات خطرناک: {pat}"

        full_code = SAFE_PREAMBLE + "\n" + code

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(full_code)
            tmp = f.name
        try:
            r = subprocess.run(
                [sys.executable, tmp],
                capture_output=True, text=True,
                timeout=PY_EXEC_TIMEOUT_S,
                cwd=PROJECT_BASE,
            )
            out = (r.stdout or "") + (r.stderr or "")
            out = sanitize_console_output(out)
            return out[:MAX_CONTENT_LENGTH] or "(بدون خروجی)"
        except subprocess.TimeoutExpired:
            return "⏰ زمان اجرا تمام شد."
        except Exception as e:
            log_error(
                f"❌ [BRAIN:py] {type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    async def _analyze_code(self, path: str) -> str:
        p = self._safepath(path)
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
            funcs = [
                n.name for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)
            ]
            classes = [
                n.name for n in ast.walk(tree)
                if isinstance(n, ast.ClassDef)
            ]
            imports = [
                n.names[0].name for n in ast.walk(tree)
                if isinstance(n, ast.Import)
            ]
            imports_from = []
            for n in ast.walk(tree):
                if isinstance(n, ast.ImportFrom) and n.module:
                    imports_from.append(n.module)
            return (
                f"📊 تحلیل {path}:\n"
                f"🔹 توابع ({len(funcs)}): "
                f"{', '.join(funcs[:15]) or '—'}\n"
                f"🔹 کلاس‌ها ({len(classes)}): "
                f"{', '.join(classes[:10]) or '—'}\n"
                f"🔹 ایمپورت‌ها: "
                f"{len(imports) + len(imports_from)}"
            )
        except SyntaxError as e:
            return f"❌ خطای نحوی: {e}"
        except Exception as e:
            return f"❌ خطا: {e}"
