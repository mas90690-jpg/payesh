import asyncio
import os
from urllib.parse import quote


async def main():
    print("=" * 70)
    print("🧪 تست فقط جستجوی لینکدین")
    print("=" * 70)

    from playwright.async_api import async_playwright

    profile = r"F:\payesh plat\storage\sessions\linkedin_v2"
    os.makedirs(profile, exist_ok=True)

    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        user_data_dir=profile,
        headless=False,
        channel="chrome",
        viewport={"width": 1366, "height": 900},
    )
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()

    # مستقیم برو جستجو
    query = "سپاهان رویش"
    url = (
        "https://www.linkedin.com/search/results/content/"
        f"?keywords={quote(query)}"
        "&origin=GLOBAL_SEARCH_HEADER&sortBy=%22date_posted%22"
    )

    print(f"🔄 رفتن به جستجو: «{query}»")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=90000)
        print(f"✅ باز شد | URL: {page.url}")
    except Exception as e:
        print(f"❌ خطا در باز کردن: {type(e).__name__}: {str(e)[:200]}")
        await ctx.close()
        await pw.stop()
        return

    # صبر کن لود بشه
    print("⏳ صبر ۱۵ ثانیه...")
    await asyncio.sleep(15)

    # چک کن صفحه زنده‌ست
    try:
        title = await page.title()
        print(f"📄 عنوان: {title}")
        current_url = page.url
        print(f"📄 URL: {current_url}")
    except Exception as e:
        print(f"❌ صفحه بسته شد: {e}")
        await ctx.close()
        await pw.stop()
        return

    # اسکرول
    print("📜 اسکرول...")
    for i in range(3):
        try:
            await page.mouse.wheel(0, 1500)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"⚠️ خطا در اسکرول: {e}")

    # چک انتخابگرها یکی یکی با try/except جدا
    print("\n🔍 چک انتخابگرها:")
    selectors = [
        "div[role='listitem']",
        "main ul li",
        "div.feed-shared-update-v2",
        "div.update-components-text",
    ]
    for sel in selectors:
        try:
            count = await page.locator(sel).count()
            print(f"   {sel}: {count}")
        except Exception as e:
            print(f"   {sel}: خطا — {type(e).__name__}")

    # نمونه پست‌ها
    print("\n📝 نمونه پست‌ها:")
    try:
        posts = await page.query_selector_all("div[role='listitem']")
        print(f"   {len(posts)} پست پیدا شد")

        for i, post in enumerate(posts[:3]):
            try:
                text = (await post.inner_text() or "").strip()
                print(f"\n   📌 پست #{i+1} ({len(text)} کاراکتر):")
                print(f"   {text[:200]}")

                from analyzers.keyword_finder import get_keyword_finder
                kw = get_keyword_finder()
                matches = kw.match(text)
                print(f"   🔑 کلیدواژه‌ها: {matches}")
            except Exception as e:
                print(f"   خطا در پست {i}: {e}")
    except Exception as e:
        print(f"   ❌ خطا: {type(e).__name__}: {e}")

    # اسکرین‌شات
    try:
        await page.screenshot(
            path=r"F:\payesh plat\storage\linkedin_search_only.png"
        )
        print("\n✅ اسکرین‌شات ذخیره شد: storage/linkedin_search_only.png")
    except Exception as e:
        print(f"⚠️ اسکرین‌شات: {e}")

    await asyncio.sleep(10)
    await ctx.close()
    await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())