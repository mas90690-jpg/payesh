"""
login.py — اسکریپت ورود تعاملی (یک‌بار اجرا برای هر پلتفرم).

چرا جدا از main.py؟
  ورود کاربر عادی نیاز به «کد پیامکی» یا «اسکن QR» دارد و main.py در پس‌زمینه
  اجرا می‌شود. این اسکریپت را یک‌بار به‌صورت دستی اجرا کن تا نشست (session)
  ساخته شود. بعد main.py بی‌صدا از همان نشست استفاده می‌کند.

اجرا:
    python login.py

نسخهٔ ۶ (این نسخه):
  - 🔴 FIX بزرگ: login_bale() حالا از Playwright استفاده می‌کند، نه baleclient.
    دلیل: collectors/bale.py از Playwright استفاده می‌کند، پس لاگین هم
    باید Playwright باشد. قبلاً login_bale از baleclient استفاده می‌کرد
    که نصب نیست و ناسازگار بود.
  - 🎨 منو: [2] بله (Bale - مرورگر) — واضح‌تر.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۵:
      * _safe_launch_context با inspect.signature
      * _login_all با نگاشت مستقیم
      * disconnect امن
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
from config import PLUGINS_CONFIG, SESSIONS_DIR
from core.kernel.logger import log_error, log_info, log_success, log_warning

_LOGIN_PREFIX = "[LOGIN"

# ─── timeout برای input کاربر (ثانیه) ───
_INPUT_TIMEOUT_S = int(os.getenv("LOGIN_INPUT_TIMEOUT_S", "600"))


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _normalize_phone(phone: str) -> str:
    """نرمال‌سازی شماره به فرمت محلی.

    برای ایران (+98/98 → 0)، بقیه به‌عنوان-is.
    """
    p = (phone or "").strip()
    if not p:
        return ""

    # ایران
    if p.startswith("+98"):
        return "0" + p[3:]
    if p.startswith("0098"):
        return "0" + p[4:]
    if p.startswith("98") and len(p) >= 12:
        # 98912... → 0912...
        return "0" + p[2:]

    # بین‌الملل — دست نمی‌زنیم
    if p.startswith("+"):
        return p

    # محلی ایرانی (بدون ۰) — فقط اگر طول منطقی باشد
    if p.isdigit() and len(p) == 10:
        return "0" + p

    return p


async def _input_async(
    prompt: str, timeout_s: int | None = None
) -> str | None:
    """input غیرمسدودکننده — با timeout اختیاری.

    Returns:
        متن ورودی یا None در timeout/EOF/Cancel.
    """
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
    """
    disconnect ایمن برای کلاینت‌های rubpy/telethon.

    🐛 FIX: برای جلوگیری از نشت منبع در خطا.
    """
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

    🔴 FIX نسخهٔ ۵: به‌جای try/except ترتیبی (که همیشه اولین تلاش
    TypeError می‌داد)، با inspect.signature پارامتر واقعی رو کشف
    می‌کنیم.

    کلکتورهای پروژه use_headless دارن، نه headless. پس نسخهٔ قبلی
    الکی یه خطای TypeError تولید می‌کرد و لاگ رو شلوغ می‌کرد.

    منطق:
      ۱) با inspect.signature دنبال پارامتری که «headless» توش هست
         می‌گردیم (use_headless یا headless).
      ۲) اون پارامتر رو با مقدار headless صدا می‌زنیم.
      ۳) اگه پارامتری پیدا نشد → بدون پارامتر صدا می‌زنیم.
      ۴) اگه signature کار نکرد → fallback به try با نام‌های استاندارد.

    Returns:
        context یا None در خطا.
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

    # ─── کشف نام پارامتر ───
    param_name: str | None = None
    try:
        sig = inspect.signature(launch)
        for p_name in sig.parameters:
            if "headless" in p_name.lower():
                param_name = p_name
                break
    except (ValueError, TypeError) as e:
        # signature قابل خواندن نیست — می‌افتیم روی fallback
        log_info(
            f"   ℹ️ _launch_context signature نامشخص | "
            f"{type(e).__name__}"
        )
    except Exception as e:
        log_warning(
            f"   ⚠️ _launch_context signature | "
            f"{type(e).__name__}: {e}"
        )

    # ─── صدا زدن ───
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
        # بدون پارامتر
        t0 = time.monotonic()
        ctx = await launch()
        elapsed_ms = (time.monotonic() - t0) * 1000
        log_info(
            f"   ✔ _launch_context() بدون پارامتر | "
            f"ctx={'✅' if ctx else '❌'} | {elapsed_ms:.0f}ms"
        )
        return ctx
    except TypeError as e:
        # signature کشف‌شده کار نکرد → fallback
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
# مسیرهای نشست (از config، نه هاردکد)
# ═══════════════════════════════════════════
def _rubika_session_path() -> str:
    """مسیر نشست روبیکا — از config (نه هاردکد)."""
    cfg = PLUGINS_CONFIG.get("rubikacollector", {}) or {}
    default_path = os.path.join(SESSIONS_DIR, "rubika_session")
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
    """مسیر نشست تلگرام — از config."""
    cfg = PLUGINS_CONFIG.get("telegramcollector", {}) or {}
    default_path = os.path.join(
        SESSIONS_DIR, "telegram_session"
    )
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


# ═══════════════════════════════════════════
# روبیکا (rubpy)
# ═══════════════════════════════════════════
async def login_rubika() -> bool:
    """
    ورود روبیکا با شماره + کد پیامکی.

    🔴 FIX: client در finally disconnect می‌شود.
    """
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
# تلگرام (telethon)
# ═══════════════════════════════════════════
async def login_telegram() -> bool:
    """
    ورود تلگرام با API + شماره + کد.

    🔴 FIX: client در finally disconnect می‌شود.
    """
    t0 = time.monotonic()
    log_info(f"▶️ {_LOGIN_PREFIX}:telegram] شروع")

    cfg = PLUGINS_CONFIG.get("telegramcollector", {}) or {}
    api_id_raw = cfg.get("api_id")
    api_hash = cfg.get("api_hash")
    phone = cfg.get("phone")

    missing = [
        name for name, val in (
            ("TELEGRAM_API_ID", api_id_raw),
            ("TELEGRAM_API_HASH", api_hash),
            ("TELEGRAM_PHONE", phone),
        ) if not val
    ]
    if missing:
        log_error(
            f"❌ {_LOGIN_PREFIX}:telegram] تنظیمات ناقص | "
            f"missing={missing}"
        )
        return False

    try:
        api_id = int(str(api_id_raw).strip())
    except (ValueError, TypeError) as e:
        log_error(
            f"❌ {_LOGIN_PREFIX}:telegram] api_id نامعتبر | "
            f"raw={api_id_raw!r} | {type(e).__name__}: {e}"
        )
        return False

    try:
        from telethon import TelegramClient
    except ImportError as e:
        log_error(
            f"❌ {_LOGIN_PREFIX}:telegram] telethon نصب نیست | {e}"
        )
        return False

    session_path = _telegram_session_path()
    log_info(
        f"📱 {_LOGIN_PREFIX}:telegram] api_id={api_id} | "
        f"session={session_path} | منتظر کد..."
    )

    client = None
    me_name = ""
    success = False

    try:
        try:
            client = TelegramClient(session_path, api_id, api_hash)
        except Exception as e:
            log_error(
                f"❌ {_LOGIN_PREFIX}:telegram] ساخت Client | "
                f"{type(e).__name__}: {e}"
            )
            return False

        try:
            await client.start(phone=phone)
        except Exception as e:
            log_error(
                f"❌ {_LOGIN_PREFIX}:telegram] start | "
                f"{type(e).__name__}: {e}"
            )
            return False

        try:
            me = await client.get_me()
            me_name = getattr(me, "first_name", "") or ""
        except Exception as e:
            log_warning(
                f"⚠️ {_LOGIN_PREFIX}:telegram] get_me | "
                f"{type(e).__name__}: {e}"
            )

        success = True
        return True
    finally:
        await _safe_disconnect_client(client, "telegram")

        elapsed_ms = (time.monotonic() - t0) * 1000
        if success:
            log_success(
                f"✅ {_LOGIN_PREFIX}:telegram] ورود موفق | "
                f"name={me_name} | {elapsed_ms:.0f}ms"
            )
        else:
            log_warning(
                f"⚠️ {_LOGIN_PREFIX}:telegram] ناموفق | "
                f"{elapsed_ms:.0f}ms"
            )


# ═══════════════════════════════════════════
# ورود مرورگری مشترک
# ═══════════════════════════════════════════
async def login_browser(
    platform_label: str,
    collector_cls,
    url: str,
) -> bool:
    """
    ورود مرورگری مشترک (ایتا / بله / واتساپ / لینکدین).

    🔴 FIX: context در finally بسته می‌شود (حتی در لغو).
    """
    t0 = time.monotonic()
    tag = platform_label.lower().replace(" ", "_")
    log_info(f"▶️ {_LOGIN_PREFIX}:browser:{tag}] شروع | url={url}")

    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        log_error(
            f"❌ {_LOGIN_PREFIX}:browser:{tag}] "
            f"playwright نصب نیست | {e}"
        )
        return False

    # ساخت collector با headless=False
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
        # راه‌اندازی playwright
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

        # باز کردن صفحه
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

        # انتظار input کاربر
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

        # ─── چک ورود ───
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
    """
    ورود بله با مرورگر (Playwright) — هماهنگ با collectors/bale.py.

    🔴 FIX نسخهٔ ۶: قبلاً از baleclient استفاده می‌کرد که نصب نیست
    و با کلکتور فعلی (Playwright-based) ناسازگار بود.

    حالا مثل ایتا/واتساپ/لینکدین از login_browser استفاده می‌کند.
    مرورگر باز می‌شود، کاربر با شماره + کد پیامکی وارد می‌شود،
    پروفایل در storage/sessions/bale_web ذخیره می‌شود.
    """
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
    # مستقیم به feed (نه login) — بعد از login state ذخیره می‌شود
    return await login_browser(
        "لینکدین",
        LinkedInCollector,
        "https://www.linkedin.com/feed/",
    )


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
║  [4] تلگرام (Telegram)                                   ║
║  [5] واتساپ (WhatsApp - مرورگر/QR)                       ║
║  [6] لینکدین (LinkedIn - مرورگر)                         ║
║  [7] ورود همه (به ترتیب)                                 ║
║  [0] خروج                                                ║
╚══════════════════════════════════════════════════════════╝
"""


