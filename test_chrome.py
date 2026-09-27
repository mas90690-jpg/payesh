import asyncio
from playwright.async_api import async_playwright


async def main():
    pw = await async_playwright().start()

    # 🎯 بدون user_data_dir — پروفایل موقت
    try:
        browser = await pw.chromium.launch(
            headless=False,
            channel="chrome",
        )
        page = await browser.new_page()
        await page.goto("https://example.com")
        title = await page.title()
        print("✅ کروم باز شد | title:", title)
        await browser.close()
    except Exception as e:
        print("❌ خطا:", e)

    await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())