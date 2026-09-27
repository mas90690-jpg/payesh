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
    print("✅ صبر ۱۵ ثانیه...")
    await asyncio.sleep(15)
    
    # اسکرول
    for i in range(3):
        await page.mouse.wheel(0, 2000)
        await asyncio.sleep(2)
    
    print("=" * 70)
    print("جستجوی انتخابگرهای جدید:")
    print("=" * 70)
    
    # انتخابگرهای متنوع جدید
    selectors = [
        "div[role='listitem']",
        "li[role='listitem']",
        "div.feed-shared-update-v2",
        "div.occludable-update",
        "div.feed-shared-update-v2--minimal-padding",
        "div.update-components-text",
        "div[data-urn*='activity']",
        "div[data-id*='urn:li']",
        "div.search-results-container li",
        "main ul li",
        "main div.scaffold-finite-scroll__content ul li",
        "div[data-test-id]",
        "article.feed-shared-update-v2",
    ]
    
    for sel in selectors:
        try:
            count = await page.locator(sel).count()
            print(f"  {sel}: {count}")
        except Exception as e:
            print(f"  {sel}: خطا — {str(e)[:50]}")
    
    # بگرد توی DOM دنبال عناصری که حاوی کلمه "Arash" هستن
    print("=" * 70)
    print("چک کلمات کلیدی توی DOM:")
    print("=" * 70)
    
    keywords = ["Arash Sepahvand", "HSE Strategist", "Dear all"]
    for kw in keywords:
        try:
            count = await page.locator(f"text={kw}").count()
            print(f"  text={kw}: {count}")
        except Exception as e:
            print(f"  text={kw}: خطا — {str(e)[:50]}")
    
    # چک کن پست‌ها توی چه تگی هستن
    print("=" * 70)
    print("ساختار درخت DOM (۵۰۰۰ کاراکتر):")
    print("=" * 70)
    try:
        html = await page.content()
        # بخشی از HTML که شامل "Arash" هست رو نشون بده
        idx = html.find("Arash Sepahvand")
        if idx > 0:
            print("HTML اطراف 'Arash Sepahvand' (۱۵۰۰ کاراکتر قبل و بعد):")
            print(html[max(0, idx-800):idx+800])
        else:
            print("کلمه 'Arash' توی HTML پیدا نشد")
            # دنبال "update-components" بگرد
            idx2 = html.find("update-components")
            if idx2 > 0:
                print("HTML اطراف 'update-components':")
                print(html[max(0, idx2-500):idx2+1500])
    except Exception as e:
        print(f"خطا: {e}")
    
    print("=" * 70)
    await asyncio.sleep(10)
    await ctx.close()
    await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())