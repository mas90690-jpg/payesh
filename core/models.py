# core/models.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional


@dataclass
class Message:
    """قالب یکسان برای پیام از هر پلتفرمی"""
    platform: str
    platform_id: str
    chat_id: str
    chat_name: str
    sender_name: str
    text: str
    time: datetime = field(default_factory=datetime.now)
    raw_data: Dict = field(default_factory=dict)


@dataclass
class AnalyzedMessage(Message):
    """پیام با تحلیل کلیدواژه و متادیتا"""
    keywords: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    score: Optional[int] = None
    category: Optional[str] = None
    sentiment: Optional[str] = None
    urgency: Optional[str] = None
    location: Optional[str] = None


@dataclass
class Lead:
    """مدل سرنخ فروش"""
    id: str
    message_id: str
    platform: str
    text: str
    chat_id: str
    chat_name: str
    user_id: str
    timestamp: datetime
    keywords: List[str] = field(default_factory=list)
    score: int = 0
    category: str = ""
    status: str = "new"  # new, contacted, converted, lost
    note: str = ""