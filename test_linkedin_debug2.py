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
    
    url = "https://www.linkedin.com/search/results/content/?keywords=%D8%B3%D9%BE%D8%A7%D9%87%D8%A7%D9%86%20%D8%B1%D9%88%DB%8C%D8%B4"
    print("🔄 رفتن به صفحه‌ی جستجو...")
    await page.goto(url, wait_until="domcontentloaded", timeout=90000)
    print("✅ صبر ۱۰ ثانیه...")
    await asyncio.sleep(10)
    
    # اسکرول
    for i in range(3):
        await page.mouse.wheel(0, 2000)
        await asyncio.sleep(2)
    
    # انتخابگرها
    posts = await page.query_selector_all("div[role='listitem']")
    print(f"✅ {len(posts)} پست پیدا شد")
    
    print("=" * 70)
    for i, post in enumerate(posts[:5]):
        print(f"\n📌 پست #{i+1}:")
        try:
            text = (await post.inner_text() or "").strip()
            print(f"   متن ({len(text)} کاراکتر): {text[:300]}")
            
            # چک کن کلیدواژه داره یا نه
            from analyzers.keyword_finder import get_keyword_finder
            kw = get_keyword_finder()
            matches = kw.match(text)
            print(f"   کلیدواژه‌ها: {matches}")
        except Exception as e:
            print(f"   خطا: {e}")
    
    await asyncio.sleep(10)
    await ctx.close()
    await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())