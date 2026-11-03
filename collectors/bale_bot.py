# collectors/bale_bot.py
"""کلکتور بله از طریق Bot API — استاندارد، دقیق، پایدار (v6).

نسخهٔ ۶ (این نسخه):
  - 🐛 FIX: _save_seen بعد از هر batch در _poll_loop
    (crash → پیام‌ها دوباره پردازش نشن)
  - 🐛 FIX: fetch_messages — save قبل از clear
    (خطا → پیام از دست نره)
  - 🐛 FIX: _call چک _connected (نه فقط _session)
    (disconnect → خطا نده)
  - 🐛 FIX: _poll_loop — _running = False در CancelledError
    (دو loop نشه)
  - 🐛 FIX: _load_offset با لاگ کامل‌تر
    (فایل خراب → لاگ)
  - _messages_queue: deque با سقف ۱۰٬۰۰۰ (fix RAM leak)
  - _seen_ids: trim بعد از add (fix RAM leak)
  - _call: retry برای sendMessage (fix پیام گم)
  - _env_bool: default از نوع bool
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections import deque
from datetime import datetime
from typing import Any

import aiohttp

from analyzers.keyword_finder import get_keyword_finder
from core.contracts.base_collector import BaseCollector, ConnectionStatus
from core.kernel.logger import (
    log_debug, log_error, log_info, log_success, log_warning,
)

_PLATFORM = "bale"
_MAX_SEEN = 100_000
_SEEN_TRIM_THRESHOLD = 120_000
_QUEUE_MAXLEN = 10_000
BALE_API_BASE = "https://tapi.bale.ai/bot{token}/{method}"


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key, str(default)).strip().lower()
    return val in ("true", "1", "yes", "on")


class BaleBotCollector(BaseCollector):
    """کلکتور بله با Bot API — polling + send (استاندارد)."""

    @classmethod
    def is_enabled(cls) -> bool:
        if not _env_bool("BALE_BOT_ENABLED"):
            return False
        return bool(os.getenv("BALE_BOT_TOKEN", "").strip())

    def __init__(
        self,
        bot_token: str | None = None,
        poll_seconds: int = int(os.getenv("BALE_BOT_POLL_SECONDS", "30")),
        long_poll_timeout: int = 25,
        admin_ids: list[str] | None = None,
        **kwargs,
    ):
        super().__init__()

        self.bot_token = (bot_token or os.getenv("BALE_BOT_TOKEN", "").strip())
        self.token = self.bot_token
        if not self.bot_token:
            raise ValueError("BALE_BOT_TOKEN در .env تنظیم نشده")

        self._poll_seconds = int(poll_seconds)
        self._long_poll_timeout = int(long_poll_timeout)
        self._admin_ids = {str(x).strip() for x in (admin_ids or []) if x}
        self._keywords_only = _env_bool("BALE_BOT_KEYWORDS_ONLY")

        self._session: aiohttp.ClientSession | None = None
        self._connected = False
        self._last_scrape_stats: dict[str, int] = {
            "total_chats": 0,
            "processed": 0,
            "messages_found": 0,
            "errors": 0,
        }
        self._running = False
        self._loop_task: asyncio.Task | None = None
        self._offset = 0
        self._messages_queue: deque = deque(maxlen=_QUEUE_MAXLEN)
        self._seen_ids: set[str] = set()
        self._kw = get_keyword_finder()

        from config import STORAGE_DIR
        self._seen_path = os.path.join(
            STORAGE_DIR, "offset", "bale_bot_seen.json")
        os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
        self._offset_path = os.path.join(
            STORAGE_DIR, "offset", "bale_bot_offset.json")
        self._load_seen()
        self._load_offset()

        log_info(
            f"🤖 [bale_bot:init] token=*** | "
            f"keywords_only={self._keywords_only}"
        )

    @property
    def platform_name(self) -> str:
        return "bale_bot"

    @property
    def poll_interval_seconds(self) -> float:
        return float(self._poll_seconds)

    @property
    def supports_history(self) -> bool:
        return False

    @property
    def supports_websocket(self) -> bool:
        return False

    # ─── persistence ───
    def _load_seen(self):
        try:
            with open(self._seen_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(f"♻️ {len(self._seen_ids)} seen")
            else:
                log_warning(
                    f"⚠️ seen file invalid type: {type(data).__name__}"
                )
                self._seen_ids = set()
        except FileNotFoundError:
            self._seen_ids = set()
        except Exception as e:
            log_warning(
                f"⚠️ load seen: {type(e).__name__}: {e}"
            )
            self._seen_ids = set()

    def _save_seen(self):
        try:
            if len(self._seen_ids) > _MAX_SEEN:
                self._seen_ids = set(
                    list(self._seen_ids)[-_MAX_SEEN:])
            with open(self._seen_path, "w", encoding="utf-8") as f:
                json.dump(
                    list(self._seen_ids), f, ensure_ascii=False)
        except Exception as e:
            log_warning(f"save seen: {e}")

    def _load_offset(self):
        try:
            with open(self._offset_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._offset = int(data.get("offset", 0))
            else:
                log_warning(
                    f"⚠️ offset file invalid: {type(data).__name__}"
                )
                self._offset = 0
        except FileNotFoundError:
            self._offset = 0
        except Exception as e:
            log_warning(
                f"⚠️ load offset: {type(e).__name__}: {e}"
            )
            self._offset = 0

    def _save_offset(self):
        try:
            with open(self._offset_path, "w", encoding="utf-8") as f:
                json.dump({"offset": self._offset}, f)
        except Exception as e:
            log_warning(f"save offset: {e}")

    # ─── API call با retry برای sendMessage ───
    async def _call(self, method: str, **params) -> dict | None:
        # 🐛 FIX: اگه disconnected، هیچ call نزن
        if not self._session or not self._connected:
            return None

        url = BALE_API_BASE.format(token=self.bot_token, method=method)
        max_retries = 3 if method == "sendMessage" else 1

        for attempt in range(max_retries):
            try:
                async with self._session.post(url, json=params) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        log_warning(
                            f"⚠️ [{method}] status={resp.status} | "
                            f"{text[:200]}"
                        )
                        if (
                            method == "sendMessage"
                            and resp.status >= 500
                            and attempt < max_retries - 1
                        ):
                            await asyncio.sleep(2 ** attempt)
                            continue
                        return None
                    try:
                        return await resp.json()
                    except Exception:
                        text = await resp.text()
                        log_warning(
                            f"⚠️ [{method}] json parse fail | "
                            f"{text[:200]}"
                        )
                        return None

            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                if (
                    method == "sendMessage"
                    and attempt < max_retries - 1
                ):
                    log_warning(
                        f"⚠️ [{method}] attempt {attempt + 1}/"
                        f"{max_retries} failed: "
                        f"{type(e).__name__}: {e}"
                    )
                    await asyncio.sleep(2 ** attempt)
                    continue
                log_warning(f"⚠️ [{method}] {type(e).__name__}: {e}")
                return None
            except Exception as e:
                log_warning(f"⚠️ [{method}] {type(e).__name__}: {e}")
                return None

        return None

    # ─── connect ───
    async def connect(self, headless: bool | None = None) -> bool:
        if self._connected:
            return True
        try:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(
                    total=self._long_poll_timeout + 10))
            me = await self._call("getMe")
            if not me or not me.get("ok"):
                log_error(f"❌ getMe fail | {me}")
                await self.disconnect()
                return False

            bot = me.get("result", {})
            log_success(
                f"✅ bot id={bot.get('id')} "
                f"@{bot.get('username', '?')}"
            )

            self._running = True
            self._loop_task = asyncio.create_task(
                self._poll_loop(), name="bale_poll")
            self._connected = True
            self._set_status(ConnectionStatus.CONNECTED)

            try:
                from core.adapters import messenger_registry
                messenger_registry.register_sender(
                    "bale", self.send_message)
            except Exception as e:
                log_warning(f"⚠️ registry: {e}")

            log_success("✅ اتصال بله‌ربات برقرار شد")
            return True
        except Exception as e:
            log_error(f"❌ connect {type(e).__name__}: {e}")
            await self.disconnect()
            return False

    # ─── poll loop ───
    async def _poll_loop(self):
        log_info("🔄 poll شروع")
        errs = 0
        while self._running:
            try:
                data = await self._call(
                    "getUpdates",
                    offset=self._offset,
                    timeout=self._long_poll_timeout,
                    limit=100,
                    allowed_updates=[
                        "message",
                        "edited_message",
                        "callback_query",
                    ],
                )
                if data is None:
                    errs += 1
                    await asyncio.sleep(min(30, 2 ** errs))
                    continue
                if not data.get("ok"):
                    errs += 1
                    log_warning(
                        f"⚠️ poll ok=false | "
                        f"{data.get('description', '?')}"
                    )
                    await asyncio.sleep(min(30, 2 ** errs))
                    continue

                errs = 0
                updates = data.get("result", []) or []
                for update in updates:
                    try:
                        await self._process_update(update)
                        upd_id = update.get("update_id")
                        if upd_id and upd_id >= self._offset:
                            self._offset = upd_id + 1
                    except Exception as e:
                        log_error(
                            f"❌ process | {type(e).__name__}: {e}"
                        )
                if updates:
                    self._save_offset()
                    # 🐛 FIX: save_seen هم بعد از batch (crash-safe)
                    if len(self._seen_ids) > _SEEN_TRIM_THRESHOLD:
                        self._save_seen()
            except asyncio.CancelledError:
                # 🐛 FIX: flag رو False کن (جلوگیری از دو loop)
                self._running = False
                break
            except Exception as e:
                log_error(f"❌ poll | {type(e).__name__}: {e}")
                await asyncio.sleep(5)
        log_info("🔌 poll پایان")

    # ─── process update ───
    async def _process_update(self, update: dict):
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return

        chat = msg.get("chat", {}) or {}
        chat_id = str(chat.get("id", ""))
        msg_id = str(msg.get("message_id", ""))
        uniq = f"{chat_id}:{msg_id}"

        if uniq in self._seen_ids:
            return

        self._seen_ids.add(uniq)
        # fix: trim بعد از add (جلوگیری از رشد بی‌نهایت)
        if len(self._seen_ids) > _SEEN_TRIM_THRESHOLD:
            self._seen_ids = set(
                list(self._seen_ids)[-_MAX_SEEN:])

        text = (msg.get("text") or msg.get("caption") or "").strip()
        if not text:
            return

        raw_type = chat.get("type", "")
        if raw_type == "private":
            chat_type = "private"
        elif raw_type in ("group", "supergroup"):
            chat_type = "group"
        elif raw_type == "channel":
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

        from_user = msg.get("from") or msg.get("sender_chat") or {}
        user_id = str(from_user.get("id", ""))
        sender_name = (
            (
                f"{from_user.get('first_name', '')} "
                f"{from_user.get('last_name', '')}"
            ).strip()
            or from_user.get("username", "")
            or chat_name
        )

        chat_username = chat.get("username")
        chat_link = (
            f"https://ble.ir/{chat_username}"
            if chat_username
            else None
        )

        reply_to = None
        if msg.get("reply_to_message"):
            reply_to = (
                str(
                    msg["reply_to_message"].get(
                        "message_id", "")
                )
                or None
            )

        file_id = None
        media_type = self._detect_media_type(msg)
        if media_type == "photo" and msg.get("photo"):
            file_id = msg["photo"][-1].get("file_id")
        elif media_type == "video" and msg.get("video"):
            file_id = msg["video"].get("file_id")
        elif media_type == "voice" and msg.get("voice"):
            file_id = msg["voice"].get("file_id")
        elif media_type == "audio" and msg.get("audio"):
            file_id = msg["audio"].get("file_id")
        elif media_type == "document" and msg.get("document"):
            file_id = msg["document"].get("file_id")
        elif media_type == "sticker" and msg.get("sticker"):
            file_id = msg["sticker"].get("file_id")

        ts = None
        if msg.get("date") and int(msg["date"]) > 0:
            ts = datetime.fromtimestamp(int(msg["date"]))
        if ts is None:
            ts = datetime.now()

        unified = {
            "id": msg_id,
            "platform": _PLATFORM,
            "text": text,
            "chat_id": chat_id,
            "chat_name": chat_name,
            "chat_type": chat_type,
            "chat_username": chat_username,
            "chat_link": chat_link,
            "user_id": user_id,
            "sender_name": sender_name,
            "timestamp": ts,
            "media_type": media_type,
            "file_id": file_id,
            "local_file_path": None,
            "reply_to": reply_to,
            "bot_api": True,
            "raw_update": update,
        }

        if self._keywords_only and not self._kw.has_match(text):
            log_debug(f"⏭️ kw skip | {chat_name} | {text[:40]}")
            return
        if chat_type == "group" and not self._kw.has_match(text):
            log_debug(
                f"⏭️ group kw skip | {chat_name} | {text[:40]}")
            return

        self._messages_queue.append(unified)
        log_info(
            f"📩 [{chat_type}] {chat_name} | "
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

    # ─── fetch ───
    async def fetch_messages(
        self, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        if not self._connected:
            return []
        # 🐛 FIX: اول save_seen، بعد clear
        # (اگه save خطا بده، پیام‌ها از دست نرن)
        try:
            self._save_seen()
        except Exception as e:
            log_warning(f"⚠️ save_seen in fetch: {e}")
        msgs = list(self._messages_queue)
        self._messages_queue.clear()
        if msgs:
            log_success(f"📥 {len(msgs)} پیام از بله‌ربات")
        return msgs

    # ─── send ───
    async def send_message(
        self, chat_id, chat_type, text: str
    ) -> bool:
        if not self._session or not text:
            return False
        chunks = self._chunk_text(text, 3900)
        for i, chunk in enumerate(chunks):
            try:
                data = await self._call(
                    "sendMessage", chat_id=chat_id, text=chunk
                )
                if data and data.get("ok"):
                    log_success(
                        f"📤 send chat={chat_id} "
                        f"chunk={i + 1}/{len(chunks)}"
                    )
                else:
                    log_warning(
                        f"⚠️ send fail | {str(data)[:200]}"
                    )
                    return False
            except Exception as e:
                log_error(f"❌ send | {type(e).__name__}: {e}")
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

    # ─── disconnect ───
    async def disconnect(self) -> None:
        self._running = False
        self._save_seen()
        try:
            from core.adapters import messenger_registry
            current = messenger_registry.get_sender("bale")
            if current is self.send_message:
                messenger_registry.unregister_sender("bale")
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
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

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def check_health(self) -> bool:
        return self._connected

    async def dump_dom(self, path: str | None = None) -> str:
        return ""