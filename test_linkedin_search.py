import asyncio
from playwright.async_api import async_playwright


async def main():
    profile = r"F:\payesh plat\storage\sessions\linkedin_v2"
    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        user_data_dir=profile,
        headless=False,
        channel="chrome",
        viewport={"width": 1366, "height": 900},
    )
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    
    # برو صفحه‌ی جستجو برای «سپاهان رویش»
    url = "https://www.linkedin.com/search/results/content/?keywords=%D8%B3%D9%BE%D8%A7%D9%87%D8%A7%D9%86%20%D8%B1%D9%88%DB%8C%D8%B4"
    print("🔄 رفتن به صفحه‌ی جستجو...")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=90000)
    except Exception as e:
        print(f"❌ خطا: {str(e)[:200]}")
        await ctx.close()
        await pw.stop()
        return
    
    print("✅ صفحه باز شد. صبر ۱۵ ثانیه...")
    await asyncio.sleep(15)
    
    # اسکرول
    for i in range(5):
        await page.mouse.wheel(0, 2000)
        await asyncio.sleep(2)
    
    print("=" * 60)
    print("URL فعلی:", page.url)
    print("عنوان:", await page.title())
    print("=" * 60)
    print("چک انتخابگرها:")
    print("=" * 60)
    
    selectors = [
        "div.feed-shared-update-v2",
        "div.update-components-text",
        "li.reusable-search__result-container",
        "div.fie-impression-container",
        "div[data-urn]",
        "div[data-chameleon-result-urn]",
        "article",
        "main div.scaffold-finite-scroll__content > div",
        "div.search-results-container > div",
    ]
    
    for sel in selectors:
        try:
            count = await page.locator(sel).count()
            print(f"  {sel}: {count}")
        except Exception as e:
            print(f"  {sel}: خطا — {str(e)[:50]}")
    
    print("=" * 60)
    print("متنی که توی صفحه هست (۵۰۰ کاراکتر اول):")
    try:
        body_text = await page.locator("body").inner_text()
        print(body_text[:500])
    except Exception as e:
        print(f"خطا در خوندن متن: {e}")
    
    print("=" * 60)
    await page.screenshot(path=r"F:\payesh plat\storage\linkedin_search_debug.png", full_page=False)
    print("✅ اسکرین‌شات: storage/linkedin_search_debug.png")
    
    await asyncio.sleep(15)
    await ctx.close()
    await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())