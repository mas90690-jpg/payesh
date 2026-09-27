import asyncio
import os
import shutil


async def main():
    # 🎯 پاک کردن پروفایل قبلی
    profile = r"F:\payesh plat\storage\sessions\bale_web"
    if os.path.exists(profile):
        shutil.rmtree(profile, ignore_errors=True)
        print("✅ پروفایل قبلی پاک شد")

    # حالا با پروفایل تازه تست کن
    from collectors.bale import BaleCollector

    c = BaleCollector(
        headless=False,
        poll_seconds=999,
        max_chats=5,
        messages_per_chat=50,
        backfill_scrolls=3,
    )
    ok = await c.connect()
    print("connect:", ok)
    if not ok:
        return

    await asyncio.sleep(5)
    msgs = await c.fetch_messages()
    print("=" * 60)
    print("payam ha:", len(msgs))
    print("=" * 60)
    for m in msgs[:20]:
        print(f"  [{m['chat_name']}] {m['sender_name']}: {(m['text'] or '')[:60]}")
    print("=" * 60)
    await c.disconnect()


if __name__ == "__main__":
    asyncio.run(main())