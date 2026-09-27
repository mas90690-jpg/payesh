import asyncio
from collectors.rubika import RubikaCollector


async def main():
    c = RubikaCollector(poll_seconds=999)
    ok = await c.connect()
    print("connect:", ok)
    if not ok:
        return
    await asyncio.sleep(2)
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