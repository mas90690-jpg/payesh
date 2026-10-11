# tests/instagram_cdp_login.py
"""
لاگین اینستاگرام با CDP — روش دقیق و تست‌شده.

روش کار:
  ۱. Chrome عادی خودت رو با پورت دیباگ باز می‌کنی
  ۲. توی Chrome لاگین اینستاگرام می‌کنی (دستی)
  ۳. Playwright از طریق CDP به همون Chrome وصل می‌شه
  ۴. از session واقعی خودت استفاده می‌کنه

چرا این روش؟
  ✅ اینستاگرام Playwright رو تشخیص نمی‌ده (چون Chrome عادیه)
  ✅ لاگین دستی انجام می‌شه (مثل کاربر واقعی)
  ✅ از TLS stack خود Chrome استفاده می‌کنه (نه curl_cffi)
  ✅ session واقعی حفظ می‌شه

اجرا:
  python tests/instagram_cdp_login.py
  python tests/instagram_cdp_login.py --username natgeo
  python tests/instagram_cdp_login.py --port 9222
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Any

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from playwright.async_api import async_playwright


# ═════════════════════════════════════════════════════════
# ثابت‌ها
# ═════════════════════════════════════════════════════════
_DEFAULT_PORT = 9222
_DEFAULT_USERNAME = "natgeo"

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
    ),
]


# ═════════════════════════════════════════════════════════
# helpers
# ═════════════════════════════════════════════════════════
class C:
    OK = "\033[92m"
    FAIL = "\033[91m"
    WARN = "\033[93m"
    INFO = "\033[96m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    END = "\033[0m"


def _short(s: Any, n: int = 80) -> str:
    if s is None:
        return "None"
    txt = str(s).strip().replace("\n", " ")
    return txt if len(txt) <= n else txt[:n] + "…"


def banner(title: str) -> None:
    print()
    print("=" * 70)
    print(f"🧪 {title}")
    print("=" * 70)


def section(title: str) -> None:
    print()
    print("─" * 70)
    print(f"── {title} " + "─" * max(0, 60 - len(title)))
    print("─" * 70)


def ok(name: str, detail: str = "", elapsed_ms: float = 0):
    t = f" | {elapsed_ms:.0f}ms" if elapsed_ms > 0 else ""
    print(f"  {C.OK}✅{C.END} [{name}] {detail}{t}")


def fail(name: str, detail: str = ""):
    print(f"  {C.FAIL}❌{C.END} [{name}] {detail}")


def warn(name: str, detail: str = ""):
    print(f"  {C.WARN}⚠️{C.END} [{name}] {detail}")


def info(msg: str):
    print(f"  {C.INFO}ℹ️{C.END} {msg}")


def _find_chrome() -> str | None:
    for p in CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


# ═════════════════════════════════════════════════════════
# مرحله ۰: راهنمای قبل از اجرا
# ═════════════════════════════════════════════════════════
def print_pre_instructions(port: int) -> None:
    chrome = _find_chrome() or "chrome.exe"

    print()
    print(f"{C.BOLD}{'=' * 70}{C.END}")
    print(f"{C.BOLD}⚠️  قبل از اجرا — این کارها رو بکن:{C.END}")
    print(f"{C.BOLD}{'=' * 70}{C.END}")
    print()
    print(f"  {C.BOLD}۱.{C.END} همهٔ Chromeها رو ببند:")
    print(f"     {C.DIM}taskkill /F /IM chrome.exe{C.END}")
    print()
    print(f"  {C.BOLD}۲.{C.END} صبر کن ۳ ثانیه.")
    print()
    print(f"  {C.BOLD}۳.{C.END} Chrome رو با پورت دیباگ باز کن:")
    print(f'     {C.DIM}"{chrome}" --remote-debugging-port={port}{C.END}')
    print()
    print(f"  {C.BOLD}۴.{C.END} توی Chrome به اینستاگرام برو:")
    print(f"     {C.DIM}https://www.instagram.com/{C.END}")
    print()
    print(f"  {C.BOLD}۵.{C.END} {C.OK}لاگین کن{C.END} (مثل کاربر عادی).")
    print()
    print(f"  {C.BOLD}۶.{C.END} Chrome رو {C.BOLD}باز بذار{C.END} و این اسکریپت رو اجرا کن.")
    print()
    print(f"{C.DIM}{'─' * 70}{C.END}")


# ═════════════════════════════════════════════════════════
# مرحله ۱: اتصال CDP
# ═════════════════════════════════════════════════════════
async def connect_cdp(pw, port: int):
    section(f"مرحله ۱: اتصال به Chrome (CDP:{port})")

    try:
        t0 = asyncio.get_event_loop().time()
        browser = await pw.chromium.connect_over_cdp(
            f"http://localhost:{port}"
        )
        elapsed = (asyncio.get_event_loop().time() - t0) * 1000
        ok("connect.cdp", f"port={port}", elapsed)
        return browser
    except Exception as e:
        fail("connect.cdp", f"{type(e).__name__}: {e}")
        print()
        print(f"  {C.WARN}راه‌حل:{C.END}")
        print(f"     ۱. Chrome رو ببند: taskkill /F /IM chrome.exe")
        print(f"     ۲. با پورت {port} باز کن")
        print(f"     ۳. دوباره اجرا کن")
        return None


# ═════════════════════════════════════════════════════════
# مرحله ۲: پیدا کردن context و page
# ═════════════════════════════════════════════════════════
async def find_context_and_page(browser):
    section("مرحله ۲: پیدا کردن context و tab اینستاگرام")

    if not browser.contexts:
        fail("context", "هیچ context ای نیست")
        return None, None

    ctx = browser.contexts[0]
    ok("context", f"pages={len(ctx.pages)}")

    # ─── پیدا کردن tab اینستاگرام ───
    page = None
    for p in ctx.pages:
        try:
            url = p.url or ""
            if "instagram.com" in url:
                page = p
                ok("page.instagram", f"url={_short(url, 70)}")
                break
        except Exception:
            continue

    if page is None:
        info("tab اینستاگرام نیست — می‌سازم...")
        page = await ctx.new_page()
        await page.goto(
            "https://www.instagram.com/",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await asyncio.sleep(5)
        ok("page.created", f"url={_short(page.url, 70)}")

    return ctx, page


# ═════════════════════════════════════════════════════════
# مرحله ۳: چک لاگین (چند لایه)
# ═════════════════════════════════════════════════════════
_LOGGEDIN_MARKERS = [
    'svg[aria-label="Home"]',
    'svg[aria-label="خانه"]',
    'a[href="/direct/inbox/"]',
    'a[href="/explore/"]',
    'svg[aria-label="New post"]',
    'svg[aria-label="پست جدید"]',
    'img[alt*="profile picture"]',
    'img[alt*="عکس پروفایل"]',
    'svg[aria-label="Reels"]',
    'svg[aria-label="Search"]',
]

_LOGIN_MARKERS = [
    'input[name="username"]',
    'input[name="password"]',
    'button[type="submit"]',
    'form#loginForm',
]


async def check_login(page) -> bool:
    section("مرحله ۳: چک لاگین (چند لایه)")

    # ─── چک نشانه‌های لاگین نکردن ───
    print(f"  {C.DIM}سلکتورهای Sign-in:{C.END}")
    login_found = False
    for sel in _LOGIN_MARKERS:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                print(f"     {C.FAIL}❌{C.END} {sel}")
                login_found = True
            else:
                print(f"     ➖ {sel}")
        except Exception:
            print(f"     ➖ {sel}")

    if login_found:
        print()
        fail("is_logged_in", "صفحهٔ لاگین — لاگین نکردی")
        return False

    # ─── چک نشانه‌های لاگین ───
    print(f"\n  {C.DIM}سلکتورهای لاگین‌شده:{C.END}")
    for sel in _LOGGEDIN_MARKERS:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                print(f"     {C.OK}✅{C.END} {sel}")
                ok("is_logged_in", f"marker: {sel[:50]}")
                return True
            else:
                print(f"     ➖ {sel}")
        except Exception:
            print(f"     ➖ {sel}")

    warn("is_logged_in", "هیچ marker ای پیدا نشد — وضعیت نامشخص")
    return False


# ═════════════════════════════════════════════════════════
# مرحله ۴: تست API داخلی اینستاگرام
# ═════════════════════════════════════════════════════════
async def test_instagram_api(page, username: str) -> dict | None:
    section(f"مرحله ۴: تست API داخلی اینستاگرام — @{username}")

    # ═══ ۱. گرفتن user_id ═══
    info(f"گرفتن user_id برای @{username}...")

    try:
        user_data = await page.evaluate(
            f"""
            async () => {{
                try {{
                    const r = await fetch(
                        'https://www.instagram.com/api/v1/users/web_profile_info/?username={username}',
                        {{
                            headers: {{ 'X-IG-App-ID': '936619743392459' }},
                            credentials: 'include',
                        }}
                    );
                    if (!r.ok) return {{ error: 'HTTP ' + r.status }};
                    const d = await r.json();
                    return d;
                }} catch (e) {{
                    return {{ error: e.message }};
                }}
            }}
            """
        )

        if "error" in user_data:
            fail("user_info", user_data["error"])
            return None

        user = user_data.get("data", {}).get("user", {})
        user_id = user.get("id")
        full_name = user.get("full_name", "")
        follower_count = user.get("edge_followed_by", {}).get("count", 0)

        if not user_id:
            fail("user_info", "user_id پیدا نشد")
            return None

        ok(
            "user_info",
            f"id={user_id} | full_name={_short(full_name, 30)} | "
            f"followers={follower_count}",
        )

    except Exception as e:
        fail("user_info", f"{type(e).__name__}: {e}")
        return None

    # ═══ ۲. گرفتن Story ═══
    info(f"گرفتن Story های @{username}...")

    try:
        stories_data = await page.evaluate(
            f"""
            async () => {{
                try {{
                    const r = await fetch(
                        'https://www.instagram.com/api/v1/feed/reels_media/?reel_ids={user_id}',
                        {{
                            headers: {{ 'X-IG-App-ID': '936619743392459' }},
                            credentials: 'include',
                        }}
                    );
                    if (!r.ok) return {{ error: 'HTTP ' + r.status }};
                    const d = await r.json();
                    return d;
                }} catch (e) {{
                    return {{ error: e.message }};
                }}
            }}
            """
        )

        if "error" in stories_data:
            warn("stories", stories_data["error"])
            return {"user_id": user_id, "stories": []}

        reels = stories_data.get("reels", {})
        reel = reels.get(str(user_id), {})
        items = reel.get("items", [])

        ok("stories", f"{len(items)} استوری")

        return {
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
            "follower_count": follower_count,
            "stories": items,
        }

    except Exception as e:
        fail("stories", f"{type(e).__name__}: {e}")
        return {"user_id": user_id, "stories": []}


# ═════════════════════════════════════════════════════════
# مرحله ۵: نمایش ساختار Story
# ═════════════════════════════════════════════════════════
def show_stories(result: dict, max_show: int = 5) -> None:
    section(f"مرحله ۵: ساختار {min(max_show, len(result.get('stories', [])))} استوری اول")

    stories = result.get("stories", [])
    if not stories:
        warn("stories", "استوری‌ای نیست")
        return

    for i, item in enumerate(stories[:max_show], 1):
        print(f"\n  {C.BOLD}─── Story {i} ───{C.END}")

        # ─── نوع ───
        media_type = item.get("media_type")
        type_name = {1: "image", 2: "video", 8: "carousel"}.get(
            media_type, f"unknown({media_type})"
        )
        print(f"     {C.DIM}media_type:{C.END} {media_type} ({type_name})")

        # ─── ID ───
        pk = item.get("pk") or item.get("id")
        print(f"     {C.DIM}pk:{C.END} {pk}")

        # ─── زمان ───
        taken_at = item.get("taken_at")
        if taken_at:
            try:
                dt = datetime.fromtimestamp(taken_at)
                print(f"     {C.DIM}taken_at:{C.END} {dt}")
            except Exception:
                print(f"     {C.DIM}taken_at:{C.END} {taken_at}")

        # ─── عکس ───
        img_versions = item.get("image_versions2", {})
        candidates = img_versions.get("candidates", [])
        if candidates:
            url = candidates[0].get("url", "")
            w = candidates[0].get("width", "?")
            h = candidates[0].get("height", "?")
            print(f"     {C.OK}image:{C.END} {w}x{h}")
            print(f"        {_short(url, 90)}")

        # ─── ویدیو ───
        video_versions = item.get("video_versions", [])
        if video_versions:
            url = video_versions[0].get("url", "")
            print(f"     {C.OK}video:{C.END} {_short(url, 90)}")

        # ─── caption ───
        caption = item.get("caption")
        if isinstance(caption, dict):
            text = caption.get("text", "")
            if text:
                print(f"     {C.OK}caption:{C.END} {_short(text, 100)}")

        # ─── متن روی استوری (اگه API داده) ───
        text_metadata = item.get("story_link_stickers") or item.get("text_post_app_info")
        if text_metadata:
            print(f"     {C.DIM}extra:{C.END} {_short(text_metadata, 80)}")


# ═════════════════════════════════════════════════════════
# main
# ═════════════════════════════════════════════════════════
async def main(
    username: str = _DEFAULT_USERNAME,
    port: int = _DEFAULT_PORT,
    skip_prompt: bool = False,
):
    banner("لاگین اینستاگرام با CDP + تست API")

    # ─── راهنما ───
    if not skip_prompt:
        print_pre_instructions(port)
        try:
            input(f"  {C.BOLD}Enter بزن اگه آماده‌ای... {C.END}")
        except (EOFError, KeyboardInterrupt):
            print("\n  لغو شد")
            return False

    # ─── playwright ───
    pw = await async_playwright().start()

    try:
        # ─── ۱. اتصال CDP ───
        browser = await connect_cdp(pw, port)
        if browser is None:
            return False

        # ─── ۲. context و page ───
        ctx, page = await find_context_and_page(browser)
        if page is None:
            return False

        # ─── ۳. چک لاگین ───
        is_logged = await check_login(page)
        if not is_logged:
            print()
            print(f"  {C.FAIL}❌ لاگین نیستی!{C.END}")
            print()
            print(f"  {C.BOLD}راه‌حل:{C.END}")
            print("     ۱. توی Chrome به instagram.com برو")
            print("     ۲. لاگین کن (مثل کاربر عادی)")
            print("     ۳. دوباره این اسکریپت رو اجرا کن")
            return False

        # ─── ۴. تست API ───
        result = await test_instagram_api(page, username)
        if result is None:
            return False

        # ─── ۵. نمایش استوری ───
        show_stories(result)

        # ─── ۶. گزارش نهایی ───
        section("مرحله ۶: گزارش نهایی")

        stories = result.get("stories", [])
        images = sum(
            1 for s in stories if s.get("media_type") == 1
        )
        videos = sum(
            1 for s in stories if s.get("media_type") == 2
        )

        ok("summary.user", f"@{result.get('username')} | id={result.get('user_id')}")
        ok("summary.stories", f"total={len(stories)} | images={images} | videos={videos}")

        print()
        print(f"{C.OK}🎉 همه چیز کار می‌کنه!{C.END}")
        print()
        print(f"{C.BOLD}📋 گام بعدی:{C.END}")
        print("     → ساخت collectors/instagram.py با CDP")
        print("     → اضافه کردن OCR برای خواندن متن از استوری")

        return True

    finally:
        # ⚠️ Chrome رو نمی‌بندیم — باز می‌مونه
        try:
            await pw.stop()
        except Exception:
            pass


# ═════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="تست لاگین اینستاگرام با CDP + API",
    )
    parser.add_argument(
        "--username", type=str, default=_DEFAULT_USERNAME,
        help=f"نام کاربری اینستاگرام (پیش‌فرض: {_DEFAULT_USERNAME})",
    )
    parser.add_argument(
        "--port", type=int, default=_DEFAULT_PORT,
        help=f"پورت CDP (پیش‌فرض: {_DEFAULT_PORT})",
    )
    parser.add_argument(
        "--no-prompt", action="store_true",
        help="بدون پرسیدن Enter",
    )
    args = parser.parse_args()

    ok_result = asyncio.run(
        main(
            username=args.username,
            port=args.port,
            skip_prompt=args.no_prompt,
        )
    )
    sys.exit(0 if ok_result else 1)