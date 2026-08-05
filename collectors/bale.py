# collectors/bale.py
import asyncio
import os
import aiohttp
from datetime import datetime
from typing import List, Dict, Any, Optional
from core.base_collector import BaseCollector, ConnectionStatus
from core.logger import log_info, log_success, log_error, log_warning
from core import messenger_registry

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
        """تشخیص نوع رسانه و استخراج file_id از شیء Message (aiobale).

        نکتهٔ مهم: در aiobale هر رسانه‌ای (عکس/ویدیو/صدا/سند/گیف) فقط از طریق
        `message.document` در دسترس است — فیلدهای photo/video/audio وجود ندارند.
        نوعِ دقیق از روی mime_type/نامِ فایل حدس زده می‌شود؛ کپشن هم روی document است.
        """
        # متن خالص → بدون رسانه
        try:
            if getattr(message, "text", None):
                return "text", None, None
        except Exception:
            pass

        doc = None
        try:
            doc = getattr(message, "document", None)
        except Exception:
            doc = None
        if not doc:
            return "text", None, None

        file_id = getattr(doc, "file_id", None) or getattr(doc, "access_hash", None)
        caption = self._caption_str(getattr(doc, "caption", None))
        mime = str(getattr(doc, "mime_type", "") or "").lower()
        name = str(getattr(doc, "name", "") or "").lower()

        if "image/" in mime or name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
            media_type = "gif" if (name.endswith(".gif") or "image/gif" in mime) else "photo"
        elif "video/" in mime or name.endswith((".mp4", ".mkv", ".mov", ".avi")):
            media_type = "video"
        elif "audio/" in mime or name.endswith((".mp3", ".ogg", ".m4a", ".wav")):
            media_type = "voice" if ("ogg" in mime or name.endswith(".ogg")) else "audio"
        else:
            media_type = "document"
        return media_type, file_id, caption

    @staticmethod
    def _caption_str(caption) -> str:
        """کپشنِ aiobale ممکن است رشته یا آبجکت MessageCaption باشد."""
        if caption is None:
            return ""
        if isinstance(caption, str):
            return caption
        # MessageCaption: .text یا .value یا .text_content
        for attr in ("text", "value", "text_content"):
            try:
                v = getattr(caption, attr, None)
                if v:
                    return str(v)
            except Exception:
                continue
        return str(caption)

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

        # متن = متنِ خودِ پیام؛ اگر نبود، کپشنِ فایل (برای تطبیق کلیدواژهٔ عکس/سند)
        text_content = (getattr(message, 'text', None) or "").strip()
        if not text_content and caption:
            text_content = str(caption or "").strip()

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
                        # فیلتر زمانی (فقط بعد از since). aiobale تاریخ را datetime می‌دهد.
                        mdate = getattr(m, 'date', None)
                        try:
                            if isinstance(mdate, datetime):
                                if mdate < since:
                                    continue
                            elif isinstance(mdate, (int, float)):
                                if datetime.fromtimestamp(mdate) < since:
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
                client_kwargs = {}
                # پروکسی/فیلترشکن اختیاری از .env (BALE_PROXY یا PROXY_URL)
                proxy = (os.getenv("BALE_PROXY", "") or os.getenv("PROXY_URL", "")).strip()
                if proxy:
                    client_kwargs["proxy"] = proxy
                    log_info(f"🛡️ بله از طریق پروکسی وصل می‌شود: {proxy}")
                self._client = Client(
                    self._dispatcher,
                    session_file=self._session_path,
                    **client_kwargs,
                )

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

                # ⚠️ بررسی واقعیِ نشست: اگر نشستِ ذخیره‌شده منقضی/بی‌اعتبار باشد،
                # نباید «متصل» اعلام کنیم (قبلاً همیشه true می‌شد و هیچ پیامی نمی‌آمد).
                session_ok = False
                try:
                    me = await self._client.get_full_me()
                    if getattr(me, "user_id", None) or getattr(self._client, "me", None) or getattr(self._client, "id", None):
                        session_ok = True
                except Exception as e:
                    log_warning(f"نشست بله هنوز اعتبارسنجی نشده (طبیعی در اولین بار): {e}")
                if not session_ok:
                    # آخرین تلاش: اگر یک نشست فایل روی دیسک باشد، خودِ run() آن را می‌خواند.
                    log_info("📞 بررسی نشست بله... (اگر نشست ندارد، با `python login.py` وارد شو)")

                log_info(f"📞 در حال اتصال با شماره: {phone_number}")
                self._listen_task = asyncio.create_task(self._client.run())

                # صبر واقعی برای بالا آمدنِ websocket + اعتبارسنجی نشست
                for _ in range(10):
                    await asyncio.sleep(1)
                    if self._client.id or getattr(self._client, "me", None):
                        session_ok = True
                        break
                if not session_ok:
                    log_error(
                        "❌ نشست بله معتبر نیست یا سرور پاسخ نداد. "
                        "یک‌بار `python login.py` (گزینه‌ی ۲) را اجرا کن و کد پیامکی را وارد کن. "
                        "اگر با فیلترشکن وصل می‌شوی، BALE_PROXY را در .env بگذار."
                    )
                    await self.disconnect()
                    return False

                self._connected = True
                self._set_status(ConnectionStatus.CONNECTED)
                # برای ChatOps: ثبتِ تابعِ ارسال تا هندلرِ مدیر بتواند جواب بدهد
                messenger_registry.register_sender("bale", self.send_message)
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

    async def send_message(self, chat_id, chat_type, text: str):
        """ارسالِ پیامِ متنی (برای جوابِ ChatOps به مدیر). chat_type رشته‌ای است."""
        if not self._client or chat_id is None:
            return
        try:
            from aiobale.enums import ChatType
            ct_map = {"private": ChatType.PRIVATE, "user": ChatType.PRIVATE,
                      "group": ChatType.GROUP, "channel": ChatType.CHANNEL}
            ct = ct_map.get(str(chat_type or "private").lower(), ChatType.PRIVATE)
            await self._client.send_message(text=text, chat_id=int(chat_id), chat_type=ct)
        except Exception as e:
            log_warning(f"ارسالِ پیامِ بله ناموفق بود: {e}")

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
        """سلامتِ اتصال: `_connected` واقعی (کلاینتِ aiobale متد is_connected ندارد).
        فقط وقتی که websocket زنده است و نشست معتبر است، سالم محسوب می‌شود."""
        if not self._connected or not self._client:
            return False
        if self._listen_task and self._listen_task.done():
            # تسکِ گوش‌دادن مرده است → اتصال قطع شده
            self._connected = False
            self._set_status(ConnectionStatus.ERROR)
            return False
        return True
    
    async def get_chats(self) -> List[Dict[str, Any]]:
        return []