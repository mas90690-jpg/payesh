import asyncio
import os
from playwright.async_api import async_playwright


async def main():
    profile = r"F:\payesh plat\storage\sessions\bale_web"
    os.makedirs(profile, exist_ok=True)

    pw = await async_playwright().start()

    print("🔄 تلاش برای باز کردن با persistent context...")

    try:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=profile,
            headless=False,
            channel="chrome",
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://example.com")
        title = await page.title()
        print("✅ پروفایل باز شد | title:", title)
        await ctx.close()
    except Exception as e:
        print("❌ خطا:", e)

    await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())