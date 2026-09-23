# collectors/rubika.py
"""
کلکتور روبیکا (Rubika) در حالت «کاربر عادی» (Userbot) با کتابخانه rubpy.

نسخهٔ ۶ (این نسخه):
  - 🐛 FIX: fetch_messages حالا از get_chats + get_messages استفاده می‌کنه
    (چون on_message_updates پیام گروهی رو نمی‌گیره)
  - ✅ صف زنده هنوز کار می‌کنه
  - ✅ fetch_history کامل
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

_MAX_SEEN = 5000


def _get_session_path() -> str:
    from config import PLUGINS_CONFIG, SESSIONS_DIR
    cfg = PLUGINS_CONFIG.get("rubikacollector", {}) or {}
    default_path = os.path.join(SESSIONS_DIR, "rubika_session")
    p = cfg.get("session_path") or default_path
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
        self._seen_ids: set[str] = set()

        from config import STORAGE_DIR
        self._session_path = _get_session_path()
        self._media_dir = os.path.join(STORAGE_DIR, "media", "rubika")
        os.makedirs(self._media_dir, exist_ok=True)
        self._seen_path = os.path.join(STORAGE_DIR, "offset", "rubika_seen.json")
        os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
        self._load_seen()

        log_info(f"🔧 [rubika:init] آماده | session={self._session_path}")

    @property
    def platform_name(self) -> str:
        return "rubika"

    @property
    def supports_history(self) -> bool:
        return True

    @property
    def supports_websocket(self) -> bool:
        return True

    @property
    def poll_interval_seconds(self) -> float:
        return float(self._poll_seconds)

    def _load_seen(self):
        try:
            import json
            with open(self._seen_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(f"♻️ {len(self._seen_ids)} شناسهٔ دیده‌شدهٔ روبیکا بارگذاری شد")
        except Exception:
            self._seen_ids = set()

    def _save_seen(self):
        try:
            import json
            if len(self._seen_ids) > _MAX_SEEN:
                self._seen_ids = set(list(self._seen_ids)[-_MAX_SEEN:])
            with open(self._seen_path, "w", encoding="utf-8") as f:
                json.dump(list(self._seen_ids), f, ensure_ascii=False)
        except Exception as e:
            log_warning(f"ذخیرهٔ ضدتکراری روبیکا ناموفق: {e}")

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

            @self._client.on_message_updates()
            async def _on_message(update):
                try:
                    msg = await self._to_unified(update)
                    if msg:
                        self._messages_queue.append(msg)
                        preview = (msg["text"] or msg["media_type"])[:40]
                        log_success(
                            f"📩 [روبیکا] {msg['chat_name']}: {preview}"
                        )
                except Exception as e:
                    log_error(f"خطا در پردازش پیام روبیکا: {e}")

            await self._client.start(phone_number=phone_number)

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
        try:
            await self._client.run()
        except Exception as e:
            if self._running:
                log_error(f"حلقه رویداد روبیکا متوقف شد: {e}")
            else:
                log_info("حلقه رویداد روبیکا به‌طور عادی متوقف شد")

    @staticmethod
    def _extract_sender_name(update) -> str:
        for attr in ("author_title", "sender_name", "author_name", "from_name"):
            v = getattr(update, attr, None)
            if v and str(v).strip():
                return str(v).strip()[:60]
        return ""

    async def _to_unified(self, update) -> dict[str, Any] | None:
        object_guid = getattr(update, "object_guid", None)
        text = getattr(update, "text", None) or ""

        # 🐛 FIX: پیام‌های بدون متن رو رد کن
        if not text.strip():
            return None

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
            if getattr(update, "is_file_inline", False) or getattr(update, "file_inline", None):
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

        file_id = None
        local_file_path = None
        if media_type != "text":
            file_id = str(getattr(update, "message_id", "") or "")
            from analyzers.keyword_finder import get_keyword_finder
            if get_keyword_finder().has_match(text):
                local_file_path = await self._download_media(update, media_type, file_id)

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
            "reply_to": (str(getattr(update, "reply_message_id", "") or "") or None),
        }

    async def _download_media(self, update, media_type: str, file_id: str):
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

    async def _resolve_chat(self, object_guid: str | None, update) -> tuple:
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
                data.get("title")
                or data.get("first_name")
                or data.get("name")
                or name
            )
            username = data.get("username")
        except Exception:
            pass

        name = name or str(object_guid)
        self._chat_cache[object_guid] = {"name": name, "username": username}
        return (name, username)

    @staticmethod
    def _build_link(username: str | None, object_guid: str | None, chat_type: str) -> str | None:
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

    # ═══════════════════════════════════════════
    # fetch_messages — صف زنده + get_chats fallback
    # ═══════════════════════════════════════════
    async def fetch_messages(
        self, since: datetime = None
    ) -> list[dict[str, Any]]:
        if not self._connected or not self._client:
            return []

        # ۱) صف زنده
        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()

        # ۲) اگه صف خالی بود، از get_chats بخون
        if not new_msgs:
            try:
                new_msgs = await self._fetch_from_chats(since)
            except Exception as e:
                log_warning(
                    f"⚠️ [rubika:fetch] get_chats | "
                    f"{type(e).__name__}: {e}"
                )

        self._save_seen()

        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پیام جدید از روبیکا")
        return new_msgs

    async def _fetch_from_chats(
        self, since: datetime = None
    ) -> list[dict[str, Any]]:
        """خوندن پیام‌های جدید از get_chats + get_messages."""
        if not self._client:
            return []

        results = []
        try:
            _hl = int(os.getenv("RUBIKA_HISTORY_LIMIT", "200"))
            if _hl <= 0:
                _hl = 200
        except (TypeError, ValueError):
            _hl = 200

        try:
            since_ts = int(since.timestamp()) if since else 0

            chats_resp = await self._client.get_chats()
            chats = self._extract_list(chats_resp, "chats")[:30]

            for ch in chats:
                guid = self._dig(ch, "object_guid")
                if not guid:
                    continue

                ctype = str(
                    self._dig(ch, "abs_object", "type")
                    or self._dig(ch, "type")
                    or ""
                ).lower()

                if not any(k in ctype for k in ("group", "channel", "user", "private")):
                    continue

                try:
                    msgs_resp = await self._client.get_messages(
                        guid, max_id=None, limit=min(_hl, 30)
                    )
                    for m in self._extract_list(msgs_resp, "messages"):
                        mtime = self._dig(m, "time")
                        try:
                            if mtime and int(mtime) < since_ts:
                                continue
                        except Exception:
                            pass

                        text = self._dig(m, "text") or ""
                        if not text.strip():
                            continue

                        mid = str(self._dig(m, "message_id") or "")
                        if not mid:
                            continue

                        key = f"rubika:{guid}:{mid}"
                        if key in self._seen_ids:
                            continue
                        self._seen_ids.add(key)

                        results.append({
                            "id": mid,
                            "platform": "rubika",
                            "text": text,
                            "chat_id": str(guid),
                            "chat_name": str(guid),
                            "chat_type": "channel" if "channel" in ctype else "group",
                            "chat_username": None,
                            "chat_link": None,
                            "user_id": str(self._dig(m, "author_object_guid") or ""),
                            "sender_name": str(self._dig(m, "author_title") or ""),
                            "timestamp": datetime.now(),
                            "media_type": "text",
                            "file_id": None,
                            "local_file_path": None,
                            "reply_to": None,
                        })
                    await asyncio.sleep(0.2)
                except Exception:
                    continue

        except Exception as e:
            log_warning(
                f"⚠️ [rubika:_fetch_from_chats] "
                f"{type(e).__name__}: {e}"
            )

        return results

    async def fetch_history(
        self, since: datetime = None
    ) -> list[dict[str, Any]]:
        """fetch_history از get_chats استفاده می‌کنه."""
        return await self.fetch_messages(since)

    @staticmethod
    def _extract_list(resp, key: str):
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
                if isinstance(v, dict) and isinstance(v.get(key), list):
                    return v[key]
        return []

    @staticmethod
    def _dig(obj, *keys):
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