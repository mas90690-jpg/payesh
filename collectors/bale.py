# collectors/bale.py
"""کلکتور بله (Bale) در حالت «کاربر عادی» با کتابخانهٔ aiobale."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any

from core.contracts.base_collector import BaseCollector, ConnectionStatus
from core.kernel.logger import log_error, log_info, log_success, log_warning
from core.adapters import messenger_registry

try:
    from aiobale import Client, Dispatcher
    from aiobale.types import Message
except ImportError:
    Client = Dispatcher = Message = None


class BaleCollector(BaseCollector):
    def __init__(self, phone=None, password=None, type="user", **kwargs):
        super().__init__()
        self.type = type
        self.phone = phone
        self.password = password
        self._client = None
        self._dispatcher = None
        self._connected = False
        self._messages_queue = []
        self._listen_task = None
        self._is_running = True
        self._chat_cache = {}

        from config import PLUGINS_CONFIG, STORAGE_DIR

        self._session_path = PLUGINS_CONFIG.get("balecollector", {}).get(
            "session_path",
            os.path.join(STORAGE_DIR, "sessions", "bale", "bale_session"),
        )
        os.makedirs(os.path.dirname(self._session_path), exist_ok=True)
        self._media_dir = os.path.join(STORAGE_DIR, "media", "bale")
        os.makedirs(self._media_dir, exist_ok=True)

    @property
    def platform_name(self) -> str:
        return "bale"

    @property
    def supports_history(self) -> bool:
        return True

    @property
    def supports_websocket(self) -> bool:
        return True

    @property
    def poll_interval_seconds(self) -> float:
        return 2.0

    def _get_extension(self, media_type: str) -> str:
        return {"photo": ".jpg", "video": ".mp4", "audio": ".mp3",
                "voice": ".ogg", "document": ".file", "gif": ".gif"}.get(media_type, ".bin")

    def _get_media_type_from_message(self, message) -> tuple:
        media_type, file_id, caption = "text", None, None
        for field, kind, attr in (
            ("photo", "photo", "file_id"),
            ("video", "video", "file_id"),
            ("audio", "audio", "file_id"),
            ("voice", "voice", "file_id"),
            ("document", "document", "file_id"),
            ("sticker", "sticker", "file_id"),
            ("animation", "gif", "file_id"),
        ):
            try:
                obj = getattr(message, field, None)
                if obj:
                    caption = getattr(message, "caption", None)
                    if field == "photo" and hasattr(obj, "__getitem__") and len(obj) > 0:
                        file_id = getattr(obj[-1], attr, None)
                    else:
                        file_id = getattr(obj, attr, None)
                    return kind, file_id, caption
            except Exception:
                continue
        return media_type, file_id, caption

    async def _download_file(self, file_id: str, media_type: str):
        if not self._client or not file_id:
            return None
        try:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            ext = self._get_extension(media_type)
            fp = os.path.join(self._media_dir, f"{stamp}_{file_id[:20]}{ext}")
            if hasattr(self._client, "download_file"):
                await self._client.download_file(file_id, fp)
            elif hasattr(self._client, "download_media"):
                await self._client.download_media(file_id, fp)
            else:
                return None
            return fp
        except Exception as e:
            log_error(f"خطا در دانلود فایل {file_id}: {e}")
            return None

    def _chat_type_of(self, chat) -> str:
        s = str(getattr(chat, "type", "")).lower()
        if "group" in s:
            return "group"
        if "channel" in s:
            return "channel"
        if "private" in s or "user" in s:
            return "private"
        return "group"

    def _extract_sender_id(self, message) -> str:
        sid = getattr(message, "sender_id", None)
        if sid is None:
            return "unknown"
        inner = getattr(sid, "id", None)
        return str(inner if inner is not None else sid)

    async def _resolve_group(self, chat_id_raw, chat_type: str) -> dict:
        key = str(chat_id_raw)
        if key in self._chat_cache:
            return self._chat_cache[key]
        name, username, link = "گروه", None, None
        try:
            cid = int(chat_id_raw)
            if chat_type in ("group", "channel"):
                full = await self._client.get_full_group(cid)
                grp = getattr(full, "group", None) or full
                name = getattr(grp, "title", None) or name
                username = getattr(grp, "username", None)
                try:
                    link = await self._client.get_group_link(cid)
                except Exception:
                    pass
        except Exception:
            pass
        if username and not link:
            link = f"https://ble.ir/{username}"
        info = {"name": name, "username": username, "link": link}
        self._chat_cache[key] = info
        return info

    async def _build_unified(self, message) -> dict:
        chat = message.chat
        chat_id_raw = getattr(chat, "id", None)
        chat_type = self._chat_type_of(chat)
        group_info = await self._resolve_group(chat_id_raw, chat_type)
        user_id = self._extract_sender_id(message)
        media_type, file_id, caption = self._get_media_type_from_message(message)
        text_content = getattr(message, "text", None) or caption or ""

        local_file_path = None
        if file_id:
            from analyzers.keyword_finder import get_keyword_finder
            if get_keyword_finder().has_match(text_content):
                local_file_path = await self._download_file(file_id, media_type)

        reply_to = getattr(message, "replied_to", None) or getattr(message, "quoted_replied_to", None)
        return {
            "id": str(message.message_id),
            "platform": "bale",
            "text": text_content,
            "chat_id": str(chat_id_raw),
            "chat_name": group_info["name"],
            "chat_type": chat_type,
            "chat_username": group_info["username"],
            "chat_link": group_info["link"],
            "user_id": user_id,
            "sender_name": "",
            "timestamp": datetime.now(),
            "media_type": media_type,
            "file_id": file_id,
            "local_file_path": local_file_path,
            "caption": caption,
            "reply_to": str(reply_to) if reply_to else None,
        }

    async def connect(self) -> bool:
        try:
            if self.type != "user":
                log_error("تنها حالت کاربر پشتیبانی می‌شود")
                return False
            if not self.phone or not self.password:
                log_error("BALE_PHONE و BALE_PASSWORD در .env تنظیم نشده‌اند")
                return False
            if Client is None or Dispatcher is None:
                log_error("کتابخانه aiobale نصب نیست")
                return False

            log_info("🔄 اتصال به بله...")
            phone_number = self.phone
            if phone_number.startswith("0"):
                phone_number = phone_number[1:]

            self._dispatcher = Dispatcher()
            self._client = Client(self._dispatcher, session_file=self._session_path)

            @self._dispatcher.message()
            async def message_handler(message: Message):
                try:
                    msg_data = await self._build_unified(message)
                    self._messages_queue.append(msg_data)
                    log_success(f"📩 [بله] {msg_data['chat_name']}: {(msg_data['text'] or msg_data['media_type'])[:40]}")
                except Exception as e:
                    log_error(f"خطا در پردازش پیام: {e}")

            self._listen_task = asyncio.create_task(self._client.run())
            await asyncio.sleep(5)

            self._connected = True
            self._set_status(ConnectionStatus.CONNECTED)
            messenger_registry.register_sender("bale", self.send_message)
            log_success("✅ اتصال به بله انجام شد")
            return True
        except Exception as e:
            log_error(f"خطا در اتصال: {e}")
            return False

    async def fetch_messages(self, since=None):
        if not self._connected:
            return []
        msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        return msgs

    async def send_message(self, chat_id, chat_type, text: str) -> bool:
        if not self._client or chat_id is None:
            return False
        try:
            from aiobale.enums import ChatType
            ct_map = {"private": ChatType.PRIVATE, "user": ChatType.PRIVATE,
                      "group": ChatType.GROUP, "channel": ChatType.CHANNEL}
            ct = ct_map.get(str(chat_type or "private").lower(), ChatType.PRIVATE)
            await self._client.send_message(text=text, chat_id=int(chat_id), chat_type=ct)
            return True
        except Exception as e:
            log_warning(f"ارسال ناموفق: {e}")
            return False

    async def disconnect(self):
        self._is_running = False
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._client:
            try:
                await self._client.stop()
            except Exception:
                pass
        self._connected = False
        self._set_status(ConnectionStatus.DISCONNECTED)

    async def check_health(self) -> bool:
        return self._connected