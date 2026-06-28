# -*- coding: utf-8 -*-
"""
اسکریپت ورود تعاملی (یک‌بار اجرا برای هر پلتفرم).

چون ورود کاربر عادی نیاز به «کد پیامکی» یا «اسکن QR» دارد و main.py در پس‌زمینه اجرا
می‌شود، این اسکریپت را یک‌بار به‌صورت دستی اجرا کنید تا نشست (session) ساخته شود.
بعد از آن main.py بی‌صدا از همان نشست استفاده می‌کند.

اجرا:
    python login.py
"""
import os
import asyncio

from config import PLUGINS_CONFIG, SESSIONS_DIR, STORAGE_DIR


# ==================================================
# مسیرهای نشست (هماهنگ با کلکتورها)
# ==================================================
def _rubika_session_path() -> str:
    d = os.path.join(STORAGE_DIR, "sessions", "rubika")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "rubika_session")


def _bale_session_path() -> str:
    p = PLUGINS_CONFIG.get("balecollector", {}).get(
        "session_path", os.path.join(SESSIONS_DIR, "bale", "bale_session")
    )
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


# ==================================================
# روبیکا (rubpy) — ورود با شماره + کد پیامکی
# ==================================================
async def login_rubika():
    cfg = PLUGINS_CONFIG.get("rubikacollector", {})
    phone = (cfg.get("phone") or "").strip()
    if not phone:
        print("❌ RUBIKA_PHONE در .env تنظیم نشده است.")
        return
    if phone.startswith("+98"):
        phone = "0" + phone[3:]
    elif phone.startswith("98"):
        phone = "0" + phone[2:]
    elif not phone.startswith("0"):
        phone = "0" + phone

    try:
        from rubpy import Client
    except ImportError:
        print("❌ rubpy نصب نیست: pip install rubpy")
        return

    print(f"\n🔄 ورود روبیکا با شماره {phone} ...")
    print("➡️  کد پیامکی روبیکا را وقتی پرسید وارد کن.")
    client = Client(name=_rubika_session_path())
    await client.start(phone_number=phone)
    try:
        me = await client.get_me()
    except Exception:
        me = ""
    print(f"✅ ورود روبیکا موفق بود. نشست ساخته شد. {me or ''}")
    await client.disconnect()


# ==================================================
# بله (aiobale) — ورود با شماره + کد پیامکی
# ==================================================
async def login_bale():
    cfg = PLUGINS_CONFIG.get("balecollector", {})
    phone = (cfg.get("phone") or "").strip()
    password = cfg.get("password") or ""
    if not phone:
        print("❌ BALE_PHONE در .env تنظیم نشده است.")
        return

    p = phone.replace("+", "")
    if p.startswith("0"):
        p = p[1:]
    if not p.startswith("98"):
        p = "98" + p
    phone_int = int(p)

    try:
        from aiobale import Client
    except ImportError:
        print("❌ aiobale نصب نیست (فایل whl را نصب کن).")
        return

    print(f"\n🔄 ورود بله با شماره {phone_int} ...")
    client = Client(session_file=_bale_session_path())
    async with client:
        resp = await client.start_phone_auth(phone_number=phone_int)
        tx = getattr(resp, "transaction_hash", None) or getattr(resp, "hash", None)
        if not tx:
            print(f"❌ ارسال کد ناموفق بود: {resp}")
            return
        code = input("➡️  کد پیامکی بله را وارد کن: ").strip()
        result = await client.validate_code(code=code, transaction_hash=tx)
        if "password" in str(type(result)).lower() or getattr(result, "need_password", False):
            if not password or password == "your_password":
                password = input("➡️  رمز دومرحله‌ای بله را وارد کن: ").strip()
            result = await client.validate_password(password=password, transaction_hash=tx)
        print(f"✅ ورود بله موفق بود. نشست ذخیره شد: {_bale_session_path()}")


# ==================================================
# تلگرام (telethon) — ورود با api + شماره + کد
# ==================================================
async def login_telegram():
    cfg = PLUGINS_CONFIG.get("telegramcollector", {})
    api_id = cfg.get("api_id")
    api_hash = cfg.get("api_hash")
    phone = cfg.get("phone")
    if not api_id or not api_hash or not phone:
        print("❌ TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_PHONE در .env تنظیم نشده.")
        return
    try:
        from telethon import TelegramClient
    except ImportError:
        print("❌ telethon نصب نیست: pip install telethon")
        return

    session_path = os.path.join(STORAGE_DIR, "sessions", "telegram", "telegram_session")
    os.makedirs(os.path.dirname(session_path), exist_ok=True)
    print("\n🔄 ورود تلگرام... (کد را وقتی پرسید وارد کن)")
    client = TelegramClient(session_path, int(api_id), api_hash)
    await client.start(phone=phone)
    me = await client.get_me()
    print(f"✅ ورود تلگرام موفق بود: {getattr(me, 'first_name', '')}")
    await client.disconnect()


# ==================================================
# ورود مرورگری مشترک (ایتا / واتساپ)
# ==================================================
async def login_browser(platform_label: str, collector_cls, url: str):
    from playwright.async_api import async_playwright
    collector = collector_cls(headless=False)
    print(f"\n🌐 مرورگر {platform_label} باز می‌شود...")
    collector._pw = await async_playwright().start()
    collector._context = await collector._launch_context(use_headless=False)
    page = collector._context.pages[0] if collector._context.pages else await collector._context.new_page()
    await page.goto(url, wait_until="domcontentloaded")
    await asyncio.get_event_loop().run_in_executor(
        None, input, f"بعد از ورود کامل به {platform_label}، اینجا Enter بزن... "
    )
    collector._page = page
    if await collector._is_logged_in():
        print(f"✅ ورود {platform_label} موفق بود. نشست ذخیره شد.")
    else:
        print(f"⚠️ هنوز ورود تشخیص داده نشد؛ اگر وارد شدی، نشست ذخیره شده است.")
    await collector.disconnect()


async def login_eitaa():
    from collectors.eitaa import EitaaCollector
    await login_browser("ایتا", EitaaCollector, "https://web.eitaa.com/")


async def login_whatsapp():
    from collectors.whatsapp import WhatsAppCollector
    await login_browser("واتساپ", WhatsAppCollector, "https://web.whatsapp.com/")


async def login_linkedin():
    from collectors.linkedin import LinkedInCollector
    await login_browser("لینکدین", LinkedInCollector, "https://www.linkedin.com/login")


# ==================================================
# منو
# ==================================================
async def main():
    print("=" * 60)
    print("  ورود تعاملی پلتفرم‌ها (ساخت نشست)")
    print("=" * 60)
    print("  [1] روبیکا (Rubika)")
    print("  [2] بله (Bale)")
    print("  [3] ایتا (Eitaa - مرورگر)")
    print("  [4] تلگرام (Telegram)")
    print("  [5] واتساپ (WhatsApp - مرورگر/QR)")
    print("  [6] لینکدین (LinkedIn - مرورگر)")
    print("  [0] خروج")
    choice = input("انتخاب: ").strip()

    if choice == "1":
        await login_rubika()
    elif choice == "2":
        await login_bale()
    elif choice == "3":
        await login_eitaa()
    elif choice == "4":
        await login_telegram()
    elif choice == "5":
        await login_whatsapp()
    elif choice == "6":
        await login_linkedin()
    else:
        print("خروج.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nلغو شد.")
