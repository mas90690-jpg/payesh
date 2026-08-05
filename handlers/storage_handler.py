# handlers/storage_handler.py
from core.base_handler import BaseHandler
from analyzers.keyword_finder import get_keyword_finder
from datetime import datetime


class StorageHandler(BaseHandler):
    def __init__(self, db, scorer=None, **kwargs):
        self.db = db
        # همان instance مشترکی که کلکتورها هم برای گیتِ دانلود عکس استفاده می‌کنند
        self.keyword_finder = get_keyword_finder()
        # فقط پیام‌های کلیدواژه‌دار ذخیره شوند (پیش‌فرض True؛ از config/.env)
        from config import PLUGINS_CONFIG

        self.keywords_only = PLUGINS_CONFIG.get("storagehandler", {}).get(
            "keywords_only", True
        )
        # تفکیکِ منطقِ مدیر از پایش: پیامِ خصوصیِ «مدیر» نباید به‌عنوان سرنخ ذخیره شود
        # (آن دستور است، نه سرنخ). مدیر فقط با آیدیِ env شناخته می‌شود.
        _adm = PLUGINS_CONFIG.get("adminhandler", {})
        self._admin_ids = set(str(x) for x in _adm.get("admin_ids", []))
        self._admin_platform = _adm.get("platform", "bale")
        self.stats = {
            "total": 0,
            "messages_with_keywords": 0,
            "media_messages": 0,
            "dropped": 0,
        }

    @property
    def handler_name(self):
        return "storage_handler"

    def can_handle(self, event_name):
        return event_name == "message.received"

    async def handle(self, event):
        raw = event.data

        # اگر دیتا دیکشنری نبود، تبدیل کن
        if not isinstance(raw, dict):
            raw = {
                "id": getattr(raw, "id", None),
                "text": getattr(raw, "text", ""),
                "chat_id": getattr(raw, "chat_id", ""),
                "chat_name": getattr(raw, "chat_name", "Unknown"),
                "user_id": getattr(raw, "user_id", "unknown"),
                "platform": getattr(raw, "platform", "bale"),
                "timestamp": datetime.now(),
                "media_type": getattr(raw, "media_type", "text"),
                "file_id": getattr(raw, "file_id", None),
                "caption": getattr(raw, "caption", None),
                "reply_to": getattr(raw, "reply_to", None),
            }

        # 🔒 تفکیکِ مدیر از پایش: اگر این پیامِ خصوصیِ یک «مدیر» روی پلتفرمِ مدیریت است،
        # سرنخ نیست (دستور است) → ذخیره نکن. (شماره‌ی پایش‌گر هرگز مدیر نیست و برعکس.)
        _u = str(raw.get("user_id") or "")
        if (
            self._admin_ids
            and raw.get("platform") == self._admin_platform
            and _u in self._admin_ids
        ):
            return  # پیامِ مدیر = دستور برای هوش، نه سرنخ

        # 🎯 هدف‌گیریِ عقاب: فقط گروه‌های هدف (data/target_groups.txt). گروه‌های تبلیغاتی/
        # اجباری نادیده گرفته می‌شوند. فقط برای پیام‌رسان‌ها (نه وب/لینکدین). فهرستِ خالی = همه.
        if raw.get("platform") in ("eitaa", "bale", "rubika", "telegram", "whatsapp"):
            from analyzers.target_filter import get_target_groups

            tg = get_target_groups()
            if tg.enabled() and not tg.is_target(
                raw.get("chat_name"), raw.get("chat_username")
            ):
                return  # گروهِ غیرهدف → نادیده

        # ساخت شیء AnalyzedMessage
        from core.models import AnalyzedMessage

        analyzed = AnalyzedMessage(
            platform=raw.get("platform"),
            platform_id=raw.get("id"),
            chat_id=raw.get("chat_id"),
            chat_name=raw.get("chat_name"),
            sender_name=raw.get("user_id"),
            text=raw.get("text")
            or raw.get("caption", ""),  # اگر متن نداشت، از caption استفاده کن
            time=raw.get("timestamp"),
        )
        analyzed = await self.keyword_finder.analyze(analyzed)

        # برخی کلکتورها (مثل لینکدین) خودشان کلیدواژه/عبارتِ مطابقت‌یافته را ست می‌کنند
        # (مثلاً عبارتِ سرچی که پست با آن پیدا شده). آن‌ها را با یافته‌های finder ادغام کن.
        preset = raw.get("keywords")
        if preset:
            merged = set(analyzed.keywords)
            merged.update(k.strip() for k in str(preset).split(",") if k.strip())
            analyzed.keywords = sorted(merged)

        # 🔑 هستهٔ اصلاح: اگر پیام هیچ کلیدواژه‌ای ندارد، اصلاً ذخیره نکن.
        # (قبلاً همهٔ پیام‌ها — حتی بی‌ربط — در جدول messages ذخیره می‌شدند.)
        self.stats["total"] += 1
        if self.keywords_only and not analyzed.keywords:
            self.stats["dropped"] += 1
            if self.stats["dropped"] % 200 == 0:
                print(
                    f"🚮 {self.stats['dropped']} پیام بدون کلیدواژه نادیده گرفته شد (ذخیره نشد)"
                )
            return

        # اضافه کردن کلمات کلیدی و اطلاعات رسانه به raw
        raw["keywords"] = ",".join(analyzed.keywords) if analyzed.keywords else ""

        # امتیازدهیِ سرنخ بر اساسِ قصدِ خرید (داغ/گرم/سرد) — برای اولویت‌بندیِ فروش
        try:
            from analyzers.lead_scorer import get_lead_scorer

            score, level, _signals = get_lead_scorer().score(
                analyzed.text, has_keyword=True
            )
            raw["opportunity_score"] = score
            raw["opportunity_type"] = level
        except Exception:
            raw["opportunity_score"], raw["opportunity_type"] = 0, None

        # شناسه‌ی یکتا بین پلتفرم‌ها (جلوگیری از تداخل id دو پیام‌رسان مختلف)
        platform = raw.get("platform") or "unknown"
        if raw.get("id") and not str(raw["id"]).startswith(f"{platform}_"):
            raw["platform_message_id"] = raw["id"]
            raw["id"] = f"{platform}_{raw['id']}"

        # ذخیره در جدول messages (با keywords و media info)
        try:
            await self.db.save_message(raw)
        except Exception as e:
            print(f"⚠️ خطا در save_message: {e}")
            return

        # اگر پیام حاوی رسانه است، آمار را به‌روز کن
        if raw.get("media_type") and raw["media_type"] != "text":
            self.stats["media_messages"] += 1
            print(
                f"\n📎 پیام حاوی رسانه: {raw['media_type']} از گروه {raw.get('chat_name')}"
            )

        # اگر کلیدواژه‌ای پیدا شد، در جدول leads ذخیره کن
        if analyzed.keywords:
            lead = {
                "id": raw.get("id"),
                "message_id": raw.get("id"),  # پیوند به رکوردِ پیام (FK + ردگیری در CRM)
                "platform": raw.get("platform"),
                "text": raw.get("text") or raw.get("caption", ""),
                "chat_id": raw.get("chat_id"),
                "chat_name": raw.get("chat_name"),
                "user_id": raw.get("user_id"),
                "timestamp": raw.get("timestamp"),
                "keywords": ",".join(analyzed.keywords),
                "media_type": raw.get("media_type", "text"),
                "file_id": raw.get("file_id"),
                "opportunity_score": raw.get("opportunity_score", 0),
                "opportunity_type": raw.get("opportunity_type"),
            }
            try:
                await self.db.save_lead(lead)
            except Exception as e:
                print(f"⚠️ خطا در save_lead: {e}")

            # 🔔 هشدارِ زندهٔ سرنخ به مدیر در بله (برای رصدِ زنده/رونمایی) — fire-and-forget
            try:
                self._alert_lead(raw, analyzed.keywords)
            except Exception:
                pass

            self.stats["messages_with_keywords"] += 1

            # نمایش در کنسول
            print(f"\n🔑 کلیدواژه پیدا شد: {analyzed.keywords}")
            print(f"   پلتفرم: {raw.get('platform')}")
            print(f"   گروه: {raw.get('chat_name')}")
            if raw.get("media_type") != "text":
                print(f"   نوع رسانه: {raw.get('media_type')}")
            print(f"   متن: {raw.get('text', raw.get('caption', ''))[:150]}")

        # هر ۲۵ سرنخ، آمار را نمایش بده
        if (
            self.stats["messages_with_keywords"] % 25 == 0
            and self.stats["messages_with_keywords"] > 0
        ):
            print(
                f"\n📊 آمار StorageHandler: {self.stats['total']} پیام دیده‌شده، "
                f"{self.stats['messages_with_keywords']} سرنخ ذخیره‌شده، "
                f"{self.stats['dropped']} بی‌کلیدواژه (نادیده)، "
                f"{self.stats['media_messages']} رسانه"
            )

    def _alert_lead(self, raw, keywords):
        """هشدارِ زندهٔ سرنخ به مدیر در بله — «الان در فلان پلتفرم/گروه کلیدواژه دیده شد»."""
        import asyncio
        from config import LEAD_ALERTS

        if not LEAD_ALERTS.get("enabled", True):
            return
        score = int(raw.get("opportunity_score") or 0)
        if score < LEAD_ALERTS.get("min_score", 0):
            return
        try:
            from core.daily_reporter import platform_label

            plabel = platform_label(raw.get("platform"))
        except Exception:
            plabel = raw.get("platform")
        text = (
            "🔔 سرنخ جدید پیدا شد\n"
            f"{plabel}\n"
            f"👥 گروه: {raw.get('chat_name')}\n"
            f"🔑 کلیدواژه: {raw.get('keywords')}\n"
            f"🎯 {raw.get('opportunity_type') or '-'} (امتیاز {score})\n"
            f"💬 {(raw.get('text') or raw.get('caption') or '')[:200]}\n"
            f"🕒 {raw.get('timestamp')}"
        )
        if raw.get("chat_link"):
            text += f"\n🔗 {raw.get('chat_link')}"
        from core import messenger_registry

        try:
            asyncio.get_running_loop().create_task(
                messenger_registry.notify_admins(text)
            )
        except RuntimeError:
            pass  # خارج از event loop (مثلاً در تست) → بی‌خیال
