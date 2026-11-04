# collectors/rubika.py
"""
کلکتور روبیکا (Rubika) در حالت «کاربر عادی» (Userbot) با کتابخانه rubpy.

نسخهٔ ۷ (این نسخه):
  - 🐛 FIX: fetch_messages حالا از get_chats + get_messages استفاده می‌کند
  - ✅ صف زنده هنوز کار می‌کند
  - ✅ fetch_history کامل
  - 🎯 FIX: لاگ‌های debug حالا log_debug هستند (نه log_info)
    → در LOG_LEVEL=INFO نمایش داده نمی‌شوند
    → در LOG_LEVEL=DEBUG نمایش داده می‌شوند
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any

from core.contracts.base_collector import BaseCollector, ConnectionStatus
from core.kernel.logger import (
    log_debug, log_error, log_info, log_success, log_warning
)

try:
    from rubpy import Client
except ImportError:
    Client = None

_MAX_SEEN = 100000

# 🎯 FIX #5: سقف‌های واقعی از .env — به‌جای مقادیر هاردکد 30
_DEFAULT_HISTORY_LIMIT = 200
_DEFAULT_MAX_CHATS = 30


def _get_history_limit() -> int:
    """RUBIKA_HISTORY_LIMIT را می‌خواند و sanitize می‌کند."""
    try:
        v = int(os.getenv("RUBIKA_HISTORY_LIMIT", str(_DEFAULT_HISTORY_LIMIT)))
        return v if v > 0 else _DEFAULT_HISTORY_LIMIT
    except (TypeError, ValueError):
        return _DEFAULT_HISTORY_LIMIT


def _get_max_chats() -> int:
    """RUBIKA_MAX_CHATS را می‌خواند و sanitize می‌کند (0=بی‌نهایت)."""
    try:
        v = int(os.getenv("RUBIKA_MAX_CHATS", str(_DEFAULT_MAX_CHATS)))
        return v if v >= 0 else _DEFAULT_MAX_CHATS
    except (TypeError, ValueError):
        return _DEFAULT_MAX_CHATS


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
        # 🎯 اگه phone پاس نشد، از config بخون
        if not phone:
            try:
                from config import get_rubika_config
                cfg = get_rubika_config()
                phone = cfg.get("phone", "") or ""
            except Exception:
                phone = ""
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
        self._seen_path = os.path.join(
            STORAGE_DIR, "offset", "rubika_seen.json")
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
                log_info(
                    f"♻️ {len(self._seen_ids)} شناسهٔ دیده‌شدهٔ روبیکا بارگذاری شد")
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
            try:
                await self._preload_chats()
            except Exception as e:
                log_warning(f"⚠️ [rubika:preload] {type(e).__name__}: {e}")

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
        for attr in (
            "author_title",
            "sender_name",
            "author_name",
                "from_name"):
            v = getattr(update, attr, None)
            if v and str(v).strip():
                return str(v).strip()[:60]
        return ""

    async def _to_unified(self, update) -> dict[str, Any] | None:
        object_guid = getattr(update, "object_guid", None)
        text = getattr(update, "text", None) or ""

        if not text.strip():
            return None

        chat_type = "private"
        try:
            if getattr(update, "is_group", False):
                chat_type = "group"
            elif getattr(update, "is_channel", False):
                chat_type = "channel"
            else:
                obj_type = str(
                    getattr(update, "object_type", "") or
                    getattr(update, "type", "") or ""
                ).lower()
                if "group" in obj_type:
                    chat_type = "group"
                elif "channel" in obj_type:
                    chat_type = "channel"
                elif object_guid and object_guid in self._chat_cache:
                    cached_type = self._chat_cache[object_guid].get("type")
                    if cached_type:
                        chat_type = cached_type
        except Exception:
            pass

        chat_name, chat_username = await self._resolve_chat(object_guid, update)
        chat_link = self._build_link(chat_username, object_guid, chat_type)

        media_type = "text"
        try:
            if getattr(
                    update,
                    "is_file_inline",
                    False) or getattr(
                    update,
                    "file_inline",
                    None):
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
            "chat_id": chat_name or str(object_guid or ""),
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
                log_success(
                    f"📁 فایل روبیکا ذخیره شد: {
                        os.path.basename(save_path)}")
                return save_path
        except Exception as e:
            log_warning(f"دانلود فایل روبیکا ناموفق بود: {e}")
        return None

    async def _resolve_chat(self, object_guid: str | None, update) -> tuple:
        """اسم چت را با تلاش‌های چندگانه می‌گیرد."""
        if not object_guid:
            return (getattr(update, "title", None) or "نامشخص", None)

        if object_guid in self._chat_cache:
            c = self._chat_cache[object_guid]
            if c.get("name") and c["name"] != str(object_guid):
                return (c["name"], c.get("username"))

        name = None
        username = None

        name = getattr(update, "title", None)
        if not name:
            first = getattr(update, "first_name", None)
            last = getattr(update, "last_name", None)
            if first:
                name = f"{first} {last or ''}".strip()
        username = getattr(update, "username", None)

        if not name:
            try:
                obj = await update.get_object()
                data = obj.to_dict() if hasattr(obj, "to_dict") else {}
                name = (
                    data.get("title")
                    or data.get("first_name")
                    or data.get("name")
                )
                username = username or data.get("username")
            except Exception:
                pass

        if not name and self._client:
            try:
                chat_obj = await self._client.get_chat(object_guid)
                data = chat_obj.to_dict() if hasattr(chat_obj, "to_dict") else {}
                name = (
                    data.get("title")
                    or data.get("first_name")
                    or data.get("name")
                )
                username = username or data.get("username")
            except Exception:
                pass

        if not name:
            cached = self._chat_cache.get(object_guid)
            if cached and cached.get("name"):
                name = cached["name"]

        if not name:
            name = str(object_guid)[:20]

        self._chat_cache[object_guid] = {"name": name, "username": username}
        log_debug(f"🔍 [rubika:resolve] {str(object_guid)[:10]}... → «{name}»")
        return (name, username)

    @staticmethod
    def _build_link(
            username: str | None,
            object_guid: str | None,
            chat_type: str) -> str | None:
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

    async def fetch_messages(
        self, since: datetime = None
    ) -> list[dict[str, Any]]:
        if not self._connected or not self._client:
            return []

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()

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

    async def _preload_chats(self):
        """همه چت‌ها را cache می‌کند تا اسم‌ها زودتر لود شوند."""
        if not self._client:
            return
        try:
            chats_resp = await self._client.get_chats()
            all_chats = self._extract_list(chats_resp, "chats")
            _max_chats = _get_max_chats()
            chats = (
                all_chats[:_max_chats]
                if _max_chats > 0
                else all_chats
            )

            if chats and not getattr(self, "_chats_debugged", False):
                self._chats_debugged = True
                try:
                    sample = chats[0]
                    sample_dict = (
                        sample.to_dict()
                        if hasattr(sample, "to_dict")
                        else sample
                    )
                    log_debug(
                        f"🔍 [rubika:debug:chat] fields="
                        f"{list(sample_dict.keys())[:20]}"
                    )
                    log_debug(
                        f"🔍 [rubika:debug:chat] sample="
                        f"{str(sample_dict)[:500]}"
                    )
                except Exception as e:
                    log_warning(f"⚠️ [rubika:debug] {type(e).__name__}: {e}")

            count = 0
            for ch in chats:
                guid = self._dig(ch, "object_guid")
                if not guid:
                    continue
                ch_name = (
                    self._dig(ch, "title")
                    or self._dig(ch, "first_name")
                    or self._dig(ch, "name")
                )
                if not ch_name and self._client:
                    for method_name in (
                            "get_chat_info", "get_chat", "get_object"):
                        if ch_name:
                            break
                        method = getattr(self._client, method_name, None)
                        if method is None:
                            continue
                        try:
                            info = await method(guid)
                            if hasattr(
                                    info, "to_dict") and callable(
                                    info.to_dict):
                                d = info.to_dict()
                            elif hasattr(info, "to_dict"):
                                d = info.to_dict
                            elif isinstance(info, dict):
                                d = info
                            else:
                                d = {}

                            if not isinstance(d, dict):
                                d = {}

                            cd = (
                                d.get("chat")
                                or d.get("abs_object")
                                or d.get("object")
                                or d
                            )
                            if isinstance(cd, dict):
                                ch_name = (
                                    cd.get("title")
                                    or cd.get("first_name")
                                    or cd.get("name")
                                )
                            if not ch_name:
                                ch_name = (
                                    getattr(info, "title", None)
                                    or getattr(info, "first_name", None)
                                    or getattr(info, "name", None)
                                )
                            if ch_name:
                                log_debug(
                                    f"✅ [rubika:preload] {str(guid)[:10]}... "
                                    f"→ «{ch_name}» (via {method_name})"
                                )
                        except Exception as e:
                            log_warning(
                                f"⚠️ [rubika:preload:{method_name}] "
                                f"{str(guid)[:10]}... | "
                                f"{type(e).__name__}: {e}"
                            )
                if not ch_name:
                    ch_name = str(guid)[:20]
                ch_username = self._dig(ch, "username")
                ch_type = str(
                    self._dig(ch, "abs_object", "type")
                    or self._dig(ch, "type")
                    or ""
                ).lower()

                entry = self._chat_cache.get(str(guid), {})
                entry["name"] = ch_name
                if ch_username:
                    entry["username"] = ch_username
                if "channel" in ch_type:
                    entry["type"] = "channel"
                elif "group" in ch_type:
                    entry["type"] = "group"
                elif "user" in ch_type or "private" in ch_type:
                    entry["type"] = "private"
                self._chat_cache[str(guid)] = entry
                count += 1

            log_success(f"♻️ [rubika:preload] {count} چت cache شد")
        except Exception as e:
            log_warning(f"⚠️ [rubika:preload] {type(e).__name__}: {e}")

    async def _fetch_from_chats(
        self, since: datetime = None
    ) -> list[dict[str, Any]]:
        """خواندن پیام‌های جدید از get_chats + get_messages."""
        if not self._client:
            return []

        results = []
        _hl = _get_history_limit()
        _max_chats = _get_max_chats()

        try:
            since_ts = int(since.timestamp()) if since else 0

            chats_resp = await self._client.get_chats()
            all_chats = self._extract_list(chats_resp, "chats")
            chats = (
                all_chats[:_max_chats]
                if _max_chats > 0
                else all_chats
            )

            # ─── debug: get_chats ───
            try:
                log_debug(
                    f"🔍 [rubika:debug:get_chats] "
                    f"type={type(chats_resp).__name__}"
                )
                attrs = [
                    a for a in dir(chats_resp)
                    if not a.startswith("_") and a != "to_dict"
                ]
                log_debug(
                    f"🔍 [rubika:debug:get_chats] attrs={attrs[:20]}"
                )
                td = getattr(chats_resp, "to_dict", None)
                if isinstance(td, dict):
                    log_debug(
                        f"🔍 [rubika:debug:get_chats] "
                        f"to_dict_keys={list(td.keys())[:20]}"
                    )
                    log_debug(
                        f"🔍 [rubika:debug:get_chats] "
                        f"to_dict_sample={str(td)[:500]}"
                    )
                chats_attr = getattr(chats_resp, "chats", None)
                log_debug(
                    f"🔍 [rubika:debug:get_chats] " f"chats_attr_type={
                        type(chats_attr).__name__} | " f"len={
                        len(chats_attr) if isinstance(
                            chats_attr, list) else 'N/A'}")
                log_debug(
                    f"🔍 [rubika:debug:get_chats] "
                    f"chats_extracted={len(chats)}"
                )
            except Exception as e:
                log_warning(
                    f"⚠️ [rubika:debug:get_chats] "
                    f"{type(e).__name__}: {e}"
                )

            for ch in chats:
                guid = self._dig(ch, "object_guid")
                if not guid:
                    continue
                ch_name = (
                    self._dig(ch, "title")
                    or self._dig(ch, "first_name")
                    or self._dig(ch, "name")
                )
                if ch_name:
                    existing = self._chat_cache.get(str(guid), {})
                    existing["name"] = ch_name
                    ch_username = self._dig(ch, "username")
                    if ch_username:
                        existing["username"] = ch_username
                    self._chat_cache[str(guid)] = existing

            for ch in chats:
                guid = self._dig(ch, "object_guid")
                if not guid:
                    continue

                ctype = str(
                    self._dig(ch, "abs_object", "type")
                    or self._dig(ch, "type")
                    or ""
                ).lower()

                if not any(
                    k in ctype for k in (
                        "group",
                        "channel",
                        "user",
                        "private")):
                    continue

                try:
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
                        if not text.strip():
                            continue

                        mid = str(self._dig(m, "message_id") or "")
                        if not mid:
                            continue

                        key = f"rubika:{guid}:{mid}"
                        if key in self._seen_ids:
                            log_debug(f"⏭️ [seen] تکراری | rubika | {mid}")
                            continue
                        self._seen_ids.add(key)

                        results.append({
                            "id": mid,
                            "platform": "rubika",
                            "text": text,
                            "chat_id": self._chat_cache.get(str(guid), {}).get("name") or str(guid),
                            "chat_name": self._chat_cache.get(str(guid), {}).get("name", str(guid)),
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
        """fetch_history از get_chats استفاده می‌کند."""
        return await self.fetch_messages(since)

    @staticmethod
    def _extract_list(resp, key: str):
        """استخراج لیست از پاسخ rubpy — Update یا dict یا attribute."""
        data = {}

        try:
            td = getattr(resp, "to_dict", None)
            if td is not None:
                if callable(td):
                    result = td()
                    if isinstance(result, dict):
                        data = result
                elif isinstance(td, dict):
                    data = td
        except Exception:
            pass

        if not data and isinstance(resp, dict):
            data = resp

        if isinstance(data, dict):
            val = data.get(key)
            if isinstance(val, list):
                return val
            for v in data.values():
                if isinstance(v, dict):
                    inner = v.get(key)
                    if isinstance(inner, list):
                        return inner

        try:
            val = getattr(resp, key, None)
            if isinstance(val, list):
                return val
            for attr in ("update", "data", "result", "message"):
                inner = getattr(resp, attr, None)
                if inner is not None:
                    inner_val = getattr(inner, key, None)
                    if isinstance(inner_val, list):
                        return inner_val
        except Exception:
            pass

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
