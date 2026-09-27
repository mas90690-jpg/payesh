import asyncio
from collectors.eitaa import EitaaCollector


async def main():
    c = EitaaCollector(
        headless=False,
        poll_seconds=999,
        max_chats=0,
        messages_per_chat=30,
        backfill_scrolls=0,
    )
    ok = await c.connect()
    print("connect:", ok)
    if not ok:
        return
    msgs = await c.fetch_messages()
    print("=" * 60)
    print("payam ha:", len(msgs))
    print("=" * 60)
    for m in msgs[:15]:
        txt = (m["text"] or "")[:70]
        print(f"[{m['chat_name']}] {m['sender_name']}: {txt}")
    print("=" * 60)
    await c.disconnect()


if __name__ == "__main__":
    asyncio.run(main())