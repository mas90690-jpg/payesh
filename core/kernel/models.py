"""مدل‌های داده — قالب یکسان برای پیام و سرنخ.

سه مدل:
  - Message         → قالب خام پیام از هر پلتفرمی
  - AnalyzedMessage → پیام با کلیدواژه + متادیتا (بعد از تحلیل)
  - Lead            → سرنخ فروش (برای گزارش‌گیری)

هر سه dataclass هستند و متدهای to_dict/from_dict دارند.

نسخهٔ ۳ (این نسخه):
  - 🐛 FIX: `Lead.score` حالا `float` است (سازگار با `LeadScorer`).
  - 🐛 FIX: `Lead` حالا فیلدهای اضافی `opportunity_type`,
    `priority_tier`, `matched_products`, `brands` دارد (سازگاری
    کامل با `storage_handler._save_lead`).
  - 🐛 FIX: `Message.from_dict` / `AnalyzedMessage.from_dict` /
    `Lead.from_dict` حالا `None`-safe هستند و از helperهای
    مشترک استفاده می‌کنند (DRY).
  - 🐛 FIX: `_parse_datetime` و `_parse_keywords` helperها.
  - 🐛 FIX: `to_dict()` حالا deep-copy از `raw_data` / `metadata`
    می‌دهد (جلوگیری از mutation ناخواسته).
  - 🐛 FIX: `Message` حالا فیلدهای `chat_username`, `chat_link`,
    `chat_type` دارد (سازگار با storage).
  - 🐛 FIX: `Lead` حالا فیلدهای `is_important`, `my_note` دارد.
  - 🎯 `Lead.to_storage_dict()` متد جدید — برای ذخیره در DB.
  - 🎯 `Lead.is_hot()`, `Lead.is_warm()` helperها.
  - 🎯 `LeadStatus` حالا `FOLLOW_UP` هم دارد.
  - 🎯 `__repr__` برای همهٔ مدل‌ها.
  - 🎯 `__all__` export.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۲.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _parse_datetime(value: Any) -> datetime:
    """🎯 FIX: parse امن datetime از str/datetime/None."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(
                value.replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except Exception:
            pass
    return datetime.now()  # noqa: DTZ005


def _parse_keywords(value: Any) -> list[str]:
    """🎯 FIX: parse امن keywords از str/list/None."""
    if isinstance(value, (list, tuple, set)):
        return [
            str(k).strip() for k in value if k and str(k).strip()
        ]
    if isinstance(value, str):
        return [
            k.strip()
            for k in value.split(",")
            if k.strip()
        ]
    return []


def _safe_dict(value: Any) -> dict[str, Any]:
    """🎯 FIX: deep-copy dict امن."""
    if not isinstance(value, dict):
        return {}
    try:
        return copy.deepcopy(value)
    except Exception:
        return dict(value)


def _safe_str(value: Any, default: str = "") -> str:
    """تبدیل امن به str."""
    if value is None:
        return default
    try:
        return str(value)
    except Exception:
        return default


# ═══════════════════════════════════════════
# Enum وضعیت سرنخ
# ═══════════════════════════════════════════
class LeadStatus(str, Enum):
    """وضعیت سرنخ در قیف فروش.

    ⚠️ از `str, Enum` استفاده می‌کنیم (نه `StrEnum`) تا در
    Python 3.10 هم کار کند.
    """

    NEW = "new"
    CONTACTED = "contacted"
    FOLLOW_UP = "follow_up"
    CONVERTED = "converted"
    LOST = "lost"

    def __str__(self) -> str:
        return self.value


