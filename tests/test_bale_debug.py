"""تست دیباگ — چرا پیام نمیاد."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from core.kernel.logger import log_info, log_success, log_warning, log_error


async def main():
    from baleclient import Client, Dispatcher
    from config import PLUGINS_CONFIG

    session_path = (
        PLUGINS_CONFIG.get("balecollector", {}).get("session_path")
        or "storage/sessions/bale_web/bale_session"
    )
    log_info(f"📁 session: {session_path}")
    log_info(f"📁 exists: {os.path.exists(session_path + '.bale')}")
    log_info(f"📁 exists2: {os.path.exists(session_path)}")

    dp = Dispatcher()

    # ─── handler با لاگ ───
    @dp.message()
    async def on_message(message):
        log_success("=" * 60)
        log_success("🎉 پیام اومد!!!")
        log_success(f"   chat_id: {getattr(message.chat, 'id', '?')}")
        log_success(f"   text: {getattr(message, 'text', '?')}")
        log_success("=" * 60)

    client = Client(dp, session_file=session_path)

    log_info("🔄 start...")
    try:
        await client.start(run_in_background=True, signal_handling=False)
        log_success("✅ start برگشت")
    except Exception as e:
        log_error(f"❌ start خطا: {type(e).__name__}: {e}")
        return

    # چک وضعیت
    log_info(f"🔍 is_connected: {getattr(client, 'is_connected', '?')}")
    log_info(f"🔍 me: {getattr(client, 'me', '?')}")

    # ─── تست ارسال پیام به خودت ───
    log_info("")
    log_info("=" * 60)
    log_info("🧪 تست: دارم یه پیام به خودم میفرستم...")
    log_info("=" * 60)
    try:
        # پیام به Saved Messages
        me = getattr(client, 'me', None)
        if me:
            log_info(f"👤 me.id = {getattr(me, 'id', '?')}")
            log_info(f"👤 me.first_name = {getattr(me, 'first_name', '?')}")
    except Exception as e:
        log_warning(f"⚠️ me error: {e}")

    # ─── صبر برای پیام ───
    log_info("")
    log_info("=" * 60)
    log_info("⏳ ۶۰ ثانیه صبر میکنم")
    log_info("   حالا از گوشیت پیام بفرست به یه گروهی که 09134039935 عضوشه")
    log_info("=" * 60)

    for i in range(60):
        await asyncio.sleep(1)
        if i % 10 == 0 and i > 0:
            log_info(f"   ... {i}s")

    log_info("⏰ زمان تموم شد")
    await client.stop()
    log_info("🔌 قطع شد")


if __name__ == "__main__":
    asyncio.run(main())