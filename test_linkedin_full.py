import asyncio
import os
from datetime import datetime


async def main():
    print("=" * 70)
    print("🧪 تست کامل لینکدین")
    print("=" * 70)

    # ═══ ۱. تست DNS (مهم‌ترین) ═══
    print("\n📡 مرحله ۱: تست DNS")
    print("-" * 70)
    import socket
    try:
        ip = socket.gethostbyname("www.linkedin.com")
        print(f"✅ DNS حل شد: www.linkedin.com → {ip}")
    except Exception as e:
        print(f"❌ DNS خطا: {e}")
        print("⚠️ اینترنت/DNS مشکل داره. لینکدین بدون این حل نمی‌شه.")
        return

    # ═══ ۲. تست اتصال HTTP ═══
    print("\n📡 مرحله ۲: تست اتصال HTTP")
    print("-" * 70)
    import urllib.request
    try:
        req = urllib.request.Request(
            "https://www.linkedin.com/",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"✅ اتصال HTTP: status={resp.status}")
    except Exception as e:
        print(f"❌ اتصال HTTP خطا: {type(e).__name__}: {e}")

    # ═══ ۳. تست با Playwright ═══
    print("\n🎭 مرحله ۳: تست Playwright + لینکدین")
    print("-" * 70)

    from playwright.async_api import async_playwright

    profile = r"F:\payesh plat\storage\sessions\linkedin_v2"
    os.makedirs(profile, exist_ok=True)

    pw = await async_playwright().start()

    try:
        print("🔄 باز کردن مرورگر...")
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=profile,
            headless=False,
            channel="chrome",
            viewport={"width": 1366, "height": 900},
        )
        print("✅ مرورگر باز شد")

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        print("🔄 رفتن به فید لینکدین...")
        try:
            await page.goto(
                "https://www.linkedin.com/feed/",
                wait_until="domcontentloaded",
                timeout=90000,
            )
            print(f"✅ صفحه باز شد | URL: {page.url}")
        except Exception as e:
            print(f"❌ خطا در باز کردن: {type(e).__name__}: {str(e)[:200]}")
            await ctx.close()
            await pw.stop()
            return

        # صبر کن لود بشه
        print("⏳ صبر ۱۰ ثانیه برای لود...")
        await asyncio.sleep(10)

        # چک لاگین
        print("\n🔐 چک لاگین:")
        url = (page.url or "").lower()
        if "/login" in url or "/authwall" in url:
            print(f"❌ لاگین نیستی | URL: {page.url}")
        elif "linkedin.com" in url:
            print(f"✅ احتمالاً لاگین هستی | URL: {page.url}")
        else:
            print(f"⚠️ وضعیت نامشخص | URL: {page.url}")

        # چک انتخابگرها
        print("\n🔍 چک انتخابگرهای فید:")
        for sel in [
            "#global-nav",
            ".global-nav",
            "div.scaffold-layout",
            "div[role='listitem']",
            "main ul li",
        ]:
            try:
                count = await page.locator(sel).count()
                print(f"   {sel}: {count}")
            except Exception as e:
                print(f"   {sel}: خطا")

        # اسکرول
        print("\n📜 اسکرول...")
        for i in range(3):
            await page.mouse.wheel(0, 1500)
            await asyncio.sleep(2)

        # تعداد پست‌های فید
        posts_count = await page.locator("div[role='listitem']").count()
        print(f"📊 تعداد پست‌های فید: {posts_count}")

        # ═══ ۴. تست جستجو ═══
        print("\n🔎 مرحله ۴: تست جستجو")
        print("-" * 70)

        from urllib.parse import quote
        query = "سپاهان رویش"
        url = (
            "https://www.linkedin.com/search/results/content/"
            f"?keywords={quote(query)}"
            "&origin=GLOBAL_SEARCH_HEADER&sortBy=%22date_posted%22"
        )

        print(f"🔄 جستجو برای: «{query}»")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)
            print(f"✅ صفحه جستجو باز شد")
        except Exception as e:
            print(f"❌ خطا: {type(e).__name__}: {str(e)[:200]}")
            await ctx.close()
            await pw.stop()
            return

        await asyncio.sleep(10)
        for i in range(3):
            await page.mouse.wheel(0, 1500)
            await asyncio.sleep(2)

        # چک انتخابگرهای جستجو
        print("\n🔍 چک انتخابگرهای جستجو:")
        for sel in [
            "div[role='listitem']",
            "main ul li",
            "div.feed-shared-update-v2",
            "div.update-components-text",
            "li.reusable-search__result-container",
        ]:
            try:
                count = await page.locator(sel).count()
                print(f"   {sel}: {count}")
            except Exception as e:
                print(f"   {sel}: خطا")

        # متن پست‌ها
        print("\n📝 نمونه پست‌ها:")
        posts = await page.query_selector_all("div[role='listitem']")
        print(f"   {len(posts)} پست پیدا شد")

        for i, post in enumerate(posts[:3]):
            try:
                text = (await post.inner_text() or "").strip()
                print(f"\n   📌 پست #{i+1} ({len(text)} کاراکتر):")
                print(f"   {text[:200]}...")

                # چک کلیدواژه
                from analyzers.keyword_finder import get_keyword_finder
                kw = get_keyword_finder()
                matches = kw.match(text)
                print(f"   🔑 کلیدواژه‌ها: {matches}")
            except Exception as e:
                print(f"   خطا: {e}")

        # ═══ ۵. تست کلکتور ═══
        print("\n🤖 مرحله ۵: تست کلکتور")
        print("-" * 70)

        await ctx.close()
        await pw.stop()

        from collectors.linkedin import LinkedInCollector

        c = LinkedInCollector(
            headless=False,
            poll_seconds=999,
            max_results=5,
            scrolls=1,
            monitor_feed=True,
            max_feed_posts=5,
        )

        print("🔄 اتصال کلکتور...")
        ok = await c.connect()
        print(f"connect: {ok}")

        if ok:
            print("🔄 fetch_messages...")
            msgs = await c.fetch_messages()
            print(f"📥 تعداد پست: {len(msgs)}")

            for m in msgs[:5]:
                print(f"   [{m['sender_name']}] {(m['text'] or '')[:60]}")

            await c.disconnect()
            print("✅ کلکتور تست شد")

    except Exception as e:
        print(f"❌ خطای کلی: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    finally:
        try:
            await pw.stop()
        except Exception:
            pass

    print("\n" + "=" * 70)
    print("🏁 تست کامل شد")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())