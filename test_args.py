import asyncio
import os
from playwright.async_api import async_playwright


PROFILE = r"F:\payesh plat\storage\sessions\bale_web"


async def test_case(name, **kwargs):
    print(f"\n{'='*60}")
    print(f"🧪 تست: {name}")
    print(f"{'='*60}")
    pw = await async_playwright().start()
    try:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE,
            headless=False,
            **kwargs,
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://example.com")
        title = await page.title()
        print(f"✅ موفق | title: {title}")
        await ctx.close()
        return True
    except Exception as e:
        print(f"❌ خطا: {str(e)[:200]}")
        return False
    finally:
        await pw.stop()
        await asyncio.sleep(2)


async def main():
    # ۱. فقط channel=chrome (پایه)
    await test_case("فقط channel=chrome", channel="chrome")

    # ۲. با viewport
    await test_case(
        "با viewport",
        channel="chrome",
        viewport={"width": 1366, "height": 768},
    )

    # ۳. با user_agent
    await test_case(
        "با user_agent",
        channel="chrome",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
    )

    # ۴. با args
    await test_case(
        "با args",
        channel="chrome",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-extensions",
            "--no-first-run",
        ],
    )

    # ۵. همه با هم
    await test_case(
        "همه با هم",
        channel="chrome",
        viewport={"width": 1366, "height": 768},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-extensions",
            "--no-first-run",
        ],
    )


if __name__ == "__main__":
    asyncio.run(main())