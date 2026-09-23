"""تست run() مستقیم — ببین listener راه میفته یا نه."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from baleclient import Client, Dispatcher


async def main():
    dp = Dispatcher()

    @dp.message()
    async def on_msg(m):
        print("=" * 60)
        print("🎉 پیام اومد!!!")
        print(f"   متن: {getattr(m, 'text', '?')}")
        print(f"   chat: {getattr(m.chat, 'id', '?') if hasattr(m, 'chat') else '?'}")
        print("=" * 60)

    c = Client(dp, session_file='storage/sessions/bale_web/bale_session')

    print("=" * 60)
    print("🔄 run() داره اجرا میشه...")
    print("   حالا از گوشیت پیام بفرست توی گروه بله")
    print("   (Ctrl+C برای خروج)")
    print("=" * 60)

    await c.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🔌 خروج")