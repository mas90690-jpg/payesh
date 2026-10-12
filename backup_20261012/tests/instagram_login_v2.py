"""
لاگین اینستاگرام با instaharvest-v2 + proxy.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    # ═══ انتخاب کتابخونه ═══
    print("─" * 60)
    print("انتخاب کتابخونه:")
    print("  ۱. instagrapi 3.0.21")
    print("  ۲. instaharvest-v2 1.1.88")
    print("  ۳. aiograpi")
    print("─" * 60)
    choice = input("انتخاب [1-3]: ").strip() or "1"

    # ═══ Proxy ═══
    proxy = os.getenv("INSTAGRAM_PROXY", "").strip()
    if not proxy:
        print()
        print("⚠️ INSTAGRAM_PROXY در .env نیست")
        proxy = input("پورت پروکسی (مثلاً http://127.0.0.1:10809): ").strip()
        if proxy:
            os.environ["INSTAGRAM_PROXY"] = proxy

    print()
    print(f"🔌 Proxy: {proxy or 'بدون پروکسی'}")

    # ═══ username/password ═══
    username = input("Instagram username: ").strip()
    password = input("Instagram password: ").strip()
    if not username or not password:
        print("❌ خالی")
        return

    # ═══ ساخت session file ═══
    session_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "storage", "sessions", "instagram",
    )
    os.makedirs(session_dir, exist_ok=True)
    session_file = os.path.join(session_dir, "session.json")

    # ═══ لاگین ═══
    print()
    print(f"🔑 لاگین به‌عنوان @{username}...")

    if choice == "1":
        # ═══ instagrapi ═══
        from instagrapi import Client

        cl = Client()

        # proxy
        if proxy:
            cl.set_proxy(proxy)

        # delay
        cl.delay_range = [1, 3]

        # session قدیمی
        if os.path.exists(session_file):
            try:
                cl.load_settings(session_file)
                print("✅ session قدیمی لود شد")
            except Exception as e:
                print(f"⚠️ session: {e}")

        try:
            cl.login(username, password)
            cl.dump_settings(session_file)
            print(f"✅ لاگین موفق")
            print(f"   session: {session_file}")
        except Exception as e:
            print(f"❌ {type(e).__name__}: {e}")
            return

        # تست
        try:
            me = cl.account_info()
            print(f"👤 @{me.username} | {me.follower_count} فالوور")
        except Exception as e:
            print(f"⚠️ account_info: {e}")

    elif choice == "2":
        # ═══ instaharvest-v2 ═══
        try:
            from instaharvest_v2 import Client
        except ImportError:
            # امتحان نام‌های دیگه
            try:
                from instaharvest import Client  # type: ignore
            except ImportError:
                print("❌ instaharvest_v2 پیدا نشد")
                return

        cl = Client()

        if proxy:
            try:
                cl.set_proxy(proxy)
            except Exception:
                try:
                    cl.set_proxies({"http": proxy, "https": proxy})
                except Exception:
                    print("⚠️ set_proxy کار نکرد")

        try:
            cl.login(username, password)
            print(f"✅ لاگین موفق")
            try:
                cl.dump_settings(session_file)
            except Exception:
                pass
        except Exception as e:
            print(f"❌ {type(e).__name__}: {e}")
            return

    elif choice == "3":
        # ═══ aiograpi (async) ═══
        import asyncio
        from aiograpi import Client

        async def do_login():
            cl = Client()
            if proxy:
                cl.set_proxy(proxy)
            await cl.login(username, password)
            await cl.dump_settings(session_file)
            return cl

        try:
            cl = asyncio.run(do_login())
            print(f"✅ لاگین موفق")
        except Exception as e:
            print(f"❌ {type(e).__name__}: {e}")
            return

    else:
        print("❌ انتخاب نامعتبر")
        return

    print()
    print("=" * 60)
    print("✅ تمام!")
    print("=" * 60)


if __name__ == "__main__":
    main()