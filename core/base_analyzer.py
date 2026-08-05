# core/base_analyzer.py
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from core.models import AnalyzedMessage


class BaseAnalyzer(ABC):
    """
    کلاس پایه برای تمام آنالیزورها

    هر آنالیزور جدید باید:
    1. از این کلاس ارث‌بری کند
    2. متد analyze را پیاده‌سازی کند
    3. در صورت نیاز، متدهای add_keywords یا get_score را override کند
    """

    @abstractmethod
    async def analyze(self, message: AnalyzedMessage) -> AnalyzedMessage:
        """
        تحلیل پیام و اضافه کردن اطلاعات به آن

        Args:
            message: پیام دریافتی

        Returns:
            AnalyzedMessage: پیام با اطلاعات تحلیل شده
        """
        pass

    async def add_keywords(
        self, message: AnalyzedMessage, keywords: List[str]
    ) -> AnalyzedMessage:
        """
        اضافه کردن کلیدواژه‌ها به پیام (قابل استفاده توسط آنالیزورها)
        """
        current_keywords = set(message.keywords)
        current_keywords.update(keywords)
        message.keywords = list(current_keywords)
        return message

    async def add_metadata(
        self, message: AnalyzedMessage, key: str, value: Any
    ) -> AnalyzedMessage:
        """
        اضافه کردن متادیتا به پیام (برای ذخیره اطلاعات اضافی)
        """
        if not hasattr(message, "metadata"):
            message.metadata = {}
        message.metadata[key] = value
        return message

    async def get_score(self, message: AnalyzedMessage) -> Optional[int]:
        """
        برگرداندن امتیاز تحلیل (در صورت وجود)

        Returns:
            امتیاز بین 0 تا 100 یا None اگر امتیازی محاسبه نشده
        """
        if hasattr(message, "score"):
            return message.score
        return None

    async def combine_analyzers(
        self, message: AnalyzedMessage, analyzers: List["BaseAnalyzer"]
    ) -> AnalyzedMessage:
        """
        ترکیب چند آنالیزور روی یک پیام

        Args:
            message: پیام اصلی
            analyzers: لیست آنالیزورها

        Returns:
            پیام تحلیل شده توسط همه آنالیزورها
        """
        for analyzer in analyzers:
            try:
                message = await analyzer.analyze(message)
            except Exception as e:
                print(f"⚠️ خطا در آنالیزور {analyzer.__class__.__name__}: {e}")
        return message
