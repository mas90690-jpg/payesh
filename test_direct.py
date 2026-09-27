import asyncio
import os


async def main():
    from playwright.async_api import async_playwright

    profile = r"F:\payesh plat\storage\sessions\bale_web"
    os.makedirs(profile, exist_ok=True)

    pw = await async_playwright().start()

    print("🔄 مرحله ۱: launch_persistent_context...")
    try:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=profile,
            headless=False,
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
        print("✅ context ساخته شد")

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        print("✅ page ساخته شد")

        await page.goto("https://web.bale.ai/", wait_until="domcontentloaded")
        print("✅ بله باز شد | URL:", page.url)

        # صبر کن ببینیم چی می‌شه
        await asyncio.sleep(15)

        print("📄 عنوان صفحه:", await page.title())
        print("📄 URL نهایی:", page.url)

        await ctx.close()
        print("✅ context بسته شد")
    except Exception as e:
        print("❌ خطا:", str(e)[:1000])
    finally:
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())