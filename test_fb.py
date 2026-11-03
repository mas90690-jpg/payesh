# test_fb.py — تست دقیق فیسبوک با ذخیره DOM
import asyncio
import os
from datetime import datetime
from collectors.facebook import FacebookCollector


async def main():
    c = FacebookCollector(
        headless=False,      # مرورگر باز باشد
        max_posts=3,
        scroll_rounds=4,     # کمتر اسکرول کن تا سریع‌تر باشد
        delay_between=2.0,
    )
    
    print("→ اتصال...")
    ok = await c.connect()
    print("connect:", ok)

    if not ok:
        print("❌ اتصال برقرار نشد.")
        await c.disconnect()
        return

    # 🎯 ذخیره DOM قبل از خواندن فید (برای دیباگ)
    print("📄 ذخیره DOM...")
    dom_path = await c.dump_dom()
    print(f"   ذخیره شد در: {dom_path}")

    print("→ خواندن فید...")
    msgs = await c.fetch_messages()
    print(f"\n📊 نتیجه: {len(msgs)} پست ذخیره شد.")
    
    if msgs:
        for i, m in enumerate(msgs[:5], 1):
            print(f"\n[{i}] {m['chat_name']}")
            print(f"    text: {(m['text'] or '')[:100]}")
            print(f"    link: {m['chat_link']}")
    else:
        print("⚠️ هیچ پستی ذخیره نشد. DOM ذخیره‌شده را بررسی کن.")
        print(f"   مسیر: {dom_path}")

    await c.disconnect()
    print("→ پایان")


if __name__ == "__main__":
    asyncio.run(main())