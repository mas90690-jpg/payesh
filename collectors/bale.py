# collectors/bale.py
import asyncio
import os
import aiohttp
from datetime import datetime
from typing import List, Dict, Any, Optional
from core.base_collector import BaseCollector, ConnectionStatus
from core.logger import log_info, log_success, log_error, log_warning

# ایمپورتِ کتابخانه در سطح ماژول با محافظ: اگر نصب نبود برنامه نمی‌شکند (None می‌ماند)
# و اگر بود، در connect استفاده می‌شود (و در تست‌ها قابل patch است).
try:
    from aiobale import Client, Dispatcher
    from aiobale.types import Message
except ImportError:
    Client = Dispatcher = Message = None

class BaleCollector(BaseCollector):
    def __init__(self, token: str = None, bot_id: str = None,
                 type: str = "user", username: str = None, password: str = None,
                 phone: str = None, **kwargs):
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
        self._last_message_id = None
        self._history_fetched = False
        # کش اطلاعات گروه (chat_id -> {name, username, link}) برای جلوگیری از درخواست تکراری
        self._chat_cache = {}

        from config import PLUGINS_CONFIG, STORAGE_DIR
        self._session_path = PLUGINS_CONFIG.get("balecollector", {}).get(
            "session_path", os.path.join(STORAGE_DIR, "sessions", "bale", "bale_session")
        )
        os.makedirs(os.path.dirname(self._session_path), exist_ok=True)

        # پوشه ذخیره فایل‌های دریافتی
        self._media_dir = os.path.join(STORAGE_DIR, "media", "bale")
        os.makedirs(self._media_dir, exist_ok=True)

    @property
    def platform_name(self) -> str:
        return "bale"

    @property
    def supports_history(self) -> bool:
        return True

    async def _download_file(self, file_id: str, media_type: str) -> Optional[str]:
        """دانلود فایل از بله و ذخیره در پوشه media"""
        if not self._client or not file_id:
            return None
        
        try:
            # مسیر ذخیره فایل
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            ext = self._get_extension(media_type)
            filename = f"{timestamp}_{file_id[:20]}{ext}"
            filepath = os.path.join(self._media_dir, filename)
            
            # دانلود فایل (بسته به نوع کلاینت)
            if hasattr(self._client, 'download_file'):
                await self._client.download_file(file_id, filepath)
            elif hasattr(self._client, 'download_media'):
                await self._client.download_media(file_id, filepath)
            else:
                # اگر متد دانلود مستقیم نداشت، از API استفاده کن
                log_warning(f"امکان دانلود فایل {file_id} وجود ندارد")
                return None
            
            log_success(f"📁 فایل دانلود شد: {filename}")
            return filepath
            
        except Exception as e:
            log_error(f"خطا در دانلود فایل {file_id}: {e}")
            return None
    
    def _get_extension(self, media_type: str) -> str:
        """دریافت پسوند فایل بر اساس نوع رسانه"""
        extensions = {
            "photo": ".jpg",
            "video": ".mp4",
            "audio": ".mp3",
            "voice": ".ogg",
            "document": ".file",
            "gif": ".gif"
        }
        return extensions.get(media_type, ".bin")
    
    def _get_media_type_from_message(self, message) -> tuple:
        """تشخیص نوع رسانه و استخراج file_id از شیء Message"""
        # بررسی انواع مختلف رسانه با try/except
        media_type = "text"
        file_id = None
        caption = None
        
        try:
            # عکس
            if hasattr(message, 'photo') and message.photo:
                media_type = "photo"
                if hasattr(message.photo, '__getitem__') and len(message.photo) > 0:
                    if hasattr(message.photo[-1], 'file_id'):
                        file_id = message.photo[-1].file_id
                    elif hasattr(message.photo[-1], 'file_unique_id'):
                        file_id = message.photo[-1].file_unique_id
                caption = getattr(message, 'caption', None)
                return media_type, file_id, caption
        except Exception:
            pass
        
        try:
            # ویدیو
            if hasattr(message, 'video') and message.video:
                media_type = "video"
                if hasattr(message.video, 'file_id'):
                    file_id = message.video.file_id
                elif hasattr(message.video, 'file_unique_id'):
                    file_id = message.video.file_unique_id
                caption = getattr(message, 'caption', None)
                return media_type, file_id, caption
        except Exception:
            pass
        
        try:
            # صدا
            if hasattr(message, 'audio') and message.audio:
                media_type = "audio"
                if hasattr(message.audio, 'file_id'):
                    file_id = message.audio.file_id
                caption = getattr(message, 'caption', None)
                return media_type, file_id, caption
        except Exception:
            pass
        
        try:
            # ویس
            if hasattr(message, 'voice') and message.voice:
                media_type = "voice"
                if hasattr(message.voice, 'file_id'):
                    file_id = message.voice.file_id
                caption = getattr(message, 'caption', None)
                return media_type, file_id, caption
        except Exception:
            pass
        
        try:
            # سند
            if hasattr(message, 'document') and message.document:
                media_type = "document"
                if hasattr(message.document, 'file_id'):
                    file_id = message.document.file_id
                caption = getattr(message, 'caption', None)
                return media_type, file_id, caption
        except Exception:
            pass
        
        try:
            # استیکر/GIF
            if hasattr(message, 'sticker') and message.sticker:
                media_type = "sticker"
                if hasattr(message.sticker, 'file_id'):
                    file_id = message.sticker.file_id
                caption = None
                return media_type, file_id, caption
        except Exception:
            pass
        
        try:
            if hasattr(message, 'animation') and message.animation:
                media_type = "gif"
                if hasattr(message.animation, 'file_id'):
                    file_id = message.animation.file_id
                caption = getattr(message, 'caption', None)
                return media_type, file_id, caption
        except Exception:
            pass
        
        return media_type, file_id, caption

    def _chat_type_of(self, chat) -> str:
        """تشخیص نوع چت از روی فیلد type در aiobale."""
        s = str(getattr(chat, 'type', '')).lower()
        if 'group' in s:
            return 'group'
        if 'channel' in s:
            return 'channel'
        if 'private' in s or 'user' in s:
            return 'private'
        return 'group'

    def _extract_sender_id(self, message) -> str:
        """استخراج شناسه فرستنده (sender_id ممکن است آبجکت باشد یا عدد)."""
        sid = getattr(message, 'sender_id', None)
        if sid is None:
            return "unknown"
        inner = getattr(sid, 'id', None)
        return str(inner if inner is not None else sid)

    async def _resolve_group(self, chat_id_raw, chat_type: str) -> dict:
        """نام واقعی، یوزرنیم و لینک گروه/کانال را با کش برمی‌گرداند."""
        key = str(chat_id_raw)
        if key in self._chat_cache:
            return self._chat_cache[key]

        name, username, link = "گروه", None, None
        try:
            cid = int(chat_id_raw)
            if chat_type in ('group', 'channel'):
                full = await self._client.get_full_group(cid)
                grp = getattr(full, 'group', None) or full
                name = getattr(grp, 'title', None) or name
                username = getattr(grp, 'username', None)
                try:
                    link = await self._client.get_group_link(cid)
                except Exception:
                    pass
        except Exception as e:
            log_warning(f"عدم دریافت اطلاعات گروه بله {chat_id_raw}: {e}")

        if username and not link:
            link = f"https://ble.ir/{username}"

        info = {"name": name, "username": username, "link": link}
        self._chat_cache[key] = info
        return info

    async def _build_unified(self, message) -> dict:
        """تبدیل یک پیام aiobale (زنده یا تاریخچه) به فرمت یکسان و غنی."""
        chat = message.chat
        chat_id_raw = getattr(chat, 'id', None)
        chat_type = self._chat_type_of(chat)
        group_info = await self._resolve_group(chat_id_raw, chat_type)
        user_id = self._extract_sender_id(message)
        media_type, file_id, caption = self._get_media_type_from_message(message)
        text_content = getattr(message, 'text', None) or caption or ""

        # دانلود فایل فقط وقتی متن/کپشن کلیدواژه داشته باشد (نه برای هر پیام).
        local_file_path = None
        if file_id:
            from analyzers.keyword_finder import get_keyword_finder
            if get_keyword_finder().has_match(text_content):
                local_file_path = await self._download_file(file_id, media_type)

        reply_to = getattr(message, 'replied_to', None) or getattr(message, 'quoted_replied_to', None)
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

    async def fetch_history(self, since: datetime) -> List[Dict[str, Any]]:
        """پیام‌های عقب‌افتادهٔ گروه‌ها/کانال‌های بله را از زمان since می‌خواند."""
        if not self._connected or not self._client:
            return []
        results = []
        try:
            dialogs = await self._client.load_dialogs(limit=100)
            for d in dialogs:
                try:
                    # load_dialogs مستقیماً PeerData برمی‌گرداند (نه d.peer)
                    peer = getattr(d, 'peer', None) or d
                    chat_id = getattr(peer, 'id', None)
                    ptype = getattr(peer, 'type', None)
                    type_str = str(ptype).lower()
                    if chat_id is None or not any(k in type_str for k in ('group', 'channel')):
                        continue
                    _hl = int(os.getenv("BALE_HISTORY_LIMIT", "200"))
                    history = await self._client.load_history(chat_id=chat_id, chat_type=ptype, limit=_hl)
                    for m in history:
                        # فیلتر زمانی (فقط بعد از since)
                        mdate = getattr(m, 'date', None)
                        try:
                            if isinstance(mdate, (int, float)) and datetime.fromtimestamp(mdate) < since:
                                continue
                        except Exception:
                            pass
                        msg = await self._build_unified(m)
                        if msg.get('text') or msg.get('media_type') != 'text':
                            results.append(msg)
                    await asyncio.sleep(0.3)
                except Exception as e:
                    log_warning(f"تاریخچهٔ یک گروه بله رد شد: {e}")
                    continue
        except Exception as e:
            # تاریخچهٔ بله best-effort است؛ اگر کتابخانه پشتیبانی نکرد، آرام رد شو
            log_info(f"تاریخچهٔ بله در دسترس نبود (دریافت لحظه‌ای فعال است): {e}")
        if results:
            log_success(f"📚 {len(results)} پیام عقب‌افتاده از بله خوانده شد")
        return results

    async def connect(self) -> bool:
        try:
            if self.type == "user":
                if not self.phone or not self.password:
                    log_error("BALE_PHONE و BALE_PASSWORD در فایل .env تنظیم نشده‌اند")
                    return False

                log_info("🔄 اتصال به بله در حالت کاربر عادی...")

                if Client is None or Dispatcher is None:
                    log_error("کتابخانه aiobale نصب نیست. لطفاً فایل whl را نصب کنید.")
                    return False

                phone_number = self.phone
                if phone_number.startswith('0'):
                    phone_number = phone_number[1:]

                self._dispatcher = Dispatcher()
                self._client = Client(self._dispatcher, session_file=self._session_path)

                @self._dispatcher.message()
                async def message_handler(message: Message):
                    try:
                        msg_data = await self._build_unified(message)
                        self._messages_queue.append(msg_data)
                        self._last_message_id = message.message_id
                        log_success(f"📩 [بله] {msg_data['chat_name']}: {(msg_data['text'] or msg_data['media_type'])[:40]}")
                    except Exception as e:
                        log_error(f"خطا در پردازش پیام دریافتی: {e}")
                        import traceback
                        traceback.print_exc()

                log_info(f"📞 در حال اتصال با شماره: {phone_number}")
                self._listen_task = asyncio.create_task(self._client.run())

                await asyncio.sleep(5)
                self._connected = True
                self._set_status(ConnectionStatus.CONNECTED)
                log_success("✅ اتصال به بله با موفقیت انجام شد")
                return True
            else:
                log_error("تنها حالت کاربر (user) پشتیبانی می‌شود")
                return False

        except Exception as e:
            log_error(f"خطا در اتصال: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def fetch_messages(self, since: datetime = None) -> List[Dict[str, Any]]:
        if not self._connected:
            return []

        new_msgs = self._messages_queue.copy()
        self._messages_queue.clear()

        if new_msgs:
            if since:
                filtered = []
                for msg in new_msgs:
                    msg_time = msg.get('timestamp')
                    if msg_time and msg_time >= since:
                        filtered.append(msg)
                    else:
                        log_warning(f"پیام {msg.get('id')} قدیمی‌تر از since است و نادیده گرفته شد")
                new_msgs = filtered
            
            if new_msgs:
                log_success(f"📥 {len(new_msgs)} پیام جدید از بله دریافت شد")
            else:
                log_info(f"پیام‌های موجود در صف همگی قدیمی‌تر از since بودند")
        
        return new_msgs

    async def disconnect(self) -> None:
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
            except:
                pass
        self._connected = False
        self._set_status(ConnectionStatus.DISCONNECTED)
        log_info("🔌 قطع ارتباط از بله")

    async def check_health(self) -> bool:
        if not self._connected:
            return False
        try:
            if self._client and hasattr(self._client, 'is_connected'):
                return self._client.is_connected
        except:
            pass
        return self._connected
    
    async def get_chats(self) -> List[Dict[str, Any]]:
        return []