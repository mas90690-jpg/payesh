# collectors/bale_bot.py
"""
کلکتور بله از طریق Bot API — دقیق، پایدار، سریع.

نسخهٔ ۳ (این نسخه):
  - 🎯 NEW: `is_enabled()` classmethod — PluginLoader قبل از ساخت
    instance این را چک می‌کند.
  - 🎯 NEW: `connect()` حالا `send_message` را در messenger_registry
    ثبت می‌کند — ChatOps از Bot API استفاده می‌کند.
  - 🎯 NEW: `disconnect()` حالا sender خود را از registry حذف می‌کند.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from datetime import datetime
from typing import Any

import aiohttp

from analyzers.keyword_finder import get_keyword_finder
from core.contracts.base_collector import BaseCollector, ConnectionStatus
from core.kernel.logger import (
    log_debug,
    log_error,
    log_info,
    log_success,
    log_warning,
)

_PLATFORM = "bale"
_MAX_SEEN = 100000

BALE_API_BASE = "https://tapi.bale.ai/bot{token}/{method}"


class BaleBotCollector(BaseCollector):
    """کلکتور بله با Bot API — polling + send."""

    @classmethod
    def is_enabled(cls) -> bool:
        """آیا این کلکتور باید لود شود؟

        PluginLoader این متد را قبل از ساخت instance صدا می‌زند.
        """
        raw = os.getenv(
            "BALE_BOT_ENABLED", "false"
        ).strip().lower()
        if raw not in ("true", "1", "yes", "on"):
            return False

        token = os.getenv("BALE_BOT_TOKEN", "").strip()
        return bool(token)

    def __init__(
        self,
        bot_token: str | None = None,
        poll_seconds: int = 5,
        long_poll_timeout: int = 25,
        admin_ids: list[str] | None = None,
        **kwargs,
    ):
        super().__init__()

        self.bot_token = (
            bot_token
            or os.getenv("BALE_BOT_TOKEN", "").strip()
        )
        if not self.bot_token:
            raise ValueError(
                "BALE_BOT_TOKEN در .env تنظیم نشده"
            )

        self._poll_seconds = int(poll_seconds)
        self._long_poll_timeout = int(long_poll_timeout)
        self._admin_ids = {
            str(x).strip() for x in (admin_ids or []) if x
        }

        self._session: aiohttp.ClientSession | None = None
        self._connected = False
        self._running = False
        self._loop_task: asyncio.Task | None = None
        self._offset = 0
        self._messages_queue: list[dict[str, Any]] = []
        self._seen_ids: set[str] = set()
        self._kw = get_keyword_finder()

        from config import STORAGE_DIR
        self._seen_path = os.path.join(
            STORAGE_DIR, "offset", "bale_bot_seen.json"
        )
        os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
        self._offset_path = os.path.join(
            STORAGE_DIR, "offset", "bale_bot_offset.json"
        )
        self._load_seen()
        self._load_offset()

        log_info(
            f"🤖 [bale_bot:init] آماده | token=...{self.bot_token[-8:]} | "
            f"admin_ids={len(self._admin_ids)}"
        )

    @property
    def platform_name(self) -> str:
        return _PLATFORM

    @property
    def poll_interval_seconds(self) -> float:
        return float(self._poll_seconds)

    @property
    def supports_history(self) -> bool:
        return False

    @property
    def supports_websocket(self) -> bool:
        return False

    def _load_seen(self):
        try:
            with open(self._seen_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(
                    f"♻️ {len(self._seen_ids)} شناسهٔ دیده‌شدهٔ بله‌ربات"
                )
        except Exception:
            self._seen_ids = set()

    def _save_seen(self):
        try:
            if len(self._seen_ids) > _MAX_SEEN:
                self._seen_ids = set(list(self._seen_ids)[-_MAX_SEEN:])
            with open(self._seen_path, "w", encoding="utf-8") as f:
                json.dump(list(self._seen_ids), f, ensure_ascii=False)
        except Exception as e:
            log_warning(f"ذخیرهٔ seen بله‌ربات: {e}")

    def _load_offset(self):
        try:
            with open(self._offset_path, encoding="utf-8") as f:
                d = json.load(f)
            self._offset = int(d.get("offset", 0))
            if self._offset:
                log_info(f"♻️ offset بله‌ربات: {self._offset}")
        except Exception:
            self._offset = 0

    def _save_offset(self):
        try:
            with open(self._offset_path, "w", encoding="utf-8") as f:
                json.dump({"offset": self._offset}, f)
        except Exception as e:
            log_warning(f"ذخیرهٔ offset: {e}")

    async def connect(self, headless: bool | None = None) -> bool:
        if self._connected:
            return True

        try:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(
                    total=self._long_poll_timeout + 10
                )
            )

            me = await self._call("getMe")
            if not me or not me.get("ok"):
                log_error(
                    f"❌ [bale_bot:connect] getMe ناموفق | "
                    f"response={me}"
                )
                await self.disconnect()
                return False

            bot_info = me.get("result", {})
            log_success(
                f"✅ [bale_bot:connect] ربات تأیید شد | "
                f"id={bot_info.get('id')} | "
                f"username=@{bot_info.get('username', '?')}"
            )

            self._running = True
            self._loop_task = asyncio.create_task(
                self._poll_loop(), name="bale_bot_poll"
            )
            self._connected = True
            self._set_status(ConnectionStatus.CONNECTED)

            try:
                from core.adapters import messenger_registry
                messenger_registry.register_sender(
                    "bale", self.send_message
                )
                log_success(
                    "✅ [bale_bot:connect] در messenger_registry "
                    "ثبت شد — ChatOps از Bot API استفاده می‌کند"
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [bale_bot:connect] ثبت registry | "
                    f"{type(e).__name__}: {e}"
                )

            log_success("✅ اتصال بله‌ربات برقرار شد")
            return True

        except Exception as e:
            log_error(
                f"❌ [bale_bot:connect] {type(e).__name__}: {e}"
            )
            await self.disconnect()
            return False

    async def _call(
        self, method: str, **params
    ) -> dict | None:
        if not self._session:
            return None

        url = BALE_API_BASE.format(
            token=self.bot_token, method=method
        )

        try:
            async with self._session.post(url, json=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    log_warning(
                        f"⚠️ [bale_bot:call:{method}] "
                        f"status={resp.status} | body={text[:200]}"
                    )
                    return None
                return await resp.json()
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            log_warning(
                f"⚠️ [bale_bot:call:{method}] "
                f"{type(e).__name__}: {e}"
            )
            return None

    async def _poll_loop(self):
        log_info("🔄 [bale_bot:poll] شروع long-polling")
        consecutive_errors = 0

        while self._running:
            try:
                data = await self._call(
                    "getUpdates",
                    offset=self._offset,
                    timeout=self._long_poll_timeout,
                    allowed_updates=[
                        "message", "edited_message",
                        "callback_query",
                    ],
                )

                if data is None:
                    continue

                if not data.get("ok"):
                    log_warning(
                        f"⚠️ [bale_bot:poll] ok=false | "
                        f"{data.get('description', '?')}"
                    )
                    consecutive_errors += 1
                    await asyncio.sleep(
                        min(30, 2 ** consecutive_errors)
                    )
                    continue

                consecutive_errors = 0
                updates = data.get("result", []) or []

                for update in updates:
                    try:
                        await self._process_update(update)
                        upd_id = update.get("update_id")
                        if upd_id and upd_id >= self._offset:
                            self._offset = upd_id + 1
                    except Exception as e:
                        log_error(
                            f"❌ [bale_bot:poll] پردازش | "
                            f"{type(e).__name__}: {e}"
                        )

                if updates:
                    self._save_offset()

            except asyncio.CancelledError:
                log_info("🛑 [bale_bot:poll] cancel شد")
                break
            except Exception as e:
                log_error(
                    f"❌ [bale_bot:poll] {type(e).__name__}: {e}"
                )
                await asyncio.sleep(5)

        log_info("🔌 [bale_bot:poll] پایان")

    async def _process_update(self, update: dict):
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return

        chat = msg.get("chat", {}) or {}
        chat_id = str(chat.get("id", ""))
        msg_id = str(msg.get("message_id", ""))
        uniq = f"{chat_id}:{msg_id}"

        if uniq in self._seen_ids:
            log_debug(f"⏭️ [bale_bot:seen] تکراری | {uniq}")
            return
        self._seen_ids.add(uniq)

        text = (msg.get("text") or msg.get("caption") or "").strip()
        if not text:
            return

        chat_type_raw = chat.get("type", "")
        if chat_type_raw == "private":
            chat_type = "private"
        elif chat_type_raw in ("group", "supergroup"):
            chat_type = "group"
        elif chat_type_raw == "channel":
            chat_type = "channel"
        else:
            chat_type = "private"

        chat_name = (
            chat.get("title")
            or (
                f"{chat.get('first_name', '')} "
                f"{chat.get('last_name', '')}"
            ).strip()
            or f"chat_{chat_id}"
        )

        from_user = msg.get("from", {}) or {}
        user_id = str(from_user.get("id", ""))
        sender_name = (
            (
                f"{from_user.get('first_name', '')} "
                f"{from_user.get('last_name', '')}"
            ).strip()
            or from_user.get("username", "")
            or chat_name
        )

        unified = {
            "id": msg_id,
            "platform": _PLATFORM,
            "text": text,
            "chat_id": chat_id,
            "chat_name": chat_name,
            "chat_type": chat_type,
            "chat_username": chat.get("username"),
            "chat_link": None,
            "user_id": user_id,
            "sender_name": sender_name,
            "timestamp": datetime.fromtimestamp(  # noqa: DTZ006
                msg.get("date", 0) or 0
            ) if msg.get("date") else datetime.now(),  # noqa: DTZ005
            "media_type": self._detect_media_type(msg),
            "file_id": None,
            "local_file_path": None,
            "reply_to": None,
            "bot_api": True,
            "raw_update": update,
        }

        if chat_type == "group" and not self._kw.has_match(text):
            log_debug(
                f"⏭️ [bale_bot:kw] بدون کلیدواژه | "
                f"{chat_name} | {text[:40]}"
            )
            return

        self._messages_queue.append(unified)
        log_info(
            f"📩 [bale_bot] {chat_type} | {chat_name} | "
            f"{sender_name} | {text[:50]}"
        )

    @staticmethod
    def _detect_media_type(msg: dict) -> str:
        if msg.get("photo"):
            return "photo"
        if msg.get("video"):
            return "video"
        if msg.get("voice"):
            return "voice"
        if msg.get("audio"):
            return "audio"
        if msg.get("document"):
            return "document"
        if msg.get("sticker"):
            return "sticker"
        return "text"

    async def fetch_messages(
        self, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        if not self._connected:
            return []
        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()
        self._save_seen()
        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پیام از بله‌ربات")
        return new_msgs

    async def send_message(
        self, chat_id, chat_type, text: str
    ) -> bool:
        if not self._session or not text:
            return False

        chunks = self._chunk_text(text, 3900)

        for i, chunk in enumerate(chunks):
            try:
                data = await self._call(
                    "sendMessage",
                    chat_id=chat_id,
                    text=chunk,
                )
                if data and data.get("ok"):
                    log_success(
                        f"📤 [bale_bot:send] chat={chat_id} | "
                        f"chunk={i+1}/{len(chunks)} | "
                        f"len={len(chunk)}"
                    )
                else:
                    log_warning(
                        f"⚠️ [bale_bot:send] ناموفق | "
                        f"response={str(data)[:200]}"
                    )
                    return False
            except Exception as e:
                log_error(
                    f"❌ [bale_bot:send] {type(e).__name__}: {e}"
                )
                return False

        return True

    @staticmethod
    def _chunk_text(text: str, size: int) -> list[str]:
        if not text:
            return [""]
        if len(text) <= size:
            return [text]
        chunks = []
        remaining = text
        while remaining:
            if len(remaining) <= size:
                chunks.append(remaining)
                break
            cut = remaining.rfind("\n", 0, size)
            if cut < size // 2:
                cut = remaining.rfind(" ", 0, size)
            if cut < size // 2:
                cut = size
            chunks.append(remaining[:cut])
            remaining = remaining[cut:].lstrip("\n")
        return chunks

    async def disconnect(self) -> None:
        self._running = False

        try:
            from core.adapters import messenger_registry
            current = messenger_registry.get_sender("bale")
            if current is self.send_message:
                messenger_registry.unregister_sender("bale")
                log_info(
                    "🗑️ [bale_bot:disconnect] از registry حذف شد"
                )
        except Exception:
            pass

        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
        if self._session:
            with contextlib.suppress(Exception):
                await self._session.close()
        self._session = None
        self._connected = False
        self._set_status(ConnectionStatus.DISCONNECTED)
        log_info("🔌 قطع ارتباط از بله‌ربات")

    async def check_health(self) -> bool:
        return self._connected

    async def dump_dom(self, path: str | None = None) -> str:
        return ""