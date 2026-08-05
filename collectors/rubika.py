# collectors/rubika.py
"""
کلکتور روبیکا (Rubika) در حالت «کاربر عادی» (Userbot) با کتابخانه rubpy.
"""
import asyncio
import os
from datetime import datetime
from typing import List, Dict, Any, Optional

from core.base_collector import BaseCollector, ConnectionStatus
from core.logger import log_info, log_success, log_error, log_warning

# ایمپورتِ rubpy در سطح ماژول با محافظ (قابل patch در تست، graceful اگر نصب نبود)
try:
    from rubpy import Client
except ImportError:
    Client = None


class RubikaCollector(BaseCollector):
    def __init__(self, phone: str = None, password: str = None, **kwargs):
        super().__init__()
        self.phone = phone
        self.password = password
        self._client = None
        self._messages_queue: List[Dict[str, Any]] = []
        self._connected = False
        self._running = False
        self._loop_task: Optional[asyncio.Task] = None
        self._chat_cache: Dict[str, Dict[str, Any]] = {}

        from config import STORAGE_DIR

        self._session_dir = os.path.join(STORAGE_DIR, "sessions", "rubika")
        os.makedirs(self._session_dir, exist_ok=True)
        self._session_path = os.path.join(self._session_dir, "rubika_session")
        # پوشه ذخیره عکس/فایل
        self._media_dir = os.path.join(STORAGE_DIR, "media", "rubika")
        os.makedirs(self._media_dir, exist_ok=True)

    @property
    def platform_name(self) -> str:
        return "rubika"

    @property
    def supports_history(self) -> bool:
        return False

    @property
    def supports_websocket(self) -> bool:
        return True

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
            log_info(f"🔄 اتصال به روبیکا (کاربر) با شماره {phone_number}...")

            self._client = Client(name=self._session_path)

            # ثبت هندلر دریافت پیام‌ها
            @self._client.on_message_updates()
            async def _on_message(update):
                print("🔔 پیام روبیکا دریافت شد!")
                try:
                    msg = await self._to_unified(update)
                    if msg:
                        self._messages_queue.append(msg)
                        preview = (msg["text"] or msg["media_type"])[:40]
                        log_success(f"📩 [روبیکا] {msg['chat_name']}: {preview}")
                except Exception as e:
                    log_error(f"خطا در پردازش پیام روبیکا: {e}")

            # شروع کلاینت
            await self._client.start(phone_number=phone_number)

            # اجرای حلقه رویداد در پس‌زمینه (استفاده از run به جای run_until_disconnected)
            self._running = True
            self._loop_task = asyncio.create_task(self._run_loop())

            self._connected = True
            self._set_status(ConnectionStatus.CONNECTED)
            log_success("✅ اتصال به روبیکا با موفقیت برقرار شد")
            return True

        except (EOFError, OSError):
            log_error(
                "نشست روبیکا ساخته نشده. یک‌بار `python login.py` را اجرا کنید "
                "تا با کد پیامکی وارد شوید."
            )
            self._set_error("no_session")
            return False
        except Exception as e:
            log_error(f"خطا در اتصال به روبیکا: {e}")
            self._set_error(str(e))
            return False

    async def _run_loop(self):
        """اجرای حلقه رویداد روبیکا در پس‌زمینه با استفاده از متد run."""
        try:
            await self._client.run()
        except Exception as e:
            if self._running:
                log_error(f"حلقه رویداد روبیکا متوقف شد: {e}")
            else:
                log_info("حلقه رویداد روبیکا به‌طور عادی متوقف شد")

    # ==================================================
    # تبدیل پیام خام rubpy به فرمت یکسان
    # ==================================================
    async def _to_unified(self, update) -> Optional[Dict[str, Any]]:
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

        chat_name, chat_username = await self._resolve_chat(object_guid, update)
        chat_link = self._build_link(chat_username, object_guid, chat_type)

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

        # دانلود واقعی عکس/فایل — فقط اگر متن کلیدواژه داشته باشد.
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
            "sender_name": "",
            "timestamp": datetime.now(),
            "media_type": media_type,
            "file_id": file_id,
            "local_file_path": local_file_path,
            "reply_to": str(getattr(update, "reply_message_id", "") or "") or None,
        }

    async def _download_media(self, update, media_type: str, file_id: str):
        """عکس/فایل پیام روبیکا را دانلود و در storage/media/rubika ذخیره می‌کند."""
        try:
            ext = {
                "photo": ".jpg",
                "video": ".mp4",
                "voice": ".ogg",
                "audio": ".mp3",
                "file": "",
            }.get(media_type, "")
            stamp = file_id or datetime.now().strftime("%Y%m%d%H%M%S%f")
            save_path = os.path.join(self._media_dir, f"rubika_{stamp}{ext}")
            await update.download(save_as=save_path)
            if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                log_success(f"📁 فایل روبیکا ذخیره شد: {os.path.basename(save_path)}")
                return save_path
        except Exception as e:
            log_warning(f"دانلود فایل روبیکا ناموفق بود: {e}")
        return None

    async def _resolve_chat(self, object_guid: Optional[str], update) -> tuple:
        if not object_guid:
            return (getattr(update, "title", None) or "نامشخص", None)

        if object_guid in self._chat_cache:
            c = self._chat_cache[object_guid]
            return (c["name"], c["username"])

        name = getattr(update, "title", None)
        username = None
        try:
            obj = await update.get_object()
            data = obj.to_dict() if hasattr(obj, "to_dict") else {}
            name = (
                data.get("title") or data.get("first_name") or data.get("name") or name
            )
            username = data.get("username")
        except Exception:
            pass

        name = name or str(object_guid)
        self._chat_cache[object_guid] = {"name": name, "username": username}
        return (name, username)

    @staticmethod
    def _build_link(
        username: Optional[str], object_guid: Optional[str], chat_type: str
    ) -> Optional[str]:
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
    async def fetch_messages(self, since: datetime = None) -> List[Dict[str, Any]]:
        if not self._connected:
            return []
        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پیام جدید از روبیکا")
        return new_msgs

    # ==================================================
    # خواندن پیام‌های عقب‌افتاده (آفلاین) — از آخرین باری که روشن بوده
    # ==================================================
    async def fetch_history(self, since: datetime) -> List[Dict[str, Any]]:
        if not self._connected or not self._client:
            return []
        results = []
        try:
            since_ts = int(since.timestamp())
            chats_resp = await self._client.get_chats()
            chats = self._extract_list(chats_resp, "chats")
            for ch in chats:
                guid = self._dig(ch, "object_guid")
                ctype = str(
                    self._dig(ch, "abs_object", "type") or self._dig(ch, "type") or ""
                ).lower()
                if not guid or not any(k in ctype for k in ("group", "channel")):
                    continue
                try:
                    _hl = os.getenv("RUBIKA_HISTORY_LIMIT", "200")
                    msgs_resp = await self._client.get_messages(
                        guid, max_id=None, limit=_hl
                    )
                    for m in self._extract_list(msgs_resp, "messages"):
                        mtime = self._dig(m, "time")
                        try:
                            if mtime and int(mtime) < since_ts:
                                continue
                        except Exception:
                            pass
                        text = self._dig(m, "text") or ""
                        if not text:
                            continue
                        results.append(
                            {
                                "id": str(self._dig(m, "message_id") or ""),
                                "platform": "rubika",
                                "text": text,
                                "chat_id": str(guid),
                                "chat_name": str(guid),
                                "chat_type": (
                                    "channel" if "channel" in ctype else "group"
                                ),
                                "chat_username": None,
                                "chat_link": None,
                                "user_id": str(
                                    self._dig(m, "author_object_guid") or ""
                                ),
                                "sender_name": "",
                                "timestamp": datetime.now(),
                                "media_type": "text",
                                "file_id": None,
                                "local_file_path": None,
                                "reply_to": None,
                            }
                        )
                    await asyncio.sleep(0.3)
                except Exception:
                    continue
        except Exception as e:
            log_info(f"تاریخچهٔ روبیکا در دسترس نبود (دریافت لحظه‌ای فعال است): {e}")
        if results:
            log_success(f"📚 {len(results)} پیام عقب‌افتاده از روبیکا خوانده شد")
        return results

    @staticmethod
    def _extract_list(resp, key: str):
        """از پاسخ rubpy لیست را با کلید مشخص بیرون می‌کشد (دفاعی)."""
        try:
            data = resp.to_dict() if hasattr(resp, "to_dict") else (resp or {})
        except Exception:
            data = {}
        if isinstance(data, dict):
            val = data.get(key)
            if isinstance(val, list):
                return val
            # گاهی یک لایه تودرتو
            for v in data.values():
                if isinstance(v, dict) and isinstance(v.get(key), list):
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
