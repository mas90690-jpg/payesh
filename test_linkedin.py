import asyncio
from collectors.linkedin import LinkedInCollector


async def main():
    c = LinkedInCollector(
        headless=False,
        poll_seconds=999,
        max_results=5,
        scrolls=1,
        monitor_feed=True,
        max_feed_posts=5,
    )
    ok = await c.connect()
    print("connect:", ok)
    if not ok:
        return
    msgs = await c.fetch_messages()
    print("=" * 60)
    print("payam ha:", len(msgs))
    print("=" * 60)
    for m in msgs[:10]:
        txt = (m["text"] or "")[:80]
        print(f"[{m['sender_name']}] {txt}")
        print(f"   link: {m.get('chat_link')}")
    print("=" * 60)
    await c.disconnect()


if __name__ == "__main__":
    asyncio.run(main())