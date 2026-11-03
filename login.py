"""
login.py — اسکریپت ورود تعاملی (یک‌بار اجرا برای هر پلتفرم).

نسخهٔ ۱۰:
  - 🎯 FIX: اینستاگرام از CDP استفاده می‌کند تا چالش reCAPTCHA را رد کند.
  - ✅ همهٔ تابع‌های مرورگری با الگوی یکسان (انتخاب Chrome واقعی).
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys
import time
from collections.abc import Awaitable, Callable

# ─── patch console قبل از هر print/import ───
from core.kernel.console_compat import patch_console

patch_console(verbose=False)


# ═══════════════════════════════════════════
# config + logger
# ═══════════════════════════════════════════
import contextlib  # noqa: E402

from config import LOGIN_INPUT_TIMEOUT_S, PLUGINS_CONFIG  # noqa: E402
from core.kernel.logger import (  # noqa: E402
    log_debug,
    log_error,
    log_info,
    log_success,
    log_warning,
)
from data import paths as data_paths  # noqa: E402

_LOGIN_PREFIX = "[LOGIN"

_INPUT_TIMEOUT_S = LOGIN_INPUT_TIMEOUT_S
_PID_FILE = str(data_paths.BOT_PID)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _is_bot_running() -> tuple[bool, int | None]:
    try:
        with open(_PID_FILE, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return False, None

    try:
        import psutil
        try:
            p = psutil.Process(pid)
            if not p.is_running():
                return False, pid
            try:
                cmdline = " ".join(p.cmdline() or []).lower()
                if "main.py" in cmdline or "python" in cmdline:
                    return True, pid
            except Exception:
                return True, pid
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return False, pid
        except psutil.AccessDenied:
            return True, pid
    except ImportError:
        try:
            os.kill(pid, 0)
            return True, pid
        except (OSError, ProcessLookupError):
            return False, pid
    return False, pid


def _warn_bot_running(platform_label: str) -> bool:
    running, pid = _is_bot_running()
    if not running:
        return True

    log_info('')
    log_info("╔" + "═" * 58 + "╗")
    log_info("║  ⚠️  ربات روشن است — پروفایل Playwright قفل است!      ║")
    log_info("╚" + "═" * 58 + "╝")
    log_info('')
    log_info(f"  PID ربات: {pid}")
    log_info('')
    log_info("  برای لاگین به مرورگر، ابتدا ربات را Stop کن:")
    log_info("     ۱) python panel.py  →  گزینه [3] Stop")
    log_info("     ۲) یا در همین ترمینال Ctrl+C بزن")
    log_info('')
    log_info("  اگر ادامه بدهی، مرورگر با خطای زیر باز نمی‌شود:")
    log_info("     Target page, context or browser has been closed")
    log_info('')

    try:
        ans = input(f"  با این حال ادامه بدهی؟ [{platform_label}] [y/N]: ")
    except (EOFError, KeyboardInterrupt):
        return False
    return ans.strip().lower() in ("y", "yes")


async def _input_async(prompt: str, timeout_s: int | None = None) -> str | None:
    if timeout_s is None:
        timeout_s = _INPUT_TIMEOUT_S
    loop = asyncio.get_running_loop()
    try:
        coro = loop.run_in_executor(None, input, prompt)
        if timeout_s and timeout_s > 0:
            return await asyncio.wait_for(coro, timeout=timeout_s)
        return await coro
    except TimeoutError:
        log_warning(f"⏰ {_LOGIN_PREFIX}:input] timeout ({timeout_s}s)")
        return None
    except (EOFError, KeyboardInterrupt) as e:
        log_warning(f"⚠️ {_LOGIN_PREFIX}:input] {type(e).__name__} — لغو")
        return None


async def _safe_disconnect_async(collector) -> None:
    if collector is None:
        return
    try:
        await collector.disconnect()
    except Exception as e:
        log_warning(f"⚠️ {_LOGIN_PREFIX}:disconnect] {type(e).__name__}: {e}")


async def _safe_disconnect_client(client, name: str = "client") -> None:
    if client is None:
        return
    try:
        disconnect = getattr(client, "disconnect", None)
        if disconnect is None:
            return
        result = disconnect()
        if asyncio.iscoroutine(result):
            await result
    except Exception as e:
        log_warning(f"⚠️ {_LOGIN_PREFIX}:{name}:disconnect] {type(e).__name__}: {e}")


async def _safe_launch_context(collector, headless: bool = False):
    if collector is None:
        log_error(f"❌ {_LOGIN_PREFIX}:launch_context] collector None")
        return None

    launch = getattr(collector, "_launch_context", None)
    if launch is None:
        log_error(f"❌ {_LOGIN_PREFIX}:launch_context] متد _launch_context وجود ندارد")
        return None

    param_name: str | None = None
    try:
        sig = inspect.signature(launch)
        for p_name in sig.parameters:
            if "headless" in p_name.lower():
                param_name = p_name
                break
    except (ValueError, TypeError) as e:
        log_info(f"   ℹ️ _launch_context signature نامشخص | {type(e).__name__}")
    except Exception as e:
        log_warning(f"   ⚠️ _launch_context signature | {type(e).__name__}: {e}")

    try:
        if param_name:
            t0 = time.monotonic()
            ctx = await launch(**{param_name: headless})
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_info(f"   ✔ _launch_context({param_name}={headless}) | ctx={'✅' if ctx else '❌'} | {elapsed_ms:.0f}ms")
            return ctx
        t0 = time.monotonic()
        ctx = await launch()
        elapsed_ms = (time.monotonic() - t0) * 1000
        log_info(f"   ✔ _launch_context() بدون پارامتر | ctx={'✅' if ctx else '❌'} | {elapsed_ms:.0f}ms")
        return ctx
    except TypeError as e:
        log_warning(f"   ⚠️ _launch_context با «{param_name}» TypeError | {e} — امتحان fallback")
        for alt in ("use_headless", "headless"):
            try:
                ctx = await launch(**{alt: headless})
                log_info(f"   ✔ fallback _launch_context({alt}={headless}) | ctx={'✅' if ctx else '❌'}")
                return ctx
            except TypeError:
                continue
        log_error(f"❌ {_LOGIN_PREFIX}:launch_context] هیچ امضایی کار نکرد | {type(e).__name__}: {e}")
        return None
    except Exception as e:
        log_error(f"❌ {_LOGIN_PREFIX}:launch_context] {type(e).__name__}: {e}")
        return None


# ═══════════════════════════════════════════
# مسیرهای نشست
# ═══════════════════════════════════════════
def _rubika_session_path() -> str:
    cfg = PLUGINS_CONFIG.get("rubikacollector", {}) or {}
    default_path = str(data_paths.SESSIONS_DIR / "rubika_session")
    p = cfg.get("session_path") or default_path
    try:
        os.makedirs(os.path.dirname(p) if os.path.dirname(p) else p, exist_ok=True)
    except Exception as e:
        log_warning(f"⚠️ {_LOGIN_PREFIX}:session:rubika] makedirs | {type(e).__name__}: {e}")
    return p


def _telegram_session_path() -> str:
    cfg = PLUGINS_CONFIG.get("telegramcollector", {}) or {}
    default_path = str(data_paths.SESSIONS_DIR / "telegram_session")
    p = cfg.get("session_path") or default_path
    try:
        os.makedirs(os.path.dirname(p) if os.path.dirname(p) else p, exist_ok=True)
    except Exception as e:
        log_warning(f"⚠️ {_LOGIN_PREFIX}:session:telegram] makedirs | {type(e).__name__}: {e}")
    return p


def _normalize_phone(phone: str) -> str:
    if not phone:
        return ""
    p = str(phone).strip()
    if p.startswith("+98"):
        return "0" + p[3:]
    if p.startswith("98") and not p.startswith("980"):
        return "0" + p[2:]
    if p.startswith("9") and len(p) == 10:
        return "0" + p
    if not p.startswith("0"):
        return "0" + p
    return p


# ═══════════════════════════════════════════
# روبیکا (rubpy)
# ═══════════════════════════════════════════
async def login_rubika() -> bool:
    t0 = time.monotonic()
    log_info(f"▶️ {_LOGIN_PREFIX}:rubika] شروع")

    cfg = PLUGINS_CONFIG.get("rubikacollector", {}) or {}
    phone = (cfg.get("phone") or "").strip()

    if not phone:
        log_error(f"❌ {_LOGIN_PREFIX}:rubika] RUBIKA_PHONE تنظیم نشده")
        return False

    phone = _normalize_phone(phone)
    session_path = _rubika_session_path()
    log_info(f"📱 {_LOGIN_PREFIX}:rubika] phone={phone} | session={session_path}")

    try:
        from rubpy import Client
    except ImportError as e:
        log_error(f"❌ {_LOGIN_PREFIX}:rubika] rubpy نصب نیست | {e}")
        return False

    import os as _os
    session_exists = _os.path.exists(session_path)

    if session_exists:
        log_success(f"✅ {_LOGIN_PREFIX}:rubika] نشست از قبل وجود دارد — بدون لاگین مجدد")
        try:
            import webbrowser
            webbrowser.open("https://web.rubika.ir/")
            log_success(f"🌐 {_LOGIN_PREFIX}:rubika] مرورگر باز شد برای مدیریت گروه‌ها")
            return True
        except Exception as e:
            log_warning(f"⚠️ {_LOGIN_PREFIX}:rubika] باز کردن مرورگر | {e}")
            return False

    log_info(f"🔑 {_LOGIN_PREFIX}:rubika] نشست جدید — منتظر کد پیامکی...")
    client = None
    me = ""
    success = False

    try:
        try:
            client = Client(name=session_path)
        except Exception as e:
            log_error(f"❌ {_LOGIN_PREFIX}:rubika] ساخت Client | {type(e).__name__}: {e}")
            return False

        try:
            await client.start(phone_number=phone)
        except Exception as e:
            log_error(f"❌ {_LOGIN_PREFIX}:rubika] start | {type(e).__name__}: {e}")
            return False

        try:
            me_obj = await client.get_me()
            me = str(me_obj) if me_obj else ""
        except Exception as e:
            log_warning(f"⚠️ {_LOGIN_PREFIX}:rubika] get_me | {type(e).__name__}: {e}")

        success = True
        return True
    finally:
        await _safe_disconnect_client(client, "rubika")
        elapsed_ms = (time.monotonic() - t0) * 1000
        if success:
            log_success(f"✅ {_LOGIN_PREFIX}:rubika] ورود موفق | me={me[:50]} | {elapsed_ms:.0f}ms")
            try:
                import webbrowser
                webbrowser.open("https://web.rubika.ir/")
                log_success(f"🌐 {_LOGIN_PREFIX}:rubika] مرورگر باز شد برای مدیریت گروه‌ها")
            except Exception as e:
                log_warning(f"⚠️ {_LOGIN_PREFIX}:rubika] باز کردن مرورگر | {e}")
        else:
            log_warning(f"⚠️ {_LOGIN_PREFIX}:rubika] ناموفق | {elapsed_ms:.0f}ms")


# ═══════════════════════════════════════════
# تلگرام (Playwright / web)
# ═══════════════════════════════════════════
async def login_telegram() -> bool:
    log_info("[..] تلگرام وِب: باز کردن مرورگر...")
    log_info("     ⚠️ VPN باید روشن باشد (web.telegram.org)")
    log_info('')

    if not _warn_bot_running("تلگرام"):
        log_warning(f"⚠️ {_LOGIN_PREFIX}:telegram] لغو شد")
        return False

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log_info("[X] playwright نصب نیست")
        return False

    from config import CHROME_PATH
    user_data_dir = str(data_paths.SESSIONS_DIR / "telegram_web")
    os.makedirs(user_data_dir, exist_ok=True)

    pw = None
    ctx = None
    try:
        pw = await async_playwright().start()
        launch_opts = {
            "user_data_dir": user_data_dir,
            "headless": False,
            "viewport": {"width": 1366, "height": 768},
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        }
        if CHROME_PATH and os.path.exists(CHROME_PATH):
            launch_opts["executable_path"] = CHROME_PATH
        else:
            launch_opts["channel"] = "chrome"

        ctx = await pw.chromium.launch_persistent_context(**launch_opts)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://web.telegram.org/k/", wait_until="domcontentloaded", timeout=60000)

        log_info('')
        log_info("═" * 60)
        log_info("📱 در مرورگر باز شده:")
        log_info("   ۱) شماره‌ات را وارد کن (+98...)")
        log_info("   ۲) کد پیامکی را وارد کن")
        log_info("   ۳) اگر 2FA داری، رمز را وارد کن")
        log_info("   ۴) بعد از دیدن چت‌ها، این پنجره را ببند")
        log_info("═" * 60)
        log_info('')
        log_info("⏳ منتظر می‌مانم تا مرورگر را ببندی...")
        log_info('')

        with contextlib.suppress(Exception):
            await ctx.pages[0].wait_for_event("close", timeout=600000)

        log_info('')
        log_info("[OK] نشست تلگرام وِب ذخیره شد ✅")
        log_info(f"     مسیر: {user_data_dir}")
        return True
    except Exception as e:
        log_info(f"[X] خطا: {type(e).__name__}: {e}")
        return False
    finally:
        try:
            if ctx:
                await ctx.close()
        except Exception:
            pass
        try:
            if pw:
                await pw.stop()
        except Exception:
            pass


# ═══════════════════════════════════════════
# یوتیوب (CDP)
# ═══════════════════════════════════════════
async def login_youtube() -> bool:
    log_info("[..] یوتیوب: باز کردن Chrome واقعی...")
    log_info("     ⚠️ VPN باید روشن باشد (youtube.com)")
    log_info('')

    if not _warn_bot_running("یوتیوب"):
        log_warning(f"⚠️ {_LOGIN_PREFIX}:youtube] لغو شد")
        return False

    import subprocess
    from config import CHROME_PATH

    user_data_dir = str(data_paths.SESSIONS_DIR / "youtube_web")
    os.makedirs(user_data_dir, exist_ok=True)

    chrome_exe = None
    if CHROME_PATH and os.path.exists(CHROME_PATH):
        chrome_exe = CHROME_PATH
    else:
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        for p in candidates:
            if os.path.exists(p):
                chrome_exe = p
                break

    if not chrome_exe:
        log_info("[X] Chrome پیدا نشد.")
        return False

    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = os.path.join(user_data_dir, name)
        with contextlib.suppress(Exception):
            os.remove(p)

    CDP_PORT = 9222
    cmd = [
        chrome_exe,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--ignore-certificate-errors",
        "https://www.youtube.com/",
    ]

    log_info(f"[..] اجرای Chrome واقعی با پورت {CDP_PORT}...")
    try:
        subprocess.Popen(cmd)
    except Exception as e:
        log_info(f"[X] خطا در اجرای Chrome: {e}")
        return False

    log_info('')
    log_info("═" * 60)
    log_info("📱 در مرورگر Chrome که باز شد:")
    log_info("   ۱) روی Sign in کلیک کن (گوشهٔ بالا راست)")
    log_info("   ۲) ایمیل/شماره گوگل را وارد کن")
    log_info("   ۳) رمز عبور را وارد کن")
    log_info("   ۴) اگر 2FA داری، کد را وارد کن")
    log_info("   ۵) بعد از دیدن آواتار، پنجره را ببند")
    log_info("═" * 60)
    log_info('')
    log_info("⏳ منتظر می‌مانم تا Chrome را ببندی...")
    log_info('')

    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")

        if not browser.contexts:
            log_info("[X] هیچ context ای پیدا نشد")
            await pw.stop()
            return False

        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        with contextlib.suppress(Exception):
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

        try:
            if "youtube.com" not in (page.url or ""):
                await page.goto("https://www.youtube.com/", wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log_warning(f"goto: {e}")

        await asyncio.sleep(2)

        is_logged = False
        try:
            avatar = await page.query_selector('img#avatar-btn, button[aria-label*="Account" i], a[href*="/feed/subscriptions"]')
            is_logged = avatar is not None
        except Exception as e:
            log_warning(f"is_logged: {e}")

        try:
            state_path = os.path.join(data_paths.SESSIONS_DIR, "youtube_state.json")
            await context.storage_state(path=state_path)
            log_info(f"   ✔ state ذخیره شد | {state_path}")
        except Exception as e:
            log_warning(f"   ⚠️ storage_state | {e}")

        await pw.stop()

        if is_logged:
            log_info('')
            log_info("[OK] نشست یوتیوب ذخیره شد ✅")
            log_info(f"     مسیر پروفایل: {user_data_dir}")
            log_info('')
            log_info("⚠️  نکته مهم:")
            log_info("    در اجرای بعدی ربات، این Chrome باید باز بماند.")
            log_info("    اگر بسته شود، دوباره login_youtube را اجرا کن.")
            return True
        else:
            log_info('')
            log_info("[X] ورود تشخیص داده نشد.")
            log_info("    ممکنه لاگین کامل نشده باشه یا صفحه لود نشده.")
            return False

    except Exception as e:
        log_info(f"[X] خطا در اتصال CDP: {type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════
# فیسبوک (CDP)
# ═══════════════════════════════════════════
async def login_facebook() -> bool:
    log_info("[..] فیسبوک: باز کردن Chrome واقعی...")
    log_info("     ⚠️ برای فیسبوک VPN لازم است")
    log_info('')

    if not _warn_bot_running("فیسبوک"):
        log_warning(f"⚠️ {_LOGIN_PREFIX}:facebook] لغو شد")
        return False

    import subprocess
    from config import CHROME_PATH

    user_data_dir = str(data_paths.SESSIONS_DIR / "facebook")
    os.makedirs(user_data_dir, exist_ok=True)

    chrome_exe = None
    if CHROME_PATH and os.path.exists(CHROME_PATH):
        chrome_exe = CHROME_PATH
    else:
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        for p in candidates:
            if os.path.exists(p):
                chrome_exe = p
                break

    if not chrome_exe:
        log_info("[X] Chrome پیدا نشد.")
        return False

    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = os.path.join(user_data_dir, name)
        with contextlib.suppress(Exception):
            os.remove(p)

    CDP_PORT = 9223
    cmd = [
        chrome_exe,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "https://www.facebook.com/",
    ]

    log_info(f"[..] اجرای Chrome واقعی با پورت {CDP_PORT}...")
    try:
        subprocess.Popen(cmd)
    except Exception as e:
        log_info(f"[X] خطا در اجرای Chrome: {e}")
        return False

    log_info('')
    log_info("═" * 60)
    log_info("📱 در مرورگر Chrome که باز شد:")
    log_info("   ۱) ایمیل/شماره و رمز فیسبوک را وارد کن")
    log_info("   ۲) اگر 2FA داری، کد را وارد کن")
    log_info("   ۳) اگر checkpoint آمد، آن را رد کن")
    log_info("   ۴) بعد از دیدن فید، این پنجره را ببند")
    log_info("═" * 60)
    log_info('')
    log_info("⏳ منتظر می‌مانم تا Chrome را ببندی...")
    log_info('')

    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")

        if not browser.contexts:
            log_info("[X] هیچ context ای پیدا نشد")
            await pw.stop()
            return False

        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        with contextlib.suppress(Exception):
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

        try:
            if "facebook.com" not in (page.url or ""):
                await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log_warning(f"goto: {e}")

        await asyncio.sleep(2)

        is_logged = False
        try:
            markers = [
                'div[role="navigation"][aria-label*="Facebook"]',
                'div[aria-label="Facebook"][role="banner"]',
                '[data-pagelet="LeftNav"]',
                'div[role="feed"]',
                'a[href*="/friends/"]',
                'a[href*="/groups/"]',
            ]
            for sel in markers:
                el = await page.query_selector(sel)
                if el:
                    is_logged = True
                    break
        except Exception as e:
            log_warning(f"is_logged: {e}")

        try:
            state_path = os.path.join(data_paths.SESSIONS_DIR, "facebook_state.json")
            await context.storage_state(path=state_path)
            log_info(f"   ✔ state ذخیره شد | {state_path}")
        except Exception as e:
            log_warning(f"   ⚠️ storage_state | {e}")

        await pw.stop()

        if is_logged:
            log_info('')
            log_info("[OK] نشست فیسبوک ذخیره شد ✅")
            log_info(f"     مسیر پروفایل: {user_data_dir}")
            log_info('')
            log_info("⚠️  نکته مهم:")
            log_info("    برای اجرای بعدی، این پروفایل آماده است.")
            log_info("    اگر فیسبوک checkpoint داد، دوباره این را اجرا کن.")
            return True
        else:
            log_info('')
            log_info("[X] ورود تشخیص داده نشد.")
            log_info("    ممکنه لاگین کامل نشده باشه یا checkpoint خورده.")
            return False

    except Exception as e:
        log_info(f"[X] خطا در اتصال CDP: {type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════
# اینستاگرام (CDP - جدید)
# ═══════════════════════════════════════════
async def login_instagram() -> bool:
    """
    ورود تعاملی به اینستاگرام با اتصال به Chrome واقعی از طریق CDP.
    """
    log_info("[..] اینستاگرام: باز کردن Chrome واقعی...")
    log_info("     ⚠️ VPN باید روشن باشد (instagram.com)")
    log_info('')

    if not _warn_bot_running("اینستاگرام"):
        log_warning(f"⚠️ {_LOGIN_PREFIX}:instagram] لغو شد")
        return False

    import subprocess
    from config import CHROME_PATH

    user_data_dir = str(data_paths.SESSIONS_DIR / "instagram")
    os.makedirs(user_data_dir, exist_ok=True)

    chrome_exe = None
    if CHROME_PATH and os.path.exists(CHROME_PATH):
        chrome_exe = CHROME_PATH
    else:
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        for p in candidates:
            if os.path.exists(p):
                chrome_exe = p
                break

    if not chrome_exe:
        log_info("[X] Chrome پیدا نشد.")
        return False

    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = os.path.join(user_data_dir, name)
        with contextlib.suppress(Exception):
            os.remove(p)

    CDP_PORT = 9224
    cmd = [
        chrome_exe,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "https://www.instagram.com/",
    ]

    log_info(f"[..] اجرای Chrome واقعی با پورت {CDP_PORT}...")
    try:
        subprocess.Popen(cmd)
    except Exception as e:
        log_info(f"[X] خطا در اجرای Chrome: {e}")
        return False

    log_info('')
    log_info("═" * 60)
    log_info("📱 در مرورگر Chrome که باز شد:")
    log_info("   ۱) نام کاربری و رمز اینستاگرام را وارد کن")
    log_info("   ۲) اگر چالش (reCAPTCHA) آمد، آن را حل کن")
    log_info("   ۳) بعد از دیدن فید اینستاگرام، این پنجره را ببند")
    log_info("═" * 60)
    log_info('')
    log_info("⏳ منتظر می‌مانم تا Chrome را ببندی...")
    log_info('')

    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")

        if not browser.contexts:
            log_info("[X] هیچ context ای پیدا نشد")
            await pw.stop()
            return False

        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        with contextlib.suppress(Exception):
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

        try:
            state_path = os.path.join(data_paths.SESSIONS_DIR, "instagram_state.json")
            await context.storage_state(path=state_path)
            log_info(f"   ✔ state ذخیره شد | {state_path}")
        except Exception as e:
            log_warning(f"   ⚠️ storage_state | {e}")

        await pw.stop()

        log_info('')
        log_info("[OK] نشست اینستاگرام ذخیره شد ✅")
        log_info(f"     مسیر پروفایل: {user_data_dir}")
        return True

    except Exception as e:
        log_info(f"[X] خطا در اتصال CDP: {type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════
# ورود مرورگری مشترک (بقیه پلتفرم‌ها)
# ═══════════════════════════════════════════
async def login_browser(
    platform_label: str,
    collector_cls,
    url: str,
) -> bool:
    t0 = time.monotonic()
    tag = platform_label.lower().replace(" ", "_")
    log_info(f"▶️ {_LOGIN_PREFIX}:browser:{tag}] شروع | url={url}")

    if not _warn_bot_running(platform_label):
        log_warning(f"⚠️ {_LOGIN_PREFIX}:browser:{tag}] لغو شد (ربات روشن)")
        return False

    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        log_error(f"❌ {_LOGIN_PREFIX}:browser:{tag}] playwright نصب نیست | {e}")
        return False

    try:
        try:
            collector = collector_cls(headless=False)
        except TypeError:
            collector = collector_cls()
    except Exception as e:
        log_error(f"❌ {_LOGIN_PREFIX}:browser:{tag}] ساخت collector | {type(e).__name__}: {e}")
        return False

    log_info(f"🌐 {_LOGIN_PREFIX}:browser:{tag}] مرورگر باز می‌شود...")
    logged_in = False

    try:
        try:
            t_launch = time.monotonic()
            collector._pw = await async_playwright().start()
            collector._context = await _safe_launch_context(collector, headless=False)
            if collector._context is None:
                log_error(f"❌ {_LOGIN_PREFIX}:browser:{tag}] _launch_context None برگرداند")
                return False
            launch_ms = (time.monotonic() - t_launch) * 1000
            log_info(f"   ✔ مرورگر راه افتاد | {launch_ms:.0f}ms")
        except Exception as e:
            log_error(f"❌ {_LOGIN_PREFIX}:browser:{tag}] launch | {type(e).__name__}: {e}")
            return False

        try:
            page = (
                collector._context.pages[0]
                if collector._context.pages
                else await collector._context.new_page()
            )
            t_goto = time.monotonic()
            await page.goto(url, wait_until="domcontentloaded")
            goto_ms = (time.monotonic() - t_goto) * 1000
            log_info(f"   ✔ صفحه باز شد | {goto_ms:.0f}ms | url={page.url}")
        except Exception as e:
            log_error(f"❌ {_LOGIN_PREFIX}:browser:{tag}] goto | {type(e).__name__}: {e}")
            return False

        user_input = await _input_async(
            f"\n➡️ بعد از ورود کامل به {platform_label}، "
            f"اینجا Enter بزن (timeout={_INPUT_TIMEOUT_S}s)... "
        )
        if user_input is None:
            log_warning(f"⚠️ {_LOGIN_PREFIX}:browser:{tag}] لغو/تایم‌اوت توسط کاربر")
            return False

        collector._page = page
        await asyncio.sleep(2)

        try:
            logged_in = await collector._is_logged_in()
        except Exception as e:
            log_warning(f"⚠️ {_LOGIN_PREFIX}:browser:{tag}] is_logged_in | {type(e).__name__}: {e} — fallback به _connected")
            logged_in = bool(getattr(collector, "_connected", False))

        if logged_in:
            log_success(f"✅ {_LOGIN_PREFIX}:browser:{tag}] ورود تشخیص داده شد")
            if hasattr(collector, "_export_state"):
                try:
                    await collector._export_state()
                    log_info("   ✔ state ذخیره شد")
                except Exception as e:
                    log_warning(f"   ⚠️ export_state | {type(e).__name__}: {e}")
        else:
            log_warning(f"⚠️ {_LOGIN_PREFIX}:browser:{tag}] ورود تشخیص داده نشد — ولی ممکنه نشست ذخیره شده باشه")
            if hasattr(collector, "dump_dom"):
                try:
                    dom_path = await collector.dump_dom()
                    log_info(f"   📄 DOM ذخیره شد | {dom_path}")
                except Exception as e:
                    log_warning(f"   ⚠️ dump_dom | {type(e).__name__}: {e}")

        return logged_in
    finally:
        await _safe_disconnect_async(collector)
        elapsed_ms = (time.monotonic() - t0) * 1000
        if logged_in:
            log_success(f"✅ {_LOGIN_PREFIX}:browser:{tag}] کامل | {elapsed_ms:.0f}ms")
        else:
            log_warning(f"⚠️ {_LOGIN_PREFIX}:browser:{tag}] ناتمام | {elapsed_ms:.0f}ms")


# ═══════════════════════════════════════════
# wrappers
# ═══════════════════════════════════════════
async def login_bale() -> bool:
    from collectors.bale import BaleCollector
    return await login_browser("بله", BaleCollector, "https://web.bale.ai/")


async def login_eitaa() -> bool:
    from collectors.eitaa import EitaaCollector
    return await login_browser("ایتا", EitaaCollector, "https://web.eitaa.com/")


async def login_whatsapp() -> bool:
    from collectors.whatsapp import WhatsAppCollector
    return await login_browser("واتساپ", WhatsAppCollector, "https://web.whatsapp.com/")


async def login_linkedin() -> bool:
    from collectors.linkedin import LinkedInCollector
    return await login_browser("لینکدین", LinkedInCollector, "https://www.linkedin.com/feed/")


# ═══════════════════════════════════════════
# منو
# ═══════════════════════════════════════════
_MENU = """
╔══════════════════════════════════════════════════════════╗
║        ورود تعاملی پلتفرم‌ها (ساخت نشست)                ║
╠══════════════════════════════════════════════════════════╣
║  [1] روبیکا (Rubika)                                     ║
║  [2] بله (Bale - مرورگر)                                 ║
║  [3] ایتا (Eitaa - مرورگر)                               ║
║  [4] تلگرام (Telegram - مرورگر)                          ║
║  [5] واتساپ (WhatsApp - مرورگر/QR)                       ║
║  [6] لینکدین (LinkedIn - مرورگر)                         ║
║  [7] اینستاگرام (Instagram - مرورگر)                     ║
║  [8] یوتیوب (YouTube - مرورگر)                           ║
║  [9] فیسبوک (Facebook - مرورگر)                          ║
║  [0] ورود همه (به ترتیب)                                 ║
║  [q] خروج                                                ║
╚══════════════════════════════════════════════════════════╝
"""


_ACTIONS: dict[str, tuple[str, Callable[[], Awaitable[bool]]]] = {
    "1": ("rubika", login_rubika),
    "2": ("bale", login_bale),
    "3": ("eitaa", login_eitaa),
    "4": ("telegram", login_telegram),
    "5": ("whatsapp", login_whatsapp),
    "6": ("linkedin", login_linkedin),
    "7": ("instagram", login_instagram),
    "8": ("youtube", login_youtube),
    "9": ("facebook", login_facebook),
}

_BY_NAME: dict[str, Callable[[], Awaitable[bool]]] = {
    name: func for _key, (name, func) in _ACTIONS.items()
}

_LOGIN_ALL_ORDER = [
    "rubika", "bale", "eitaa", "telegram",
    "whatsapp", "linkedin", "instagram", "youtube",
    "facebook",
]


async def _login_all() -> None:
    log_info(f"🎯 {_LOGIN_PREFIX}:all] ورود به {len(_LOGIN_ALL_ORDER)} پلتفرم")
    success_count = 0
    fail_count = 0

    for name in _LOGIN_ALL_ORDER:
        func = _BY_NAME.get(name)
        if not func:
            log_warning(f"   ⚠️ {_LOGIN_PREFIX}:all] «{name}» تابع ندارد — skip")
            continue

        log_info(f"   ▶️ {_LOGIN_PREFIX}:all] {name}")
        try:
            ok = await func()
            if ok:
                success_count += 1
                log_success(f"   ✅ {_LOGIN_PREFIX}:all] {name}")
            else:
                fail_count += 1
                log_warning(f"   ⚠️ {_LOGIN_PREFIX}:all] {name} ناموفق")
        except Exception as e:
            fail_count += 1
            log_error(f"   ❌ {_LOGIN_PREFIX}:all] {name} | {type(e).__name__}: {e}")

    log_info(f"🏁 {_LOGIN_PREFIX}:all] کامل | ✅{success_count} ⚠️{fail_count}")


async def main() -> None:
    log_info(f"▶️ {_LOGIN_PREFIX}:main] شروع")

    running, pid = _is_bot_running()
    if running:
        log_info('')
        log_info("╔" + "═" * 58 + "╗")
        print(f"║  ⚠️  توجه: ربات الان روشن است (PID: {pid or 0:>6})         ║")
        log_info("║  برای لاگین مرورگری، ابتدا ربات را Stop کن.          ║")
        log_info("╚" + "═" * 58 + "╝")

    while True:
        log_info(_MENU)

        try:
            choice = await _input_async("انتخاب: ", timeout_s=300)
        except KeyboardInterrupt:
            log_info(f"ℹ️ {_LOGIN_PREFIX}:main] KeyboardInterrupt در منو")
            return

        if choice is None:
            log_warning(f"⚠️ {_LOGIN_PREFIX}:main] لغو در منو")
            return

        choice = choice.strip().lower()

        if choice in ("0", "", "q", "quit", "exit"):
            if choice == "0":
                log_info(f"🎯 {_LOGIN_PREFIX}:main] ورود همه")
                await _login_all()
                continue
            log_info(f"ℹ️ {_LOGIN_PREFIX}:main] خروج")
            return

        action = _ACTIONS.get(choice)
        if not action:
            log_warning(f"⚠️ {_LOGIN_PREFIX}:main] انتخاب نامعتبر: {choice!r}")
            continue

        platform, func = action
        log_info(f"🎯 {_LOGIN_PREFIX}:main] platform={platform}")

        t0 = time.monotonic()
        try:
            ok = await func()
            elapsed_ms = (time.monotonic() - t0) * 1000
            if ok:
                log_success(f"✅ {_LOGIN_PREFIX}:main] {platform} | success | {elapsed_ms:.0f}ms")
            else:
                log_warning(f"⚠️ {_LOGIN_PREFIX}:main] {platform} | failed/incomplete | {elapsed_ms:.0f}ms")
        except Exception as e:
            log_error(f"❌ {_LOGIN_PREFIX}:main] {platform} | {type(e).__name__}: {e}")
            import traceback
            for ln in traceback.format_exc().splitlines()[-5:]:
                log_error(f"   ↳ {ln}")

        try:
            cont = await _input_async(
                "\nمی‌خواهی پلتفرم دیگری هم وارد شوی؟ (Enter = بله، q = خروج): ",
                timeout_s=60,
            )
        except KeyboardInterrupt:
            log_info(f"ℹ️ {_LOGIN_PREFIX}:main] KeyboardInterrupt — خروج")
            return

        if cont is None or cont.strip().lower() in ("q", "quit", "exit"):
            log_info(f"ℹ️ {_LOGIN_PREFIX}:main] خروج")
            return


# ═══════════════════════════════════════════
# runner
# ═══════════════════════════════════════════
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log_warning(f"⚠️ {_LOGIN_PREFIX}] KeyboardInterrupt")
    except Exception as e:
        log_error(f"❌ {_LOGIN_PREFIX}:fatal] {type(e).__name__}: {e}")
        sys.exit(1)