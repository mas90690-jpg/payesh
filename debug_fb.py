# debug_fb.py — بررسی دقیق متن پست‌های فیسبوک
import asyncio
from playwright.async_api import async_playwright

async def main():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        channel="chrome",
        headless=False,
    )
    context = await browser.new_context(
        viewport={"width": 1366, "height": 900},
    )
    page = await context.new_page()
    
    print("→ رفتن به فیسبوک...")
    await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(8)
    
    articles = await page.query_selector_all('div[role="article"]')
    print(f"→ {len(articles)} پست پیدا شد.")
    
    for i, art in enumerate(articles[:3], 1):
        print(f"\n===== پست {i} =====")
        # چاپ متن خام
        raw = await art.inner_text()
        print(f"متن خام: {raw[:200]}...")
        
        # پیدا کردن div های داخل پست
        for sel in (
            'div[data-ad-preview="message"]',
            'div[dir="auto"]',
            'span[dir="auto"]',
            'div[data-ad-comet-preview="message"]',
        ):
            els = await art.query_selector_all(sel)
            if els:
                print(f"سلکتور '{sel}': {len(els)} مورد")
                for j, el in enumerate(els[:2], 1):
                    t = (await el.inner_text() or "").strip()
                    print(f"   {j}. متن: {t[:100]}")
    
    await page.screenshot(path="fb_debug.png")
    print("\n→ اسکرین‌شات در fb_debug.png ذخیره شد.")
    await browser.close()
    await pw.stop()

if __name__ == "__main__":
    asyncio.run(main())