"""
login.py — اسکریپت ورود تعاملی (یک‌بار اجرا برای هر پلتفرم).

چرا جدا از main.py؟
  ورود کاربر عادی نیاز به «کد پیامکی» یا «اسکن QR» دارد و main.py در پس‌زمینه
  اجرا می‌شود. این اسکریپت را یک‌بار به‌صورت دستی اجرا کن تا نشست (session)
  ساخته شود. بعد main.py بی‌صدا از همان نشست استفاده می‌کند.

اجرا:
    python login.py

نسخهٔ ۷ (این نسخه):
  - 🔴 FIX: منو [8] اینستاگرام → [7] (شماره‌گذاری پشت‌سرهم).
  - 🔴 FIX: چک «ربات روشن است؟» قبل از هر لاگین مرورگری — چون
    پروفایل Playwright قفل می‌شود و خطای exitCode=21 می‌دهد.
  - 🔴 FIX: _ACTIONS کلید «8» → «7» برای اینستاگرام.
  - 🎨 پیام واضح‌تر برای کاربر در صورت قفل‌بودن پروفایل.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۶.
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
from config import LOGIN_INPUT_TIMEOUT_S, PLUGINS_CONFIG
from core.kernel.logger import log_error, log_info, log_success, log_warning
from data import paths as data_paths

_LOGIN_PREFIX = "[LOGIN"

# ─── timeout برای input کاربر (ثانیه) — از config ───
_INPUT_TIMEOUT_S = LOGIN_INPUT_TIMEOUT_S

# ─── مسیر PID (از data.paths) ───
_PID_FILE = str(data_paths.BOT_PID)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _is_bot_running() -> tuple[bool, int | None]:
    """
    🔴 NEW: چک می‌کند ربات روشن است یا نه.

    چون Playwright با پروفایل قفل‌شده کار نمی‌کند و خطای
    exitCode=21 می‌دهد، قبل از هر لاگین مرورگری این چک لازم است.

    Returns:
        (is_running, pid)
    """
    try:
        with open(_PID_FILE, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return False, None

    # چک زنده‌بودن پروسه
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
        # fallback بدون psutil
        try:
            os.kill(pid, 0)
            return True, pid
        except (OSError, ProcessLookupError):
            return False, pid

    return False, pid


def _warn_bot_running(platform_label: str) -> bool:
    """
    هشدار می‌دهد اگر ربات روشن است.

    Returns:
        True اگر کاربر تصمیم به ادامه گرفت، False اگر لغو کرد.
    """
    running, pid = _is_bot_running()
    if not running:
        return True

    print()
    print("╔" + "═" * 58 + "╗")
    print("║  ⚠️  ربات روشن است — پروفایل Playwright قفل است!      ║")
    print("╚" + "═" * 58 + "╝")
    print()
    print(f"  PID ربات: {pid}")
    print()
    print("  برای لاگین به مرورگر، ابتدا ربات را Stop کن:")
    print("     ۱) python panel.py  →  گزینه [3] Stop")
    print("     ۲) یا در همین ترمینال Ctrl+C بزن")
    print()
    print("  اگر ادامه بدهی، مرورگر با خطای زیر باز نمی‌شود:")
    print("     Target page, context or browser has been closed")
    print()

    try:
        ans = input(f"  با این حال ادامه بدهی؟ [{platform_label}] [y/N]: ")
    except (EOFError, KeyboardInterrupt):
        return False

    return ans.strip().lower() in ("y", "yes")


async def _input_async(
    prompt: str, timeout_s: int | None = None
) -> str | None:
    """input غیرمسدودکننده — با timeout اختیاری."""
    if timeout_s is None:
        timeout_s = _INPUT_TIMEOUT_S

    loop = asyncio.get_running_loop()
    try:
        coro = loop.run_in_executor(None, input, prompt)
        if timeout_s and timeout_s > 0:
            return await asyncio.wait_for(coro, timeout=timeout_s)
        return await coro
    except TimeoutError:
        log_warning(
            f"⏰ {_LOGIN_PREFIX}:input] timeout ({timeout_s}s)"
        )
        return None
    except (EOFError, KeyboardInterrupt) as e:
        log_warning(
            f"⚠️ {_LOGIN_PREFIX}:input] {type(e).__name__} — لغو"
        )
        return None


async def _safe_disconnect_async(collector) -> None:
    """disconnect ایمن + async."""
    if collector is None:
        return
    try:
        await collector.disconnect()
    except Exception as e:
        log_warning(
            f"⚠️ {_LOGIN_PREFIX}:disconnect] "
            f"{type(e).__name__}: {e}"
        )


async def _safe_disconnect_client(
    client, name: str = "client"
) -> None:
    """disconnect ایمن برای کلاینت‌های rubpy/telethon."""
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
        log_warning(
            f"⚠️ {_LOGIN_PREFIX}:{name}:disconnect] "
            f"{type(e).__name__}: {e}"
        )


async def _safe_launch_context(collector, headless: bool = False):
    """
    صدا زدن _launch_context با امضای درست.

    با inspect.signature پارامتر «headless» را کشف می‌کنیم.
    """
    if collector is None:
        log_error(
            f"❌ {_LOGIN_PREFIX}:launch_context] collector None"
        )
        return None

    launch = getattr(collector, "_launch_context", None)
    if launch is None:
        log_error(
            f"❌ {_LOGIN_PREFIX}:launch_context] "
            f"متد _launch_context وجود ندارد"
        )
        return None

    param_name: str | None = None
    try:
        sig = inspect.signature(launch)
        for p_name in sig.parameters:
            if "headless" in p_name.lower():
                param_name = p_name
                break
    except (ValueError, TypeError) as e:
        log_info(
            f"   ℹ️ _launch_context signature نامشخص | "
            f"{type(e).__name__}"
        )
    except Exception as e:
        log_warning(
            f"   ⚠️ _launch_context signature | "
            f"{type(e).__name__}: {e}"
        )

    try:
        if param_name:
            t0 = time.monotonic()
            ctx = await launch(**{param_name: headless})
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_info(
                f"   ✔ _launch_context({param_name}={headless}) | "
                f"ctx={'✅' if ctx else '❌'} | {elapsed_ms:.0f}ms"
            )
            return ctx
        t0 = time.monotonic()
        ctx = await launch()
        elapsed_ms = (time.monotonic() - t0) * 1000
        log_info(
            f"   ✔ _launch_context() بدون پارامتر | "
            f"ctx={'✅' if ctx else '❌'} | {elapsed_ms:.0f}ms"
        )
        return ctx
    except TypeError as e:
        log_warning(
            f"   ⚠️ _launch_context با «{param_name}» TypeError | "
            f"{e} — امتحان fallback"
        )
        for alt in ("use_headless", "headless"):
            try:
                ctx = await launch(**{alt: headless})
                log_info(
                    f"   ✔ fallback _launch_context({alt}={headless}) | "
                    f"ctx={'✅' if ctx else '❌'}"
                )
                return ctx
            except TypeError:
                continue
        log_error(
            f"❌ {_LOGIN_PREFIX}:launch_context] هیچ امضایی کار نکرد | "
            f"{type(e).__name__}: {e}"
        )
        return None
    except Exception as e:
        log_error(
            f"❌ {_LOGIN_PREFIX}:launch_context] "
            f"{type(e).__name__}: {e}"
        )
        return None


# ═══════════════════════════════════════════
# مسیرهای نشست (از config)
# ═══════════════════════════════════════════
def _rubika_session_path() -> str:
    cfg = PLUGINS_CONFIG.get("rubikacollector", {}) or {}
    default_path = str(data_paths.SESSIONS_DIR / "rubika_session")
    p = cfg.get("session_path") or default_path
    try:
        os.makedirs(
            os.path.dirname(p) if os.path.dirname(p) else p,
            exist_ok=True,
        )
    except Exception as e:
        log_warning(
            f"⚠️ {_LOGIN_PREFIX}:session:rubika] makedirs | "
            f"{type(e).__name__}: {e}"
        )
    return p


def _telegram_session_path() -> str:
    cfg = PLUGINS_CONFIG.get("telegramcollector", {}) or {}
    default_path = str(data_paths.SESSIONS_DIR / "telegram_session")
    p = cfg.get("session_path") or default_path
    try:
        os.makedirs(
            os.path.dirname(p) if os.path.dirname(p) else p,
            exist_ok=True,
        )
    except Exception as e:
        log_warning(
            f"⚠️ {_LOGIN_PREFIX}:session:telegram] makedirs | "
            f"{type(e).__name__}: {e}"
        )
    return p

def _normalize_phone(phone: str) -> str:
    """نرمال‌سازی شماره تلفن ایران → 09XXXXXXXXX."""
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
    """ورود روبیکا با شماره + کد پیامکی."""
    t0 = time.monotonic()
    log_info(f"▶️ {_LOGIN_PREFIX}:rubika] شروع")

    cfg = PLUGINS_CONFIG.get("rubikacollector", {}) or {}
    phone = (cfg.get("phone") or "").strip()

    if not phone:
        log_error(
            f"❌ {_LOGIN_PREFIX}:rubika] RUBIKA_PHONE تنظیم نشده"
        )
        return False

    phone = _normalize_phone(phone)
    session_path = _rubika_session_path()
    log_info(
        f"📱 {_LOGIN_PREFIX}:rubika] phone={phone} | "
        f"session={session_path}"
    )

    try:
        from rubpy import Client
    except ImportError as e:
        log_error(
            f"❌ {_LOGIN_PREFIX}:rubika] rubpy نصب نیست | {e}"
        )
        return False

    log_info(f"🔑 {_LOGIN_PREFIX}:rubika] منتظر کد پیامکی...")

    client = None
    me = ""
    success = False

    try:
        try:
            client = Client(name=session_path)
        except Exception as e:
            log_error(
                f"❌ {_LOGIN_PREFIX}:rubika] ساخت Client | "
                f"{type(e).__name__}: {e}"
            )
            return False

        try:
            try:
                await client.start(phone_number=phone)
            except TypeError:
                await client.start(phone)
        except Exception as e:
            log_error(
                f"❌ {_LOGIN_PREFIX}:rubika] start | "
                f"{type(e).__name__}: {e}"
            )
            return False

        try:
            me_obj = await client.get_me()
            me = str(me_obj) if me_obj else ""
        except Exception as e:
            log_warning(
                f"⚠️ {_LOGIN_PREFIX}:rubika] get_me | "
                f"{type(e).__name__}: {e}"
            )

        success = True
        return True
    finally:
        await _safe_disconnect_client(client, "rubika")

        elapsed_ms = (time.monotonic() - t0) * 1000
        if success:
            log_success(
                f"✅ {_LOGIN_PREFIX}:rubika] ورود موفق | "
                f"me={me[:50]} | {elapsed_ms:.0f}ms"
            )
        else:
            log_warning(
                f"⚠️ {_LOGIN_PREFIX}:rubika] ناموفق | "
                f"{elapsed_ms:.0f}ms"
            )


# ═══════════════════════════════════════════
# تلگرام (Playwright / web)
# ═══════════════════════════════════════════
async def login_telegram() -> bool:
    """ورود تعاملی به تلگرام وِب (مرورگر)."""
    print("[..] تلگرام وِب: باز کردن مرورگر...")
    print("     ⚠️ VPN باید روشن باشد (web.telegram.org)")
    print()

    # 🔴 NEW: چک ربات
    if not _warn_bot_running("تلگرام"):
        log_warning(f"⚠️ {_LOGIN_PREFIX}:telegram] لغو شد")
        return False

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("[X] playwright نصب نیست")
        return False

    from config import CHROME_PATH

    user_data_dir = str(
        data_paths.SESSIONS_DIR / "telegram_web"
    )
    os.makedirs(user_data_dir, exist_ok=True)

    pw = None
    ctx = None
    try:
        pw = await async_playwright().start()

        launch_opts = {
            "user_data_dir": user_data_dir,
            "headless": False,
            "viewport": {"width": 1366, "height": 768},
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        if CHROME_PATH and os.path.exists(CHROME_PATH):
            launch_opts["executable_path"] = CHROME_PATH
        else:
            launch_opts["channel"] = "chrome"

        ctx = await pw.chromium.launch_persistent_context(
            **launch_opts
        )
        page = (
            ctx.pages[0] if ctx.pages
            else await ctx.new_page()
        )
        await page.goto(
            "https://web.telegram.org/k/",
            wait_until="domcontentloaded",
            timeout=60000,
        )

        print()
        print("═" * 60)
        print("📱 در مرورگر باز شده:")
        print("   ۱) شماره‌ات را وارد کن (+98...)")
        print("   ۲) کد پیامکی را وارد کن")
        print("   ۳) اگر 2FA داری، رمز را وارد کن")
        print("   ۴) بعد از دیدن چت‌ها، این پنجره را ببند")
        print("═" * 60)
        print()
        print("⏳ منتظر می‌مانم تا مرورگر را ببندی...")
        print()

        try:
            await ctx.pages[0].wait_for_event(
                "close", timeout=600000
            )
        except Exception:
            pass

        print()
        print("[OK] نشست تلگرام وِب ذخیره شد ✅")
        print(f"     مسیر: {user_data_dir}")
        return True

    except Exception as e:
        print(f"[X] خطا: {type(e).__name__}: {e}")
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
# ورود مرورگری مشترک
# ═══════════════════════════════════════════
async def login_browser(
    platform_label: str,
    collector_cls,
    url: str,
) -> bool:
    """
    ورود مرورگری مشترک (ایتا / بله / واتساپ / لینکدین / اینستاگرام).

    🔴 NEW: قبل از باز کردن مرورگر، چک می‌کند ربات روشن نباشد.
    """
    t0 = time.monotonic()
    tag = platform_label.lower().replace(" ", "_")
    log_info(f"▶️ {_LOGIN_PREFIX}:browser:{tag}] شروع | url={url}")

    # 🔴 NEW: چک ربات
    if not _warn_bot_running(platform_label):
        log_warning(
            f"⚠️ {_LOGIN_PREFIX}:browser:{tag}] لغو شد (ربات روشن)"
        )
        return False

    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        log_error(
            f"❌ {_LOGIN_PREFIX}:browser:{tag}] "
            f"playwright نصب نیست | {e}"
        )
        return False

    try:
        try:
            collector = collector_cls(headless=False)
        except TypeError:
            collector = collector_cls()
    except Exception as e:
        log_error(
            f"❌ {_LOGIN_PREFIX}:browser:{tag}] ساخت collector | "
            f"{type(e).__name__}: {e}"
        )
        return False

    log_info(f"🌐 {_LOGIN_PREFIX}:browser:{tag}] مرورگر باز می‌شود...")

    logged_in = False

    try:
        try:
            t_launch = time.monotonic()
            collector._pw = await async_playwright().start()
            collector._context = await _safe_launch_context(
                collector, headless=False
            )
            if collector._context is None:
                log_error(
                    f"❌ {_LOGIN_PREFIX}:browser:{tag}] "
                    f"_launch_context None برگرداند"
                )
                return False
            launch_ms = (time.monotonic() - t_launch) * 1000
            log_info(f"   ✔ مرورگر راه افتاد | {launch_ms:.0f}ms")
        except Exception as e:
            log_error(
                f"❌ {_LOGIN_PREFIX}:browser:{tag}] launch | "
                f"{type(e).__name__}: {e}"
            )
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
            log_info(
                f"   ✔ صفحه باز شد | {goto_ms:.0f}ms | url={page.url}"
            )
        except Exception as e:
            log_error(
                f"❌ {_LOGIN_PREFIX}:browser:{tag}] goto | "
                f"{type(e).__name__}: {e}"
            )
            return False

        user_input = await _input_async(
            f"\n➡️ بعد از ورود کامل به {platform_label}، "
            f"اینجا Enter بزن (timeout={_INPUT_TIMEOUT_S}s)... "
        )
        if user_input is None:
            log_warning(
                f"⚠️ {_LOGIN_PREFIX}:browser:{tag}] "
                f"لغو/تایم‌اوت توسط کاربر"
            )
            return False

        collector._page = page

        await asyncio.sleep(2)

        try:
            logged_in = await collector._is_logged_in()
        except Exception as e:
            log_warning(
                f"⚠️ {_LOGIN_PREFIX}:browser:{tag}] is_logged_in | "
                f"{type(e).__name__}: {e} — "
                f"fallback به _connected"
            )
            logged_in = bool(getattr(collector, "_connected", False))

        if logged_in:
            log_success(
                f"✅ {_LOGIN_PREFIX}:browser:{tag}] "
                f"ورود تشخیص داده شد"
            )

            if hasattr(collector, "_export_state"):
                try:
                    await collector._export_state()
                    log_info("   ✔ state ذخیره شد")
                except Exception as e:
                    log_warning(
                        f"   ⚠️ export_state | "
                        f"{type(e).__name__}: {e}"
                    )
        else:
            log_warning(
                f"⚠️ {_LOGIN_PREFIX}:browser:{tag}] "
                f"ورود تشخیص داده نشد — ولی ممکنه نشست ذخیره شده باشه"
            )
            if hasattr(collector, "dump_dom"):
                try:
                    dom_path = await collector.dump_dom()
                    log_info(f"   📄 DOM ذخیره شد | {dom_path}")
                except Exception as e:
                    log_warning(
                        f"   ⚠️ dump_dom | {type(e).__name__}: {e}"
                    )

        return logged_in
    finally:
        await _safe_disconnect_async(collector)

        elapsed_ms = (time.monotonic() - t0) * 1000
        if logged_in:
            log_success(
                f"✅ {_LOGIN_PREFIX}:browser:{tag}] کامل | "
                f"{elapsed_ms:.0f}ms"
            )
        else:
            log_warning(
                f"⚠️ {_LOGIN_PREFIX}:browser:{tag}] ناتمام | "
                f"{elapsed_ms:.0f}ms"
            )


# ═══════════════════════════════════════════
# wrappers
# ═══════════════════════════════════════════
async def login_bale() -> bool:
    from collectors.bale import BaleCollector
    return await login_browser(
        "بله", BaleCollector, "https://web.bale.ai/"
    )


async def login_eitaa() -> bool:
    from collectors.eitaa import EitaaCollector
    return await login_browser(
        "ایتا", EitaaCollector, "https://web.eitaa.com/"
    )


async def login_whatsapp() -> bool:
    from collectors.whatsapp import WhatsAppCollector
    return await login_browser(
        "واتساپ",
        WhatsAppCollector,
        "https://web.whatsapp.com/",
    )


async def login_linkedin() -> bool:
    from collectors.linkedin import LinkedInCollector
    return await login_browser(
        "لینکدین",
        LinkedInCollector,
        "https://www.linkedin.com/feed/",
    )


async def login_instagram() -> bool:
    from collectors.instagram import InstagramCollector
    return await login_browser(
        "اینستاگرام",
        InstagramCollector,
        "https://www.instagram.com/",
    )


# ═══════════════════════════════════════════
# منو — 🔴 FIX: [8] → [7] برای اینستاگرام
# ═══════════════════════════════════════════
_MENU = """
╔══════════════════════════════════════════════════════════╗
║        ورود تعاملی پلتفرم‌ها (ساخت نشست)                ║
╠══════════════════════════════════════════════════════════╣
║  [1] روبیکا (Rubika)                                     ║
║  [2] بله (Bale - مرورگر)                                 ║
║  [3] ایتا (Eitaa - مرورگر)                               ║
║  [4] تلگرام (Telegram)                                   ║
║  [5] واتساپ (WhatsApp - مرورگر/QR)                       ║
║  [6] لینکدین (LinkedIn - مرورگر)                         ║
║  [7] اینستاگرام (Instagram - مرورگر)                     ║
║  [9] ورود همه (به ترتیب)                                 ║
║  [0] خروج                                                ║
╚══════════════════════════════════════════════════════════╝
"""


# 🔴 FIX: کلید «8» → «7» برای اینستاگرام
_ACTIONS: dict[str, tuple[str, Callable[[], Awaitable[bool]]]] = {
    "1": ("rubika", login_rubika),
    "2": ("bale", login_bale),
    "3": ("eitaa", login_eitaa),
    "4": ("telegram", login_telegram),
    "5": ("whatsapp", login_whatsapp),
    "6": ("linkedin", login_linkedin),
    "7": ("instagram", login_instagram),
}

_BY_NAME: dict[str, Callable[[], Awaitable[bool]]] = {
    name: func for _key, (name, func) in _ACTIONS.items()
}

_LOGIN_ALL_ORDER = [
    "rubika", "bale", "eitaa", "telegram",
    "whatsapp", "linkedin", "instagram",
]


async def _login_all() -> None:
    """ورود به همه پلتفرم‌ها به ترتیب."""
    log_info(
        f"🎯 {_LOGIN_PREFIX}:all] ورود به "
        f"{len(_LOGIN_ALL_ORDER)} پلتفرم"
    )

    success_count = 0
    fail_count = 0

    for name in _LOGIN_ALL_ORDER:
        func = _BY_NAME.get(name)
        if not func:
            log_warning(
                f"   ⚠️ {_LOGIN_PREFIX}:all] «{name}» تابع ندارد — skip"
            )
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
            log_error(
                f"   ❌ {_LOGIN_PREFIX}:all] {name} | "
                f"{type(e).__name__}: {e}"
            )

    log_info(
        f"🏁 {_LOGIN_PREFIX}:all] کامل | "
        f"✅{success_count} ⚠️{fail_count}"
    )


async def main() -> None:
    log_info(f"▶️ {_LOGIN_PREFIX}:main] شروع")

    # 🔴 NEW: هشدار اولیه اگر ربات روشن است
    running, pid = _is_bot_running()
    if running:
        print()
        print("╔" + "═" * 58 + "╗")
        print("║  ⚠️  توجه: ربات الان روشن است (PID: {:>6})         ║".format(pid or 0))
        print("║  برای لاگین مرورگری، ابتدا ربات را Stop کن.          ║")
        print("╚" + "═" * 58 + "╝")

    while True:
        print(_MENU)

        try:
            choice = await _input_async("انتخاب: ", timeout_s=300)
        except KeyboardInterrupt:
            log_info(
                f"ℹ️ {_LOGIN_PREFIX}:main] KeyboardInterrupt در منو"
            )
            return

        if choice is None:
            log_warning(f"⚠️ {_LOGIN_PREFIX}:main] لغو در منو")
            return

        choice = choice.strip()

        if choice == "0" or choice == "":
            log_info(f"ℹ️ {_LOGIN_PREFIX}:main] خروج")
            return

        if choice == "9":
            log_info(f"🎯 {_LOGIN_PREFIX}:main] ورود همه")
            await _login_all()
            continue

        action = _ACTIONS.get(choice)
        if not action:
            log_warning(
                f"⚠️ {_LOGIN_PREFIX}:main] "
                f"انتخاب نامعتبر: {choice!r}"
            )
            continue

        platform, func = action
        log_info(f"🎯 {_LOGIN_PREFIX}:main] platform={platform}")

        t0 = time.monotonic()
        try:
            ok = await func()
            elapsed_ms = (time.monotonic() - t0) * 1000
            if ok:
                log_success(
                    f"✅ {_LOGIN_PREFIX}:main] {platform} | "
                    f"success | {elapsed_ms:.0f}ms"
                )
            else:
                log_warning(
                    f"⚠️ {_LOGIN_PREFIX}:main] {platform} | "
                    f"failed/incomplete | {elapsed_ms:.0f}ms"
                )
        except Exception as e:
            log_error(
                f"❌ {_LOGIN_PREFIX}:main] {platform} | "
                f"{type(e).__name__}: {e}"
            )
            import traceback
            for ln in traceback.format_exc().splitlines()[-5:]:
                log_error(f"   ↳ {ln}")

        try:
            cont = await _input_async(
                "\nمی‌خواهی پلتفرم دیگری هم وارد شوی؟ "
                "(Enter = بله، q = خروج): ",
                timeout_s=60,
            )
        except KeyboardInterrupt:
            log_info(
                f"ℹ️ {_LOGIN_PREFIX}:main] KeyboardInterrupt — خروج"
            )
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
        log_error(
            f"❌ {_LOGIN_PREFIX}:fatal] "
            f"{type(e).__name__}: {e}"
        )
        sys.exit(1)