# ═══════════════════════════════════════════
# Message
# ═══════════════════════════════════════════
@dataclass
class Message:
    """قالب یکسان پیام از هر پلتفرمی."""

    platform: str = ""
    platform_id: str = ""
    chat_id: str = ""
    chat_name: str = "Unknown"
    sender_name: str = ""
    text: str = ""
    time: datetime = field(default_factory=datetime.now)
    raw_data: dict[str, Any] = field(default_factory=dict)

    # 🎯 فیلدهای اضافی (سازگار با storage)
    chat_username: str | None = None
    chat_link: str | None = None
    chat_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """تبدیل به dict — مناسب JSON."""
        return {
            "platform": self.platform,
            "platform_id": self.platform_id,
            "chat_id": self.chat_id,
            "chat_name": self.chat_name,
            "chat_username": self.chat_username,
            "chat_link": self.chat_link,
            "chat_type": self.chat_type,
            "sender_name": self.sender_name,
            "text": self.text,
            "time": (
                self.time.isoformat()
                if isinstance(self.time, datetime)
                else _safe_str(self.time)
            ),
            # 🎯 FIX: deep-copy
            "raw_data": _safe_dict(self.raw_data),
        }

    @classmethod
    def from_dict(
        cls, data: dict[str, Any] | None
    ) -> Message:
        """بازسازی از dict — None-safe."""
        if not isinstance(data, dict):
            return cls()

        return cls(
            platform=_safe_str(data.get("platform", "")),
            platform_id=_safe_str(data.get("platform_id", "")),
            chat_id=_safe_str(data.get("chat_id", "")),
            chat_name=_safe_str(
                data.get("chat_name", "Unknown")
            ) or "Unknown",
            sender_name=_safe_str(data.get("sender_name", "")),
            text=_safe_str(data.get("text", "")),
            time=_parse_datetime(data.get("time")),
            raw_data=_safe_dict(data.get("raw_data")),
            chat_username=data.get("chat_username"),
            chat_link=data.get("chat_link"),
            chat_type=data.get("chat_type"),
        )

    def __repr__(self) -> str:
        return (
            f"Message(platform={self.platform!r}, "
            f"chat={self.chat_name!r}, "
            f"len(text)={len(self.text)})"
        )


# ═══════════════════════════════════════════
# AnalyzedMessage
# ═══════════════════════════════════════════
@dataclass
class AnalyzedMessage(Message):
    """پیام با تحلیل کلیدواژه و متادیتا."""

    keywords: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    category: str | None = None
    sentiment: str | None = None
    urgency: str | None = None
    location: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """تبدیل به dict — شامل فیلدهای تحلیل."""
        base = super().to_dict()
        base.update({
            "keywords": list(self.keywords or []),
            # 🎯 FIX: deep-copy
            "metadata": _safe_dict(self.metadata),
            "score": self.score,
            "category": self.category,
            "sentiment": self.sentiment,
            "urgency": self.urgency,
            "location": self.location,
        })
        return base

    @classmethod
    def from_dict(
        cls, data: dict[str, Any] | None
    ) -> AnalyzedMessage:
        """بازسازی از dict — None-safe."""
        if not isinstance(data, dict):
            return cls()

        # 🐛 FIX: استفاده از helperهای مشترک
        score_raw = data.get("score")
        score: float | None = None
        if score_raw is not None:
            try:
                score = float(score_raw)
            except (TypeError, ValueError):
                score = None

        return cls(
            platform=_safe_str(data.get("platform", "")),
            platform_id=_safe_str(data.get("platform_id", "")),
            chat_id=_safe_str(data.get("chat_id", "")),
            chat_name=_safe_str(
                data.get("chat_name", "Unknown")
            ) or "Unknown",
            sender_name=_safe_str(data.get("sender_name", "")),
            text=_safe_str(data.get("text", "")),
            time=_parse_datetime(data.get("time")),
            raw_data=_safe_dict(data.get("raw_data")),
            chat_username=data.get("chat_username"),
            chat_link=data.get("chat_link"),
            chat_type=data.get("chat_type"),
            keywords=_parse_keywords(data.get("keywords")),
            metadata=_safe_dict(data.get("metadata")),
            score=score,
            category=data.get("category"),
            sentiment=data.get("sentiment"),
            urgency=data.get("urgency"),
            location=data.get("location"),
        )

    def __repr__(self) -> str:
        return (
            f"AnalyzedMessage(platform={self.platform!r}, "
            f"chat={self.chat_name!r}, "
            f"kw={len(self.keywords)}, "
            f"score={self.score})"
        )


