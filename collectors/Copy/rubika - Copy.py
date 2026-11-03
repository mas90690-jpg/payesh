# collectors/rubika.py
"""
کلکتور روبیکا (Rubika) — نسخهٔ ۱۰.۰ (نهایی — فوق دقیق)

تغییرات نسخهٔ ۱۰.۰:
  - 🎯 FIX: اسم چت‌ها دیگر GUID خام نیستند
    → helper جدید `_extract_name_from_obj` در چند سطح جستجو می‌کند
    → `_preload_chats` قبل از fallback به GUID، از API می‌پرسد
    → `_fetch_from_chats` هم قبل از fallback به GUID، از API می‌پرسد
    → `_resolve_chat` از همان helper استفاده می‌کند
  - 🎯 FIX: chat_username و chat_link در پیام‌های تاریخی پر می‌شوند
  - 🎯 FIX: آمار API-resolved در preload گزارش می‌شود
  - 🎯 id پایدار از chat_guid + message_id (بدون تکرار)
  - 🎯 timestamp دقیق از time (ثانیه یا میلی‌ثانیه)
  - 🎯 since دقیق (فیلتر بر اساس time)
  - 🎯 last_run_at — چند روز خاموش بود، از همون‌جا ادامه می‌ده
  - 🎯 _last_scrape_stats برای آمار تفکیکی
  - 🎯 چت‌های سیستمی فیلتر می‌شن
  - 🎯 تنظیمات از .env
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
from datetime import datetime
from typing import Any

from core.contracts.base_collector import BaseCollector, ConnectionStatus
from core.kernel.logger import log_debug, log_error, log_info, log_success, log_warning


_MAX_SEEN = 100000

# 🎯 چت‌های سیستمی
_SYSTEM_NAMES = {
    "پیام‌های ذخیره شده", "پیامهای ذخیره شده", "ذخیره‌شده",
    "ذخیره شده", "شما", "Saved Messages", "Drafts", "You",
    "روبیکا", "Rubika", "تیم روبیکا", "پشتیبانی روبیکا",
}

_SYSTEM_PREFIXES = (
    "کانال ایجاد", "گروه ایجاد", "Channel created", "Group created",
)


# ═══════════════════════════════════════════════════════════
# helpers — env
# ═══════════════════════════════════════════════════════════
def _get_history_limit() -> int:
    """RUBIKA_HISTORY_LIMIT را می‌خواند."""
    try:
        v = int(os.getenv("RUBIKA_HISTORY_LIMIT", "500"))
        return v if v > 0 else 500
    except (TypeError, ValueError):
        return 500


def _get_max_chats() -> int:
    """RUBIKA_MAX_CHATS را می‌خواند."""
    try:
        v = int(os.getenv("RUBIKA_MAX_CHATS", "0"))
        return max(v, 0)
    except (TypeError, ValueError):
        return 0


def _get_session_path() -> str:
    from config import PLUGINS_CONFIG, SESSIONS_DIR
    cfg = PLUGINS_CONFIG.get("rubikacollector", {}) or {}
    default_path = os.path.join(SESSIONS_DIR, "rubika_session")
    p = cfg.get("session_path") or default_path
    try:
        parent = os.path.dirname(p)
        if parent:
            os.makedirs(parent, exist_ok=True)
    except Exception as e:
        log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
    return p


# ═══════════════════════════════════════════════════════════
# helper — استخراج اسم از هر شیئی
# ═══════════════════════════════════════════════════════════
def _obj_to_dict(obj) -> dict:
    """تبدیل هر شیئی (rubpy object / dict) به dict."""
    if obj is None:
        return {}

    data = {}
    try:
        td = getattr(obj, "to_dict", None)
        if td is not None:
            if callable(td):
                r = td()
                if isinstance(r, dict):
                    data = r
            elif isinstance(td, dict):
                data = td
    except Exception as e:
        log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

    if not data and isinstance(obj, dict):
        data = obj

    return data if isinstance(data, dict) else {}


def _extract_name_from_obj(obj) -> tuple[str | None, str | None]:
    """
    اسم و username را از هر شیئی استخراج می‌کند.
    جستجو در چند سطح: title, first_name, name, abs_object.title, ...

    Returns:
        (name, username) — هرکدام می‌توانند None باشند.
    """
    if obj is None:
        return None, None

    data = _obj_to_dict(obj)

    # اگر dict خالی بود، از attribute استفاده کن
    if not data:
        title = getattr(obj, "title", None)
        first = getattr(obj, "first_name", None)
        last = getattr(obj, "last_name", None)
        name_attr = getattr(obj, "name", None)
        username_attr = getattr(obj, "username", None)
        name = (
            title
            or (f"{first} {last or ''}".strip() if first else None)
            or name_attr
        )
        return (
            str(name).strip() if name else None,
            str(username_attr).strip() if username_attr else None,
        )

    # جستجو در سطح‌های مختلف
    candidates: list[dict] = [data]
    for key in (
        "chat", "abs_object", "object", "peer",
        "user", "channel", "group", "dialog",
    ):
        sub = data.get(key)
        if isinstance(sub, dict):
            candidates.append(sub)

    name: str | None = None
    username: str | None = None

    for c in candidates:
        if not name:
            n = c.get("title")
            if n and str(n).strip():
                name = str(n).strip()
        if not name:
            first = c.get("first_name")
            last = c.get("last_name")
            if first and str(first).strip():
                name = f"{first} {last or ''}".strip()
        if not name:
            n = c.get("name")
            if n and str(n).strip():
                name = str(n).strip()
        if not username:
            u = c.get("username")
            if u and str(u).strip():
                username = str(u).strip()
        if name and username:
            break

    return (name or None, username or None)


# ═══════════════════════════════════════════════════════════
# کلکتور
# ═══════════════════════════════════════════════════════════
class RubikaCollector(BaseCollector):
    def __init__(
        self,
        phone: str | None = None,
        password: str | None = None,
        poll_seconds: int | None = None,
        **kwargs,
    ):
        super().__init__()

        # ─── تنظیمات از env ───
        if not phone:
            try:
                from config import get_rubika_config
                cfg = get_rubika_config()
                phone = cfg.get("phone", "") or ""
            except Exception:
                phone = ""

        self.phone = phone
        self.password = password
        self._poll_seconds = int(
            poll_seconds or os.getenv("RUBIKA_POLL_SECONDS", "30")
        )
        self._client = None
        self._messages_queue: list[dict[str, Any]] = []
        self._connected = False
        self._running = False
        self._loop_task: asyncio.Task | None = None
        self._chat_cache: dict[str, dict[str, Any]] = {}
        self._seen_ids: set[str] = set()
        self._last_scrape_stats: dict[str, int] = {}

        from config import STORAGE_DIR
        self._storage_dir = STORAGE_DIR
        self._session_path = _get_session_path()
        self._media_dir = os.path.join(STORAGE_DIR, "media", "rubika")
        os.makedirs(self._media_dir, exist_ok=True)

        # ─── seen ───
        self._seen_path = os.path.join(
            STORAGE_DIR, "offset", "rubika_seen.json"
        )
        os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
        self._load_seen()

        # ─── state (first_run + last_run_at) ───
        self._state_path = os.path.join(
            STORAGE_DIR, "offset", "rubika_state.json"
        )
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
        self._first_run_done = self._load_first_run()
        self._last_run_at = self._load_last_run_at()

        log_info(
            f"🔧 [rubika:init] session={self._session_path} | "
            f"history_limit={_get_history_limit()} | "
            f"max_chats={_get_max_chats()}"
        )

    # ═══════════════════════════════════════════════
    # state
    # ═══════════════════════════════════════════════
    def _load_first_run(self) -> bool:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                d = json.load(f)
            done = bool(d.get("first_run_done", False))
            if done:
                log_info("♻️ [rubika:state] بار اول انجام شده")
            else:
                log_info("🆕 [rubika:state] بار اول — تاریخچه کامل")
            return done
        except Exception:
            log_info("🆕 [rubika:state] بار اول — تاریخچه کامل")
            return False

    def _load_last_run_at(self) -> datetime | None:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                d = json.load(f)
            s = d.get("last_run_at")
            if s:
                return datetime.fromisoformat(s)
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")
        return None

    def _save_state(self):
        try:
            data = {
                "first_run_done": True,
                "first_run_at": getattr(
                    self, "_first_run_at", datetime.now().isoformat()  # noqa: DTZ005
                ),
                "last_run_at": datetime.now().isoformat(),  # noqa: DTZ005
            }
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log_warning(f"ذخیره state ناموفق: {e}")

    # ═══════════════════════════════════════════════
    # seen
    # ═══════════════════════════════════════════════
    def _load_seen(self):
        try:
            with open(self._seen_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._seen_ids = set(data[-_MAX_SEEN:])
                log_info(
                    f"♻️ {len(self._seen_ids)} شناسهٔ دیده‌شدهٔ روبیکا"
                )
        except Exception:
            self._seen_ids = set()

    def _save_seen(self):
        try:
            if len(self._seen_ids) > _MAX_SEEN:
                self._seen_ids = set(
                    list(self._seen_ids)[-_MAX_SEEN:]
                )
            with open(self._seen_path, "w", encoding="utf-8") as f:
                json.dump(list(self._seen_ids), f, ensure_ascii=False)
        except Exception as e:
            log_warning(f"ذخیره seen: {e}")

    # ═══════════════════════════════════════════════
    # خواص
    # ═══════════════════════════════════════════════
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

    # ═══════════════════════════════════════════════
    # اتصال
    # ═══════════════════════════════════════════════
    async def connect(self) -> bool:
        try:
            if not self.phone:
                log_error("RUBIKA_PHONE در .env تنظیم نشده است")
                return False

            # lazy import — rubpy سنگینه، فقط وقتی لازمه لود کن
            try:
                from rubpy import Client
            except ImportError:
                log_error(
                    "کتابخانه rubpy نصب نیست. نصب: pip install rubpy"
                )
                return False

            phone_number = self._normalize_phone(self.phone)
            log_info(
                f"🔄 اتصال به روبیکا (کاربر) با شماره "
                f"{phone_number} | session={self._session_path}"
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

            if os.path.exists(self._session_path):
                log_info(
                    "🔑 [rubika:connect] استفاده از نشست موجود..."
                )
                await self._client.start()
            else:
                log_info(
                    "🔑 [rubika:connect] نشست جدید — نیاز به لاگین..."
                )
                await self._client.start(phone_number=phone_number)

            self._running = True
            self._loop_task = asyncio.create_task(self._run_loop())

            self._connected = True
            self._set_status(ConnectionStatus.CONNECTED)
            log_success("✅ اتصال به روبیکا برقرار شد")

            try:
                await self._preload_chats()
            except Exception as e:
                log_warning(
                    f"⚠️ [rubika:preload] {type(e).__name__}: {e}"
                )

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
    def _normalize_phone(phone: str) -> str:
        p = phone.strip()
        if p.startswith("+98"):
            return "0" + p[3:]
        if p.startswith("98"):
            return "0" + p[2:]
        if not p.startswith("0"):
            return "0" + p
        return p

    # ═══════════════════════════════════════════════
    # چت سیستمی
    # ═══════════════════════════════════════════════
    def _is_system_name(self, name: str) -> bool:
        if not name:
            return False
        n = name.strip()
        if not n:
            return False
        if n in _SYSTEM_NAMES:
            return True
        return any(n.startswith(prefix) for prefix in _SYSTEM_PREFIXES)

    # ═══════════════════════════════════════════════
    # استخراج timestamp
    # ═══════════════════════════════════════════════
    def _extract_timestamp(self, update) -> datetime | None:
        """timestamp از time (ثانیه یا میلی‌ثانیه)."""
        time_val = None
        for attr in ("time", "date", "timestamp", "created_at"):
            v = getattr(update, attr, None)
            if v:
                time_val = v
                break

        # fallback: از original_update
        if not time_val:
            try:
                orig = getattr(update, "original_update", None)
                if isinstance(orig, dict):
                    time_val = orig.get("time")
            except Exception as e:
                log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

        if not time_val:
            return None

        try:
            ts_int = int(time_val)
            # اگه بزرگتر از 10 میلیارد بود → میلی‌ثانیه
            if ts_int > 10_000_000_000:
                return datetime.fromtimestamp(ts_int / 1000)  # noqa: DTZ006
            else:
                return datetime.fromtimestamp(ts_int)  # noqa: DTZ006
        except (ValueError, OSError, TypeError):
            return None

    # ═══════════════════════════════════════════════
    # استخراج sender
    # ═══════════════════════════════════════════════
    @staticmethod
    def _extract_sender_name(update) -> str:
        for attr in (
            "author_title", "sender_name",
            "author_name", "from_name"
        ):
            v = getattr(update, attr, None)
            if v and str(v).strip():
                return str(v).strip()[:60]
        return ""

    # ═══════════════════════════════════════════════
    # resolve — اسم چت از هر منبعی
    # ═══════════════════════════════════════════════
    async def _resolve_name_from_api(
        self, object_guid: str,
    ) -> tuple[str | None, str | None]:
        """
        اسم و username را از API می‌گیرد.
        چند روش را امتحان می‌کند و اولین موفق را برمی‌گرداند.
        """
        if not self._client or not object_guid:
            return None, None

        for method_name in ("get_chat_info", "get_chat", "get_object"):
            method = getattr(self._client, method_name, None)
            if method is None:
                continue
            try:
                info = await method(object_guid)
                n, u = _extract_name_from_obj(info)
                if n:
                    log_debug(
                        f"✅ [rubika:resolve:api] "
                        f"{str(object_guid)[:10]}... → «{n}» "
                        f"(via {method_name})"
                    )
                    return n, u
            except Exception as e:
                log_debug(
                    f"⚠️ [rubika:resolve:api:{method_name}] "
                    f"{str(object_guid)[:10]}... | "
                    f"{type(e).__name__}: {e}"
                )

        return None, None

    # ═══════════════════════════════════════════════
    # تبدیل Update → Unified Message
    # ═══════════════════════════════════════════════
    async def _to_unified(self, update) -> dict[str, Any] | None:
        object_guid = getattr(update, "object_guid", None)
        text = getattr(update, "text", None) or ""

        if not text.strip():
            return None

        # chat_type
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
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

        chat_name, chat_username = await self._resolve_chat(
            object_guid, update
        )

        # چت سیستمی skip
        if self._is_system_name(chat_name):
            log_debug(f"⏭️ [rubika:system] {chat_name}")
            return None

        chat_link = self._build_link(
            chat_username, object_guid, chat_type
        )

        # media
        media_type = "text"
        try:
            if (
                getattr(update, "is_file_inline", False)
                or getattr(update, "file_inline", None)
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
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

        file_id = None
        local_file_path = None
        if media_type != "text":
            file_id = str(getattr(update, "message_id", "") or "")
            from analyzers.keyword_finder import get_keyword_finder
            if get_keyword_finder().has_match(text):
                local_file_path = await self._download_media(
                    update, media_type, file_id
                )

        # 🎯 id پایدار: hash(chat_guid + message_id)
        message_id = str(getattr(update, "message_id", "") or "")
        chat_guid_str = str(object_guid or chat_name)
        if message_id:
            mid = hashlib.sha256(
                f"{chat_guid_str}:{message_id}".encode()
            ).hexdigest()[:32]
        else:
            mid = hashlib.sha256(
                f"{chat_guid_str}:{text[:200]}".encode()
            ).hexdigest()[:32]

        # timestamp
        msg_timestamp = self._extract_timestamp(update)

        return {
            "id": mid,
            "platform": "rubika",
            "text": text,
            "chat_id": chat_guid_str,
            "chat_name": chat_name,
            "chat_type": chat_type,
            "chat_username": chat_username,
            "chat_link": chat_link,
            "user_id": str(getattr(update, "author_guid", "") or ""),
            "sender_name": self._extract_sender_name(update),
            "timestamp": msg_timestamp or datetime.now(),  # noqa: DTZ005
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
        try:
            ext = {
                "photo": ".jpg",
                "video": ".mp4",
                "voice": ".ogg",
                "audio": ".mp3",
                "file": "",
            }.get(media_type, "")
            stamp = file_id or datetime.now().strftime("%Y%m%d%H%M%S%f")  # noqa: DTZ005
            save_path = os.path.join(
                self._media_dir, f"rubika_{stamp}{ext}"
            )
            await update.download(save_as=save_path)
            if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                log_success(
                    f"📁 فایل روبیکا ذخیره شد: "
                    f"{os.path.basename(save_path)}"
                )
                return save_path
        except Exception as e:
            log_warning(f"دانلود فایل روبیکا ناموفق: {e}")
        return None

    async def _resolve_chat(
        self, object_guid: str | None, update
    ) -> tuple[str, str | None]:
        """
        اسم چت را با تلاش‌های چندگانه می‌گیرد:
          ۱. cache
          ۲. خود update
          ۳. API
          ۴. fallback به GUID (فقط اگه هیچ‌کدام نشد)
        """
        if not object_guid:
            n, u = _extract_name_from_obj(update)
            return (n or "نامشخص", u)

        # ۱) cache
        if object_guid in self._chat_cache:
            c = self._chat_cache[object_guid]
            if c.get("name") and c["name"] != str(object_guid):
                return (c["name"], c.get("username"))

        # ۲) خود update
        name, username = _extract_name_from_obj(update)

        # ۳) API
        if not name:
            n, u = await self._resolve_name_from_api(object_guid)
            if n:
                name = n
                username = username or u

        # ۴) fallback
        if not name:
            cached = self._chat_cache.get(object_guid)
            if cached and cached.get("name"):
                name = cached["name"]

        if not name:
            name = str(object_guid)[:20]

        self._chat_cache[object_guid] = {
            "name": name,
            "username": username,
        }
        log_debug(
            f"🔍 [rubika:resolve] {str(object_guid)[:10]}... → «{name}»"
        )
        return (name, username)

    @staticmethod
    def _build_link(
        username: str | None,
        object_guid: str | None,
        chat_type: str,
    ) -> str | None:
        if username:
            base = os.getenv("RUBIKA_WEB_URL", "https://rubika.ir").rstrip("/")
            return f"{base}/{username}"
        return None

    # ═══════════════════════════════════════════════
    # fetch_messages
    # ═══════════════════════════════════════════════
    async def fetch_messages(
        self, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        if not self._connected or not self._client:
            return []

        # ─── تعیین since نهایی ───
        effective_since = since
        if effective_since is None and self._last_run_at:
            effective_since = self._last_run_at
            log_info(
                f"♻️ [rubika:since] از last_run_at: "
                f"{self._last_run_at.isoformat()}"
            )
        elif since:
            log_info(
                f"🎯 [rubika:since] از پارامتر since: "
                f"{since.isoformat()}"
            )

        # ─── ریست آمار ───
        self._last_scrape_stats = {
            "total_chats": 0,
            "processed": 0,
            "skipped_system": 0,
            "skipped_target": 0,
            "failed": 0,
            "messages_found": 0,
            "messages_skipped_old": 0,
            "messages_skipped_dup": 0,
        }

        # ─── ۱) پیام‌های زنده از صف ───
        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()

        # ─── ۲) پیام‌های تاریخی از get_messages ───
        try:
            history_msgs = await self._fetch_from_chats(
                effective_since=effective_since
            )
            new_msgs.extend(history_msgs)
        except Exception as e:
            log_warning(
                f"⚠️ [rubika:fetch] get_chats | "
                f"{type(e).__name__}: {e}"
            )

        self._save_seen()

        # ─── ذخیره state ───
        if not self._first_run_done:
            self._first_run_at = datetime.now().isoformat()  # noqa: DTZ005
            self._first_run_done = True
        self._save_state()

        if new_msgs:
            log_success(f"📥 {len(new_msgs)} پیام از روبیکا")

        # ─── گزارش آمار ───
        stats = self._last_scrape_stats
        log_info(
            f"📊 [rubika:stats] total={stats['total_chats']} | "
            f"processed={stats['processed']} | "
            f"system={stats['skipped_system']} | "
            f"failed={stats['failed']} | "
            f"found={stats['messages_found']} | "
            f"old={stats['messages_skipped_old']} | "
            f"dup={stats['messages_skipped_dup']}"
        )

        return new_msgs

    # ═══════════════════════════════════════════════
    # preload_chats — با API fallback
    # ═══════════════════════════════════════════════
    async def _preload_chats(self):
        """
        همه چت‌ها را cache می‌کند تا اسم‌ها زودتر لود شوند.
        اگه get_chats اسم نداشت، از API می‌پرسد.
        """
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

            count = 0
            resolved_from_api = 0
            resolved_fallback = 0

            for ch in chats:
                guid = self._dig(ch, "object_guid")
                if not guid:
                    continue

                # ۱) اسم از خود get_chats
                ch_name, ch_username = _extract_name_from_obj(ch)

                # ۲) اگه نشد، از API بپرس
                if not ch_name:
                    n, u = await self._resolve_name_from_api(str(guid))
                    if n:
                        ch_name = n
                        ch_username = ch_username or u
                        resolved_from_api += 1

                # ۳) fallback نهایی
                if not ch_name:
                    ch_name = str(guid)[:20]
                    resolved_fallback += 1

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

            log_success(
                f"♻️ [rubika:preload] {count} چت cache شد | "
                f"API: {resolved_from_api} | "
                f"fallback: {resolved_fallback}"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [rubika:preload] {type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════════
    # fetch_from_chats
    # ═══════════════════════════════════════════════
    async def _fetch_from_chats(
        self, effective_since: datetime | None = None
    ) -> list[dict[str, Any]]:
        if not self._client:
            return []

        results = []
        _hl = _get_history_limit()
        _max_chats = _get_max_chats()

        try:
            chats_resp = await self._client.get_chats()
            all_chats = self._extract_list(chats_resp, "chats")
            self._last_scrape_stats["total_chats"] = len(all_chats)

            chats = (
                all_chats[:_max_chats]
                if _max_chats > 0
                else all_chats
            )

            for ch in chats:
                guid = self._dig(ch, "object_guid")
                if not guid:
                    continue

                # ─── ۱) اسم از خود get_chats ───
                ch_name, ch_username = _extract_name_from_obj(ch)

                # ─── ۲) از cache ───
                if not ch_name:
                    cached = self._chat_cache.get(str(guid), {})
                    ch_name = cached.get("name")
                    ch_username = ch_username or cached.get("username")

                # ─── ۳) از API ───
                if not ch_name:
                    n, u = await self._resolve_name_from_api(str(guid))
                    if n:
                        ch_name = n
                        ch_username = ch_username or u
                        # cache کن
                        entry = self._chat_cache.get(str(guid), {})
                        entry["name"] = ch_name
                        if ch_username:
                            entry["username"] = ch_username
                        self._chat_cache[str(guid)] = entry

                # ─── ۴) fallback ───
                if not ch_name:
                    ch_name = str(guid)[:20]

                # ─── چت سیستمی ───
                if self._is_system_name(ch_name):
                    self._last_scrape_stats["skipped_system"] += 1
                    continue

                # ─── ctype ───
                ctype = str(
                    self._dig(ch, "abs_object", "type")
                    or self._dig(ch, "type")
                    or ""
                ).lower()

                if not any(
                    k in ctype
                    for k in ("group", "channel", "user", "private")
                ):
                    continue

                # ─── get_messages ───
                try:
                    msgs_resp = await self._client.get_messages(
                        guid, max_id=None, limit=_hl
                    )
                    msg_list = self._extract_list(msgs_resp, "messages")

                    for m in msg_list:
                        self._last_scrape_stats["messages_found"] += 1

                        # ─── since filter ───
                        mtime = self._dig(m, "time")
                        msg_timestamp = None
                        if mtime:
                            try:
                                ts_int = int(mtime)
                                if ts_int > 10_000_000_000:
                                    msg_timestamp = datetime.fromtimestamp(  # noqa: DTZ006
                                        ts_int / 1000
                                    )
                                else:
                                    msg_timestamp = datetime.fromtimestamp(  # noqa: DTZ006
                                        ts_int
                                    )
                            except (ValueError, OSError) as e:
                                log_debug(
                                    f"⚠️ silent: {
                                        type(e).__name__}: {e}")

                        if (
                            effective_since
                            and msg_timestamp
                            and msg_timestamp < effective_since
                        ):
                            self._last_scrape_stats[
                                "messages_skipped_old"
                            ] += 1
                            continue

                        text = self._dig(m, "text") or ""
                        if not text.strip():
                            continue

                        # ─── id پایدار ───
                        message_id = str(
                            self._dig(m, "message_id") or ""
                        )
                        if not message_id:
                            continue

                        mid = hashlib.sha256(
                            f"{guid}:{message_id}".encode()
                        ).hexdigest()[:32]

                        # ─── dedupe سراسری ───
                        key = f"{guid}:{mid}"
                        if key in self._seen_ids:
                            self._last_scrape_stats[
                                "messages_skipped_dup"
                            ] += 1
                            continue
                        self._seen_ids.add(key)
                        if len(self._seen_ids) > 120_000:
                            self._seen_ids = set(
                                list(self._seen_ids)[-_MAX_SEEN:]
                            )

                        # ─── chat_type ───
                        if "channel" in ctype:
                            chat_type = "channel"
                        elif "group" in ctype:
                            chat_type = "group"
                        else:
                            chat_type = "private"

                        # ─── chat_link ───
                        chat_link = self._build_link(
                            ch_username, str(guid), chat_type
                        )

                        results.append({
                            "id": mid,
                            "platform": "rubika",
                            "text": text,
                            "chat_id": str(guid),
                            "chat_name": ch_name,
                            "chat_type": chat_type,
                            "chat_username": ch_username,
                            "chat_link": chat_link,
                            "user_id": str(
                                self._dig(m, "author_object_guid") or ""
                            ),
                            "sender_name": str(
                                self._dig(m, "author_title") or ""
                            ),
                            "timestamp": msg_timestamp or datetime.now(),  # noqa: DTZ005
                            "media_type": "text",
                            "file_id": None,
                            "local_file_path": None,
                            "reply_to": None,
                        })

                    self._last_scrape_stats["processed"] += 1
                    await asyncio.sleep(0.2)

                except Exception as e:
                    log_warning(
                        f"⚠️ [rubika:chat] {ch_name}: "
                        f"{type(e).__name__}: {e}"
                    )
                    self._last_scrape_stats["failed"] += 1
                    continue

        except Exception as e:
            log_warning(
                f"⚠️ [rubika:_fetch_from_chats] "
                f"{type(e).__name__}: {e}"
            )

        return results

    # ═══════════════════════════════════════════════
    # fetch_history
    # ═══════════════════════════════════════════════
    async def fetch_history(
        self, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        return await self.fetch_messages(since)

    # ═══════════════════════════════════════════════
    # ابزار
    # ═══════════════════════════════════════════════
    @staticmethod
    def _extract_list(resp, key: str):
        """استخراج لیست از پاسخ rubpy."""
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
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

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
        except Exception as e:
            log_debug(f"⚠️ silent: {type(e).__name__}: {e}")

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
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
        if self._client:
            with contextlib.suppress(Exception):
                await self._client.disconnect()
        self._connected = False
        self._set_status(ConnectionStatus.DISCONNECTED)
        log_info("🔌 قطع ارتباط از روبیکا")

    async def check_health(self) -> bool:
        return self._connected
