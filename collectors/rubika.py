# collectors/rubika.py
"""
کلکتور روبیکا (Rubika) در حالت «کاربر عادی» (Userbot) با کتابخانه rubpy.

نسخهٔ ۵ (این نسخه):
  - ✅ منطق دقیقاً مثل v3 قدیم (که کار می‌کرد)
  - ✅ import paths به ساختار جدید
  - 🐛 FIX: مسیر نشست حالا از config میاد (مثل login.py)
      fallback: SESSIONS_DIR/rubika_session
      دلیل: login.py نشست رو در config["rubikacollector"]["session_path"]
      ذخیره می‌کنه. حالا collector هم از همون منبع می‌خونه → هماهنگی کامل.

نسخهٔ ۴ (قبلی):
  - FIX مسیر نشست (SESSIONS_DIR/rubika_session هاردکد شده بود)

نسخهٔ ۳:
  - پشتیبانی از RUBIKA_POLL_SECONDS

نسخهٔ ۲:
  - رفع باگ int() برای RUBIKA_HISTORY_LIMIT
  - استخراج sender_name از author_title
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any

from core.contracts.base_collector import BaseCollector, ConnectionStatus
from core.kernel.logger import log_error, log_info, log_success, log_warning

try:
    from rubpy import Client
except ImportError:
    Client = None


def _get_session_path() -> str:
    """
    مسیر نشست روبیکا — از config (هماهنگ با login.py).

    ترتیب:
      ۱) config["rubikacollector"]["session_path"]
      ۲) fallback: SESSIONS_DIR/rubika_session

    Returns:
        مسیر فایل نشست
    """
    from config import PLUGINS_CONFIG, SESSIONS_DIR

    cfg = PLUGINS_CONFIG.get("rubikacollector", {}) or {}
    default_path = os.path.join(SESSIONS_DIR, "rubika_session")
    p = cfg.get("session_path") or default_path

    # اطمینان از وجود پوشهٔ والد
    try:
        parent = os.path.dirname(p)
        if parent:
            os.makedirs(parent, exist_ok=True)
    except Exception:
        pass

    return p


class RubikaCollector(BaseCollector):
    def __init__(
        self,
        phone: str = None,
        password: str = None,
        poll_seconds: int = 30,
        **kwargs,
    ):
        super().__init__()
        self.phone = phone
        self.password = password
        self._poll_seconds = int(poll_seconds)
        self._client = None
        self._messages_queue: list[dict[str, Any]] = []
        self._connected = False
        self._running = False
        self._loop_task: asyncio.Task | None = None
        self._chat_cache: dict[str, dict[str, Any]] = {}

        # 🐛 FIX: مسیر نشست از config (هماهنگ با login.py)
        from config import STORAGE_DIR
        self._session_path = _get_session_path()
        self._media_dir = os.path.join(STORAGE_DIR, "media", "rubika")
        os.makedirs(self._media_dir, exist_ok=True)

        log_info(
            f"🔧 [rubika:init] آماده | session={self._session_path}"
        )

    @property
    def platform_name(self) -> str:
        return "rubika"

    @property
    def supports_history(self) -> bool:
        return False

    @property
    def supports_websocket(self) -> bool:
        return True

    @property
    def poll_interval_seconds(self) -> float:
        """فاصله بین دو دور دریافت پیام.

        روبیکا WebSocket هست (فقط صف رو تخلیه می‌کنه)، پس ۳۰ ثانیه
        کافیه و از flood/قطع اتصال جلوگیری می‌کنه.
        """
        return float(self._poll_seconds)

    # ==================================================
    # اتصال با حلقه رویداد
    # ==================================================
    async def connect(self) -> bool:
        try:
            if not self.phone:
                log_error("RUBIKA_PHONE در .env تنظیم نشده است")
                return False

            if Client is None:
                log_error("کتابخانه rubpy نصب نیست. نصب: pip install rubpy")
                return False

            phone_number = self._normalize_phone(self.phone)
            log_info(
                f"🔄 اتصال به روبیکا (کاربر) با شماره {phone_number} | "
                f"session={self._session_path}"
            )

            self._client = Client(name=self._session_path)

            # ثبت هندلر دریافت پیام‌ها
            @self._client.on_message_updates()
            async def _on_message(update):
                try:
                    msg = await self._to_unified(update)
                    if msg:
                        self._messages_queue.append(msg)
                        preview = (
                            msg["text"] or msg["media_type"]
                        )[:40]
                        log_success(
                            f"📩 [روبیکا] {msg['chat_name']}: {preview}"
                        )
                except Exception as e:
                    log_error(f"خطا در پردازش پیام روبیکا: {e}")

            # شروع کلاینت
            await self._client.start(phone_number=phone_number)

            # اجرای حلقه رویداد در پس‌زمینه
            self._running = True
            self._loop_task = asyncio.create_task(self._run_loop())

            self._connected = True
            self._set_status(ConnectionStatus.CONNECTED)
            log_success("✅ اتصال به روبیکا با موفقیت برقرار شد")
            return True

        except (EOFError, OSError):
            log_error(
                "نشست روبیکا ساخته نشده. یک‌بار `python login.py` را "
                "اجرا کنید تا با کد پیامکی وارد شوید."
            )
            self._set_error("no_session")
            return False
        except Exception as e:
            log_error(f"خطا در اتصال به روبیکا: {e}")
            self._set_error(str(e))
            return False

    async def _run_loop(self):
        """اجرای حلقه رویداد روبیکا در پس‌زمینه."""
        try:
            await self._client.run()
        except Exception as e:
            if self._running:
                log_error(f"حلقه رویداد روبیکا متوقف شد: {e}")
            else:
                log_info("حلقه رویداد روبیکا به‌طور عادی متوقف شد")

    # ==================================================
    # ابزار کمکی
    # ==================================================
    @staticmethod
    def _extract_sender_name(update) -> str:
        """نام فرستنده از update — rubpy در بعضی نسخه‌ها author_title می‌دهد."""
        for attr in (
            "author_title",
            "sender_name",
            "author_name",
            "from_name",
        ):
            v = getattr(update, attr, None)
            if v and str(v).strip():
                return str(v).strip()[:60]
        return ""

    # ==================================================
    # تبدیل پیام خام به فرمت یکسان
    # ==================================================
    async def _to_unified(self, update) -> dict[str, Any] | None:
        object_guid = getattr(update, "object_guid", None)
        text = getattr(update, "text", None) or ""

        chat_type = "private"
        try:
            if getattr(update, "is_group", False):
                chat_type = "group"
            elif getattr(update, "is_channel", False):
                chat_type = "channel"
        except Exception:
            pass

        chat_name, chat_username = await self._resolve_chat(
            object_guid, update
        )
        chat_link = self._build_link(
            chat_username, object_guid, chat_type
        )

        media_type = "text"
        try:
            if getattr(update, "is_file_inline", False) or getattr(
                update, "file_inline", None
            ):
                media_type = "file"
            elif getattr(update, "photo", None):
                media_type = "photo"
            elif getattr(update, "video", None):
                media_type = "video"
            elif getattr(update, "voice", None):
                media_type = "voice"
            elif getattr(update, "music", None):
                media_type = "audio"
        except Exception:
            pass

        # دانلود واقعی عکس/فایل — فقط اگر متن کلیدواژه داشته باشد
        file_id = None
        local_file_path = None
        if media_type != "text":
            file_id = str(getattr(update, "message_id", "") or "")
            from analyzers.keyword_finder import get_keyword_finder

            if get_keyword_finder().has_match(text):
                local_file_path = await self._download_media(
                    update, media_type, file_id
                )

        return {
            "id": str(getattr(update, "message_id", "") or ""),
            "platform": "rubika",
            "text": text,
            "chat_id": str(object_guid or ""),
            "chat_name": chat_name,
            "chat_type": chat_type,
            "chat_username": chat_username,
            "chat_link": chat_link,
            "user_id": str(getattr(update, "author_guid", "") or ""),
            "sender_name": self._extract_sender_name(update),
            "timestamp": datetime.now(),
            "media_type": media_type,
            "file_id": file_id,
            "local_file_path": local_file_path,
            "reply_to": (
                str(getattr(update, "reply_message_id", "") or "")
                or None
            ),
        }

    async def _download_media(
        self, update, media_type: str, file_id: str
    ):
        """عکس/فایل روبیکا را دانلود و در storage/media/rubika ذخیره می‌کند."""
        try:
            ext = {
                "photo": ".jpg",
                "video": ".mp4",
                "voice": ".ogg",
                "audio": ".mp3",
                "file": "",
            }.get(media_type, "")
            stamp = file_id or datetime.now().strftime(
                "%Y%m%d%H%M%S%f"
            )
            save_path = os.path.join(
                self._media_dir, f"rubika_{stamp}{ext}"
            )
            await update.download(save_as=save_path)
            if (
                os.path.exists(save_path)
                and os.path.getsize(save_path) > 0
            ):
                log_success(
                    f"📁 فایل روبیکا ذخیره شد: "
                    f"{os.path.basename(save_path)}"
                )
                return save_path
        except Exception as e:
            log_warning(f"دانلود فایل روبیکا ناموفق بود: {e}")
        return None

    async def _resolve_chat(
        self, object_guid: str | None, update
    ) -> tuple:
        if not object_guid:
            return (getattr(update, "title", None) or "نامشخص", None)

        if object_guid in self._chat_cache:
            c = self._chat_cache[object_guid]
            return (c["name"], c["username"])

        name = getattr(update, "title", None)
        username = None
        try:
            obj = await update.get_object()
            data = (
                obj.to_dict() if hasattr(obj, "to_dict") else {}
            )
            name = (
                data.get("title")
                or data.get("first_name")
                or data.get("name")
                or name
            )
            username = data.get("username")
        except Exception:
            pass

        name = name or str(object_guid)
        self._chat_cache[object_guid] = {
            "name": name,
            "username": username,
        }
        return (name, username)

    @staticmethod
    def _build_link(
        username: str | None,
        object_guid: str | None,
        chat_type: str,
    ) -> str | None:
        if username:
            return f"https://rubika.ir/{username}"
        return None

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        p = phone.strip()
        if p.startswith("+98"):
            return "0" + p[3:]
        if p.startswith("98"):
            return "0" + p[2:]
        if not p.startswith("0"):
            return "0" + p
        return p

    # ==================================================
    # دریافت پیام‌ها از صف
    # ==================================================
    async def fetch_messages(
        self, since: datetime = None
    ) -> list[dict[str, Any]]:
        if not self._connected:
            return []
        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پیام جدید از روبیکا")
        return new_msgs

    async def fetch_history(
        self, since: datetime = None
    ) -> list[dict[str, Any]]:
        """روبیکا پیام‌ها رو زنده می‌گیره — تاریخچه لازم نیست."""
        return await self.fetch_messages(since)
    # ==================================================
    # خواندن پیام‌های عقب‌افتاده
    # ==================================================
    async def fetch_history(
        self, since: datetime
    ) -> list[dict[str, Any]]:
        if not self._connected or not self._client:
            return []
        results = []

        try:
            _hl = int(os.getenv("RUBIKA_HISTORY_LIMIT", "200"))
            if _hl <= 0:
                _hl = 200
        except (TypeError, ValueError):
            _hl = 200

        try:
            since_ts = int(since.timestamp())
            chats_resp = await self._client.get_chats()
            chats = self._extract_list(chats_resp, "chats")

            for ch in chats:
                guid = self._dig(ch, "object_guid")
                ctype = str(
                    self._dig(ch, "abs_object", "type")
                    or self._dig(ch, "type")
                    or ""
                ).lower()

                if not guid or not any(
                    k in ctype for k in ("group", "channel")
                ):
                    continue

                try:
                    msgs_resp = await self._client.get_messages(
                        guid, max_id=None, limit=_hl
                    )
                    for m in self._extract_list(
                        msgs_resp, "messages"
                    ):
                        mtime = self._dig(m, "time")
                        try:
                            if mtime and int(mtime) < since_ts:
                                continue
                        except Exception:
                            pass

                        text = self._dig(m, "text") or ""
                        if not text:
                            continue

                        results.append({
                            "id": str(
                                self._dig(m, "message_id") or ""
                            ),
                            "platform": "rubika",
                            "text": text,
                            "chat_id": str(guid),
                            "chat_name": str(guid),
                            "chat_type": (
                                "channel"
                                if "channel" in ctype
                                else "group"
                            ),
                            "chat_username": None,
                            "chat_link": None,
                            "user_id": str(
                                self._dig(
                                    m, "author_object_guid"
                                ) or ""
                            ),
                            "sender_name": (
                                self._dig(m, "author_title") or ""
                            ),
                            "timestamp": datetime.now(),
                            "media_type": "text",
                            "file_id": None,
                            "local_file_path": None,
                            "reply_to": None,
                        })

                    await asyncio.sleep(0.3)
                except Exception:
                    continue

        except Exception as e:
            log_info(
                f"تاریخچهٔ روبیکا در دسترس نبود "
                f"(دریافت لحظه‌ای فعال است): {e}"
            )

        if results:
            log_success(
                f"📚 {len(results)} پیام عقب‌افتاده از روبیکا "
                f"خوانده شد"
            )
        return results

    @staticmethod
    def _extract_list(resp, key: str):
        """از پاسخ rubpy لیست را با کلید مشخص بیرون می‌کشد."""
        try:
            data = (
                resp.to_dict()
                if hasattr(resp, "to_dict")
                else (resp or {})
            )
        except Exception:
            data = {}

        if isinstance(data, dict):
            val = data.get(key)
            if isinstance(val, list):
                return val
            for v in data.values():
                if (
                    isinstance(v, dict)
                    and isinstance(v.get(key), list)
                ):
                    return v[key]
        return []

    @staticmethod
    def _dig(obj, *keys):
        """دسترسی تو در تو و امن به دیکشنری."""
        cur = obj
        for k in keys:
            if isinstance(cur, dict):
                cur = cur.get(k)
            else:
                return None
        return cur

    async def disconnect(self) -> None:
        self._running = False
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
        self._connected = False
        self._set_status(ConnectionStatus.DISCONNECTED)
        log_info("🔌 قطع ارتباط از روبیکا")

    async def check_health(self) -> bool:
        return self._connected