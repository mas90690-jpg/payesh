# collectors/telegram.py
"""
ماژول جمع‌آوری پیام از تلگرام (Telegram)
پشتیبانی از WebSocket و Polling
"""

import asyncio
import os
from datetime import datetime
from typing import List, Dict, Any, Optional
from core.base_collector import BaseCollector, ConnectionStatus
from core.logger import log_info, log_success, log_error, log_warning


class TelegramCollector(BaseCollector):
    """کلکتور پیام‌رسان تلگرام"""

    def __init__(self, api_id: str = None, api_hash: str = None, phone: str = None, **kwargs):
        super().__init__()
        self.api_id = int(api_id) if api_id else None
        self.api_hash = api_hash
        self.phone = phone
        self._client = None
        self._messages_queue = []
        self._listen_task = None
        self._connected = False

        from config import STORAGE_DIR
        self._session_dir = os.path.join(STORAGE_DIR, "sessions", "telegram")
        os.makedirs(self._session_dir, exist_ok=True)
        self._session_path = os.path.join(self._session_dir, "telegram_session")
        self._media_dir = kwargs.get("media_dir", os.path.join(STORAGE_DIR, "media", "telegram"))
        os.makedirs(self._media_dir, exist_ok=True)

    @property
    def platform_name(self) -> str:
        return "telegram"

    @property
    def supports_history(self) -> bool:
        return True

    @property
    def supports_websocket(self) -> bool:
        return True

    async def connect(self) -> bool:
        """اتصال به تلگرام"""
        try:
            if not self.api_id or not self.api_hash or not self.phone:
                log_error("API_ID, API_HASH و شماره تلفن تلگرام در .env تنظیم نشده است")
                return False

            log_info("🔄 اتصال به تلگرام در حالت کاربر عادی...")

            try:
                from telethon import TelegramClient, events
            except ImportError:
                log_error("کتابخانه telethon نصب نیست. لطفاً با 'pip install telethon' نصب کنید.")
                return False

            self._client = TelegramClient(
                self._session_path,
                self.api_id,
                self.api_hash
            )

            @self._client.on(events.NewMessage)
            async def message_handler(event):
                try:
                    msg = await self._process_message(event.message)
                    self._messages_queue.append(msg)
                    chat_name = msg.get('chat_name', 'Unknown')
                    log_success(f"📩 پیام جدید از تلگرام: {chat_name}")
                except Exception as e:
                    log_error(f"خطا در پردازش پیام تلگرام: {e}")

            await self._client.start(phone=self.phone)
            self._connected = True
            self._set_status(ConnectionStatus.CONNECTED)
            log_success("✅ اتصال به تلگرام با موفقیت انجام شد")
            return True

        except Exception as e:
            log_error(f"خطا در اتصال به تلگرام: {e}")
            self._set_error(str(e))
            return False

    async def _process_message(self, message) -> Dict[str, Any]:
        """پردازش پیام خام تلگرام به فرمت استاندارد"""
        try:
            msg_id = str(message.id)
            text = message.text or message.caption or ''

            chat = message.chat
            if chat:
                chat_id = str(chat.id)
                chat_name = chat.title or chat.first_name or "گروه ناشناس"
            else:
                chat_id = ""
                chat_name = "گروه ناشناس"

            # یوزرنیم، لینک و نوع گروه (آدرس دقیق)
            chat_username = getattr(chat, 'username', None) if chat else None
            chat_link = f"https://t.me/{chat_username}" if chat_username else None
            chat_type = "private"
            try:
                if getattr(message, 'is_group', False):
                    chat_type = "group"
                elif getattr(message, 'is_channel', False):
                    chat_type = "channel"
            except Exception:
                pass

            sender = message.sender
            if sender:
                user_id = str(sender.id)
                sender_name = sender.first_name or sender.username or "ناشناس"
            else:
                user_id = "unknown"
                sender_name = "ناشناس"

            timestamp = datetime.fromtimestamp(message.date.timestamp())

            # تشخیص نوع رسانه
            media_type = "text"
            file_id = None

            if message.photo:
                media_type = "photo"
                file_id = str(message.photo.id)
            elif message.video:
                media_type = "video"
                file_id = str(message.video.id)
            elif message.audio:
                media_type = "audio"
                file_id = str(message.audio.id)
            elif message.document:
                media_type = "document"
                file_id = str(message.document.id)
            elif message.voice:
                media_type = "voice"
                file_id = str(message.voice.id)

            # دانلود واقعی عکس/فایل — فقط اگر متن/کپشن کلیدواژه داشته باشد.
            # (قبلاً همهٔ عکس‌ها دانلود می‌شدند؛ حالا فقط رسانهٔ پیام‌های مرتبط.)
            local_file_path = None
            from analyzers.keyword_finder import get_keyword_finder
            if media_type != "text" and get_keyword_finder().has_match(text):
                try:
                    save_base = os.path.join(self._media_dir, f"telegram_{msg_id}")
                    local_file_path = await self._client.download_media(message, file=save_base)
                    if local_file_path:
                        log_success(f"📁 فایل تلگرام ذخیره شد: {os.path.basename(local_file_path)}")
                except Exception as e:
                    log_warning(f"دانلود فایل تلگرام ناموفق بود: {e}")

            return {
                "id": msg_id,
                "platform": "telegram",
                "text": text,
                "chat_id": chat_id,
                "chat_name": chat_name,
                "chat_username": chat_username,
                "chat_link": chat_link,
                "chat_type": chat_type,
                "user_id": user_id,
                "sender_name": sender_name,
                "timestamp": timestamp,
                "media_type": media_type,
                "file_id": file_id,
                "local_file_path": local_file_path,
                "caption": message.caption,
                "reply_to": str(message.reply_to_msg_id) if message.reply_to_msg_id else None
            }
        except Exception as e:
            log_error(f"خطا در پردازش پیام تلگرام: {e}")
            return {
                "id": str(datetime.now().timestamp()),
                "platform": "telegram",
                "text": "",
                "chat_id": "",
                "chat_name": "خطا",
                "user_id": "unknown",
                "sender_name": "unknown",
                "timestamp": datetime.now(),
                "media_type": "text",
                "file_id": None,
                "caption": None,
                "reply_to": None
            }

    async def fetch_messages(self, since: datetime = None) -> List[Dict[str, Any]]:
        """دریافت پیام‌های جدید از صف"""
        if not self._connected:
            return []

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()

        if new_msgs and since:
            filtered = []
            for msg in new_msgs:
                msg_time = msg.get('timestamp')
                if msg_time and msg_time >= since:
                    filtered.append(msg)
            new_msgs = filtered

        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پیام جدید از تلگرام دریافت شد")

        return new_msgs

    async def fetch_history(self, since: datetime) -> List[Dict[str, Any]]:
        """پیام‌های عقب‌افتادهٔ همهٔ گروه‌ها/کانال‌ها را از زمان since می‌خواند."""
        if not self._connected or not self._client:
            return []
        results = []
        try:
            from telethon import errors
            async for dialog in self._client.iter_dialogs():
                if not (getattr(dialog, 'is_group', False) or getattr(dialog, 'is_channel', False)):
                    continue
                try:
                    async for message in self._client.iter_messages(
                        dialog.entity, offset_date=since, reverse=True, limit=1000
                    ):
                        msg = await self._process_message(message)
                        if msg.get('text') or msg.get('media_type') != 'text':
                            results.append(msg)
                    await asyncio.sleep(0.3)  # احترام به محدودیت نرخ
                except errors.FloodWaitError as e:
                    log_warning(f"⏳ FloodWait تلگرام: {e.seconds} ثانیه صبر...")
                    await asyncio.sleep(min(e.seconds, 60))
                except Exception as e:
                    log_warning(f"تاریخچهٔ یک گروه تلگرام رد شد: {e}")
                    continue
        except Exception as e:
            log_error(f"خطا در خواندن تاریخچهٔ تلگرام: {e}")
        if results:
            log_success(f"📚 {len(results)} پیام عقب‌افتاده از تلگرام خوانده شد")
        return results

    async def disconnect(self) -> None:
        """قطع اتصال از تلگرام"""
        if self._client:
            await self._client.disconnect()
        self._connected = False
        self._set_status(ConnectionStatus.DISCONNECTED)
        log_info("🔌 قطع ارتباط از تلگرام")

    async def check_health(self) -> bool:
        return self._connected