_ACTIONS: dict[str, tuple[str, Callable[[], Awaitable[bool]]]] = {
    "1": ("rubika", login_rubika),
    "2": ("bale", login_bale),
    "3": ("eitaa", login_eitaa),
    "4": ("telegram", login_telegram),
    "5": ("whatsapp", login_whatsapp),
    "6": ("linkedin", login_linkedin),
}

# 🎯 FIX: نگاشت نام → تابع (برای _login_all)
_BY_NAME: dict[str, Callable[[], Awaitable[bool]]] = {
    name: func for _key, (name, func) in _ACTIONS.items()
}

# ترتیب ورود در حالت «ورود همه»
_LOGIN_ALL_ORDER = [
    "rubika", "bale", "eitaa", "telegram", "whatsapp", "linkedin",
]


async def _login_all() -> None:
    """ورود به همه پلتفرم‌ها به ترتیب."""
    log_info(f"🎯 {_LOGIN_PREFIX}:all] ورود به {len(_LOGIN_ALL_ORDER)} پلتفرم")

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

    while True:
        print(_MENU)

        try:
            choice = await _input_async("انتخاب: ", timeout_s=300)
        except KeyboardInterrupt:
            log_info(f"ℹ️ {_LOGIN_PREFIX}:main] KeyboardInterrupt در منو")
            return

        if choice is None:
            log_warning(f"⚠️ {_LOGIN_PREFIX}:main] لغو در منو")
            return

        choice = choice.strip()

        if choice == "0" or choice == "":
            log_info(f"ℹ️ {_LOGIN_PREFIX}:main] خروج")
            return

        # ورود همه
        if choice == "7":
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

        # سؤال: ادامه؟
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