# ═══════════════════════════════════════════
# Lead
# ═══════════════════════════════════════════
@dataclass
class Lead:
    """مدل سرنخ فروش.

    ⚠️ در `storage_handler._save_lead`، سرنخ به‌صورت **dict** ساخته
    می‌شود. برای سازگاری کامل، از `Lead.from_lead_dict()` و
    `Lead.to_storage_dict()` استفاده کن.
    """

    id: str = ""
    message_id: str = ""
    platform: str = ""
    text: str = ""
    chat_id: str = ""
    chat_name: str = "Unknown"
    user_id: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    keywords: list[str] = field(default_factory=list)
    # 🐛 FIX: score از int به float
    score: float = 0.0
    category: str = ""
    status: LeadStatus = LeadStatus.NEW
    note: str = ""

    # 🐛 FIX: فیلدهای اضافی storage
    opportunity_type: str | None = None
    priority_tier: str = "T0"
    matched_products: str = ""
    brands: str = ""
    media_type: str = "text"
    file_id: str | None = None
    is_important: bool = False
    my_note: str | None = None

    # ─── helpers ───
    def is_hot(self) -> bool:
        """🎯 آیا این سرنخ داغ است؟"""
        return self.score >= 60

    def is_warm(self) -> bool:
        """🎯 آیا این سرنخ گرم است؟"""
        return 30 <= self.score < 60

    def is_cold(self) -> bool:
        """🎯 آیا این سرنخ سرد است؟"""
        return self.score < 30

    def to_dict(self) -> dict[str, Any]:
        """تبدیل به dict — keywords به string با کاما."""
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
                else _safe_str(self.timestamp)
            ),
            "keywords": ",".join(self.keywords or []),
            "score": self.score,
            "category": self.category,
            "status": (
                self.status.value
                if isinstance(self.status, LeadStatus)
                else _safe_str(self.status)
            ),
            "note": self.note,
            # 🎯 فیلدهای اضافی
            "opportunity_type": self.opportunity_type,
            "priority_tier": self.priority_tier,
            "matched_products": self.matched_products,
            "brands": self.brands,
            "media_type": self.media_type,
            "file_id": self.file_id,
            "is_important": self.is_important,
            "my_note": self.my_note,
        }

    def to_storage_dict(self) -> dict[str, Any]:
        """🎯 dict سازگار با `storage_handler` و DB.

        کلیدها: id, message_id, platform, text, chat_id, chat_name,
        user_id, timestamp, keywords (str), opportunity_score,
        opportunity_type, priority_tier, matched_products, brands,
        media_type, file_id, is_important, my_note.
        """
        return {
            "id": self.id,
            "message_id": self.message_id,
            "platform": self.platform,
            "text": self.text,
            "chat_id": self.chat_id,
            "chat_name": self.chat_name,
            "user_id": self.user_id,
            "timestamp": self.timestamp,
            "keywords": ",".join(self.keywords or []),
            "opportunity_score": int(self.score or 0),
            "opportunity_type": self.opportunity_type,
            "priority_tier": self.priority_tier,
            "matched_products": self.matched_products,
            "brands": self.brands,
            "media_type": self.media_type,
            "file_id": self.file_id,
            "is_important": 1 if self.is_important else 0,
            "my_note": self.my_note,
        }

    @classmethod
    def from_dict(
        cls, data: dict[str, Any] | None
    ) -> Lead:
        """بازسازی از dict (چکیده از دیتابیس)."""
        if not isinstance(data, dict):
            return cls()

        # ─── keywords ───
        keywords = _parse_keywords(data.get("keywords"))

        # ─── timestamp ───
        timestamp = _parse_datetime(
            data.get("timestamp") or data.get("time")
        )

        # ─── status ───
        status_raw = data.get("status", "new")
        try:
            status = LeadStatus(status_raw)
        except (ValueError, TypeError):
            status = LeadStatus.NEW

        # ─── score ───
        score_raw = data.get(
            "opportunity_score", data.get("score", 0)
        )
        try:
            score = float(score_raw or 0)
        except (TypeError, ValueError):
            score = 0.0

        # ─── is_important ───
        is_imp_raw = data.get("is_important", False)
        is_important = bool(is_imp_raw)

        return cls(
            id=_safe_str(data.get("id", "")),
            message_id=_safe_str(
                data.get("message_id") or data.get("id", "")
            ),
            platform=_safe_str(data.get("platform", "")),
            text=_safe_str(data.get("text", "")),
            chat_id=_safe_str(data.get("chat_id", "")),
            chat_name=_safe_str(
                data.get("chat_name", "Unknown")
            ) or "Unknown",
            user_id=_safe_str(data.get("user_id", "")),
            timestamp=timestamp,
            keywords=keywords,
            score=score,
            category=_safe_str(data.get("category", "")),
            status=status,
            note=_safe_str(data.get("note", "")),
            opportunity_type=data.get("opportunity_type"),
            priority_tier=_safe_str(
                data.get("priority_tier", "T0")
            ) or "T0",
            matched_products=_safe_str(
                data.get("matched_products", "")
            ),
            brands=_safe_str(data.get("brands", "")),
            media_type=_safe_str(
                data.get("media_type", "text")
            ) or "text",
            file_id=data.get("file_id"),
            is_important=is_important,
            my_note=data.get("my_note"),
        )

    @classmethod
    def from_lead_dict(
        cls, data: dict[str, Any] | None
    ) -> Lead:
        """🎯 سازگاری با `storage_handler._save_lead`.

        کلیدهای پشتیبانی‌شده:
          - id, message_id, platform, text, chat_id, chat_name,
            user_id
          - keywords (str با کاما یا list)
          - opportunity_score یا score → self.score
          - opportunity_type → self.opportunity_type
          - priority_tier → self.priority_tier
          - matched_products, brands → self.*
          - timestamp یا time یا created_at
        """
        if not isinstance(data, dict):
            return cls()

        # ─── keywords ───
        keywords = _parse_keywords(data.get("keywords"))

        # ─── score ───
        score_raw = data.get(
            "opportunity_score", data.get("score", 0)
        )
        try:
            score = float(score_raw or 0)
        except (TypeError, ValueError):
            score = 0.0

        # ─── timestamp ───
        timestamp = _parse_datetime(
            data.get("timestamp")
            or data.get("time")
            or data.get("created_at")
        )

        # ─── note ───
        opp_type = data.get("opportunity_type")
        note = _safe_str(data.get("note", ""))
        if not note and opp_type:
            note = f"نوع: {opp_type}"

        # ─── is_important ───
        is_important = bool(data.get("is_important", False))

        return cls(
            id=_safe_str(data.get("id", "")),
            message_id=_safe_str(
                data.get("message_id") or data.get("id", "")
            ),
            platform=_safe_str(data.get("platform", "")),
            text=_safe_str(data.get("text", "")),
            chat_id=_safe_str(data.get("chat_id", "")),
            chat_name=_safe_str(
                data.get("chat_name", "Unknown")
            ) or "Unknown",
            user_id=_safe_str(data.get("user_id", "")),
            timestamp=timestamp,
            keywords=keywords,
            score=score,
            category=_safe_str(data.get("category", "")),
            status=LeadStatus.NEW,
            note=note,
            opportunity_type=opp_type,
            priority_tier=_safe_str(
                data.get("priority_tier", "T0")
            ) or "T0",
            matched_products=_safe_str(
                data.get("matched_products", "")
            ),
            brands=_safe_str(data.get("brands", "")),
            media_type=_safe_str(
                data.get("media_type", "text")
            ) or "text",
            file_id=data.get("file_id"),
            is_important=is_important,
            my_note=data.get("my_note"),
        )

    def __repr__(self) -> str:
        return (
            f"Lead(id={self.id!r}, "
            f"platform={self.platform!r}, "
            f"kw={len(self.keywords)}, "
            f"score={self.score}, "
            f"status={self.status.value})"
        )


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "AnalyzedMessage",
    "Lead",
    "LeadStatus",
    "Message",
]