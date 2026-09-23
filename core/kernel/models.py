# core/models.py
"""
مدل‌های داده — قالب یکسان برای پیام و سرنخ.

سه مدل:
  - Message         → قالب خام پیام از هر پلتفرمی
  - AnalyzedMessage → پیام با کلیدواژه + متادیتا (بعد از تحلیل)
  - Lead            → سرنخ فروش (برای گزارش‌گیری)

هر سه dataclass هستند و متدهای to_dict/from_dict دارند.

نسخهٔ ۲ (این نسخه):
  - 🎯 to_dict() / from_dict() برای JSON round-trip
  - 🎯 from_lead_dict() برای سازگاری با storage_handler (که keywords
    رو به‌صورت string پاس می‌ده)
  - 🐛 FIX: raw_data: Dict[str, Any] (به‌جای Dict بدون type args)
  - 🐛 FIX: score: Optional[float] (نه فقط int — سازگار با LeadScorer)
  - 🎯 default_timezone helper (datetime.now بدون TZ → ok، ولی مستند)
  - 🧹 from __future__ import annotations
  - 🧹 type hints در همه‌جا
  - 🧹 مستندسازی platform_id (storage از id استفاده می‌کنه)
  - 🧹 Lead.status حالا با Enum (LeadStatus) برای type-safety
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


# ═══════════════════════════════════════════
# Enum وضعیت سرنخ
# ═══════════════════════════════════════════
class LeadStatus(StrEnum):
    """وضعیت سرنخ در قیف فروش."""

    NEW = "new"
    CONTACTED = "contacted"
    CONVERTED = "converted"
    LOST = "lost"


# ═══════════════════════════════════════════
# Message
# ═══════════════════════════════════════════
@dataclass
class Message:
    """قالب یکسان پیام از هر پلتفرمی.

    فیلدها:
        platform:      نام پلتفرم (bale/eitaa/telegram/...)
        platform_id:   شناسه پیام در پلتفرم مبدأ
                       (⚠️ storage_handler از `id` استفاده می‌کند که
                        با `platform_{id}` یکتا می‌شود. این نام legacy است.)
        chat_id:       شناسه چت در پلتفرم مبدأ
        chat_name:     نام نمایشی چت
        sender_name:   نام فرستنده
        text:          متن پیام
        time:          زمان پیام (naive، local timezone)
        raw_data:      آبجکت خام از SDK پلتفرم (dict/obj)
    """

    platform: str
    platform_id: str
    chat_id: str
    chat_name: str
    sender_name: str
    text: str
    time: datetime = field(default_factory=datetime.now)
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """تبدیل به dict — مناسب JSON (datetime → ISO)."""
        return {
            "platform": self.platform,
            "platform_id": self.platform_id,
            "chat_id": self.chat_id,
            "chat_name": self.chat_name,
            "sender_name": self.sender_name,
            "text": self.text,
            "time": (
                self.time.isoformat()
                if isinstance(self.time, datetime)
                else self.time
            ),
            "raw_data": self.raw_data,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        """بازسازی از dict."""
        t = data.get("time")
        if isinstance(t, str):
            try:
                t = datetime.fromisoformat(t)
            except Exception:
                t = datetime.now()
        return cls(
            platform=str(data.get("platform", "")),
            platform_id=str(data.get("platform_id", "")),
            chat_id=str(data.get("chat_id", "")),
            chat_name=str(data.get("chat_name", "Unknown")),
            sender_name=str(data.get("sender_name", "")),
            text=str(data.get("text", "")),
            time=t or datetime.now(),
            raw_data=data.get("raw_data") or {},
        )


# ═══════════════════════════════════════════
# AnalyzedMessage
# ═══════════════════════════════════════════
@dataclass
class AnalyzedMessage(Message):
    """پیام با تحلیل کلیدواژه و متادیتا."""

    keywords: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float | None = None         # ← FIX: int → float
    category: str | None = None
    sentiment: str | None = None
    urgency: str | None = None
    location: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """تبدیل به dict — شامل فیلدهای تحلیل."""
        base = super().to_dict()
        base.update({
            "keywords": list(self.keywords or []),
            "metadata": dict(self.metadata or {}),
            "score": self.score,
            "category": self.category,
            "sentiment": self.sentiment,
            "urgency": self.urgency,
            "location": self.location,
        })
        return base

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnalyzedMessage:
        """بازسازی از dict."""
        t = data.get("time")
        if isinstance(t, str):
            try:
                t = datetime.fromisoformat(t)
            except Exception:
                t = datetime.now()
        return cls(
            platform=str(data.get("platform", "")),
            platform_id=str(data.get("platform_id", "")),
            chat_id=str(data.get("chat_id", "")),
            chat_name=str(data.get("chat_name", "Unknown")),
            sender_name=str(data.get("sender_name", "")),
            text=str(data.get("text", "")),
            time=t or datetime.now(),
            raw_data=data.get("raw_data") or {},
            keywords=list(data.get("keywords") or []),
            metadata=dict(data.get("metadata") or {}),
            score=data.get("score"),
            category=data.get("category"),
            sentiment=data.get("sentiment"),
            urgency=data.get("urgency"),
            location=data.get("location"),
        )


# ═══════════════════════════════════════════
# Lead
# ═══════════════════════════════════════════
@dataclass
class Lead:
    """مدل سرنخ فروش.

    ⚠️ نکته مهم: در `storage_handler._save_lead`، سرنخ به‌صورت **dict** ساخته
    می‌شود (نه با این dataclass) و `keywords` به‌صورت **string** (با کاما)
    پاس داده می‌شود. برای سازگاری، از `Lead.from_lead_dict()` استفاده کن.
    """

    id: str
    message_id: str
    platform: str
    text: str
    chat_id: str
    chat_name: str
    user_id: str
    timestamp: datetime
    keywords: list[str] = field(default_factory=list)
    score: int = 0
    category: str = ""
    status: LeadStatus = LeadStatus.NEW
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """تبدیل به dict — keywords به string با کاما (مثل storage)."""
        return {
            "id": self.id,
            "message_id": self.message_id,
            "platform": self.platform,
            "text": self.text,
            "chat_id": self.chat_id,
            "chat_name": self.chat_name,
            "user_id": self.user_id,
            "timestamp": (
                self.timestamp.isoformat()
                if isinstance(self.timestamp, datetime)
                else self.timestamp
            ),
            "keywords": ",".join(self.keywords or []),
            "score": self.score,
            "category": self.category,
            "status": (
                self.status.value
                if isinstance(self.status, LeadStatus)
                else str(self.status)
            ),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Lead:
        """بازسازی از dict (چکیده از دیتابیس)."""
        # keywords ممکنه str یا list باشه
        kw_raw = data.get("keywords", "")
        if isinstance(kw_raw, list):
            keywords = [str(k).strip() for k in kw_raw if k]
        else:
            keywords = [
                k.strip() for k in str(kw_raw or "").split(",") if k.strip()
            ]

        # timestamp
        ts = data.get("timestamp")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except Exception:
                ts = datetime.now()
        elif not isinstance(ts, datetime):
            ts = datetime.now()

        # status
        status_raw = data.get("status", "new")
        try:
            status = LeadStatus(status_raw)
        except (ValueError, TypeError):
            status = LeadStatus.NEW

        return cls(
            id=str(data.get("id", "")),
            message_id=str(data.get("message_id", "")),
            platform=str(data.get("platform", "")),
            text=str(data.get("text", "")),
            chat_id=str(data.get("chat_id", "")),
            chat_name=str(data.get("chat_name", "Unknown")),
            user_id=str(data.get("user_id", "")),
            timestamp=ts,
            keywords=keywords,
            score=int(data.get("score", 0) or 0),
            category=str(data.get("category", "")),
            status=status,
            note=str(data.get("note", "")),
        )

    @classmethod
    def from_lead_dict(cls, data: dict[str, Any]) -> Lead:
        """سازگاری با `storage_handler._save_lead` که کلیدهای متفاوتی
        پاس می‌دهد (opportunity_score, opportunity_type, priority_tier, ...).

        کلیدهای پشتیبانی‌شده:
          - id, message_id, platform, text, chat_id, chat_name, user_id
          - keywords (str با کاما یا list)
          - opportunity_score یا score → self.score
          - opportunity_type → self.note (پیش‌فرض)
          - timestamp یا time
        """
        # keywords
        kw_raw = data.get("keywords", "")
        if isinstance(kw_raw, list):
            keywords = [str(k).strip() for k in kw_raw if k]
        else:
            keywords = [
                k.strip()
                for k in str(kw_raw or "").split(",")
                if k.strip()
            ]

        # score از opportunity_score یا score
        score = data.get("opportunity_score", data.get("score", 0))

        # timestamp
        ts = data.get("timestamp") or data.get("time")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except Exception:
                ts = datetime.now()
        elif not isinstance(ts, datetime):
            ts = datetime.now()

        # note از opportunity_type (با پیشوند)
        opp_type = data.get("opportunity_type")
        note = str(data.get("note", ""))
        if opp_type and not note:
            note = f"نوع: {opp_type}"

        return cls(
            id=str(data.get("id", "")),
            message_id=str(data.get("message_id") or data.get("id", "")),
            platform=str(data.get("platform", "")),
            text=str(data.get("text", "")),
            chat_id=str(data.get("chat_id", "")),
            chat_name=str(data.get("chat_name", "Unknown")),
            user_id=str(data.get("user_id", "")),
            timestamp=ts,
            keywords=keywords,
            score=int(score or 0),
            category=str(data.get("category", "")),
            status=LeadStatus.NEW,
            note=note,
        )
