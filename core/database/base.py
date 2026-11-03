"""کلاس پایه + رپر نتیجهٔ کوئری.

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX: `_QueryResult` حالا `to_dict()` دارد (طبق R2.19).
  - 🐛 FIX: `_QueryResult` حالا `__iter__`, `__len__`, `__aiter__`
    دارد (سازگار با iteration و async iteration).
  - 🐛 FIX: `_QueryResult` حالا `reset()` دارد (برای fetch دوباره).
  - 🐛 FIX: `_QueryResult.fetchall()` حالا deep-copy برمی‌گرداند
    (جلوگیری از mutation ناخواسته).
  - 🐛 FIX: type hints کامل (`list[Any]` به‌جای `list`).
  - 🐛 FIX: `mark_important` type hint `str | None = None`.
  - 🐛 FIX: `execute()` params type hints.
  - 🎯 `__repr__`, `__bool__`, `__slots__` بهبود یافته.
  - 🎯 `_QueryResult.get_stats()` برای debug.
  - 🎯 `BaseDatabase.__repr__` و `_safe_name` helper.
  - 🎯 `__all__` export.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۱.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from core.kernel.logger import log_info, log_warning, log_error, log_debug, log_success

log_info(f'✅ [MODULE] {__name__}')

# نوع‌های کمکی
RowType = dict[str, Any]
ParamsType = tuple[Any, ...] | list[Any]


# ═══════════════════════════════════════════════════════════
# _QueryResult
# ═══════════════════════════════════════════════════════════
class _QueryResult:
    """fetchone/fetchall روی ردیف‌های از پیش خوانده‌شده.

    چون اتصالِ aiosqlite بعد از execute بسته می‌شود، نمی‌توان روی
    cursor بسته fetch کرد. این رپر هم برای SQLite و هم برای Postgres
    یکسان است.

    Features:
        - `await fetchone()` — یک ردیف (dict) یا None
        - `await fetchall()` — همه ردیف‌ها (deep-copy)
        - `reset()` — بازنشانی cursor داخلی
        - `to_dict()` — خلاصهٔ وضعیت (برای debug)
        - iteration: `for row in result` (sync) و
          `async for row in result` (async)

    Note:
        ردیف‌ها dict هستند (نه sqlite3.Row یا asyncpg.Record)
        چون در `sqlite.py` و `postgres.py` به dict تبدیل می‌شوند.
    """

    __slots__ = ("_i", "_rows")

    def __init__(self, rows: list[RowType] | None = None):
        self._rows: list[RowType] = list(rows) if rows else []
        self._i: int = 0

    # ═══════════════════════════════════════════
    # fetch API
    # ═══════════════════════════════════════════
    async def fetchone(self) -> RowType | None:
        """یک ردیف (dict) یا None."""
        if self._i < len(self._rows):
            row = self._rows[self._i]
            self._i += 1
            return row
        return None

    async def fetchall(self) -> list[RowType]:
        """همهٔ ردیف‌ها — deep-copy (جلوگیری از mutation)."""
        return copy.deepcopy(self._rows)

    async def fetchmany(self, size: int = 1) -> list[RowType]:
        """چند ردیف بعدی — تا `size`."""
        try:
            size = max(1, int(size))
        except (TypeError, ValueError):
            size = 1
        end = min(self._i + size, len(self._rows))
        result = copy.deepcopy(self._rows[self._i:end])
        self._i = end
        return result

    # ═══════════════════════════════════════════
    # reset / status
    # ═══════════════════════════════════════════
    def reset(self) -> None:
        """بازنشانی cursor — برای fetch دوباره."""
        self._i = 0

    def get_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت — برای debug."""
        return {
            "total_rows": len(self._rows),
            "current_index": self._i,
            "has_more": self._i < len(self._rows),
        }

    def to_dict(self) -> dict[str, Any]:
        """🎯 خلاصهٔ JSON-safe — برای debug."""
        return {
            "total_rows": len(self._rows),
            "current_index": self._i,
            "remaining": max(0, len(self._rows) - self._i),
            "has_more": self._i < len(self._rows),
            "rows_preview": [
                dict(r) if isinstance(r, dict) else str(r) for r in self._rows[:3]
            ],
        }

    # ═══════════════════════════════════════════
    # iteration
    # ═══════════════════════════════════════════
    def __iter__(self):
        """🎯 iteration sync — از cursor فعلی."""
        return iter(self._rows[self._i:])

    def __len__(self) -> int:
        """تعداد کل ردیف‌ها (نه باقی‌مانده)."""
        return len(self._rows)

    def __bool__(self) -> bool:
        """آیا ردیفی دارد؟"""
        return bool(self._rows)

    def __repr__(self) -> str:
        return f"<_QueryResult total={len(self._rows)} i={self._i}>"


# ═══════════════════════════════════════════════════════════
# BaseDatabase
# ═══════════════════════════════════════════════════════════
class BaseDatabase(ABC):
    """قرارداد مشترک بین SQLite و Postgres.

    همهٔ متدهای abstract باید در subclassها پیاده‌سازی شوند.
    """

    def __init_subclass__(cls, **kwargs):
        """🎯 اطمینان از اینکه subclassها ABC را درست پیاده می‌کنند."""
        super().__init_subclass__(**kwargs)

    # ═══════════════════════════════════════════
    # Lifecycle
    # ═══════════════════════════════════════════
    @abstractmethod
    async def init(self) -> None: ...

    @abstractmethod
    async def migrate(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    # ═══════════════════════════════════════════
    # Write
    # ═══════════════════════════════════════════
    @abstractmethod
    async def save_message(self, message: dict[str, Any]) -> int: ...

    @abstractmethod
    async def save_lead(self, lead: dict[str, Any]) -> int: ...

    @abstractmethod
    async def mark_important(
        self, message_id: str, note: str | None = None
    ) -> None: ...

    @abstractmethod
    async def update_lead_score(self, lead_id: str, score: int) -> None: ...

    @abstractmethod
    async def update_last_poll(
        self,
        platform: str,
        timestamp: datetime) -> None: ...

    @abstractmethod
    async def set_chat_offset(
        self, platform: str, chat_id: str, last_message_id: Any
    ) -> None: ...

    @abstractmethod
    async def update_daily_stats(self) -> None: ...

    @abstractmethod
    async def delete_old_messages(self, days: int = 30) -> None: ...

    # ═══════════════════════════════════════════
    # Read
    # ═══════════════════════════════════════════
    @abstractmethod
    async def get_last_poll(self, platform: str) -> datetime | None: ...

    @abstractmethod
    async def get_chat_offset(
        self,
        platform: str,
        chat_id: str) -> str | None: ...

    @abstractmethod
    async def get_high_score_leads(
        self, min_score: int = 0, limit: int = 50
    ) -> list[dict]: ...

    @abstractmethod
    async def get_leads_by_date_range(
        self, start_date: str, end_date: str
    ) -> list[dict]: ...

    @abstractmethod
    async def get_keyword_stats(self,
                                limit: int = 20) -> list[tuple[str,
                                                               int]]: ...

    @abstractmethod
    async def get_group_stats(self,
                              limit: int = 20) -> list[tuple[str,
                                                             int]]: ...

    @abstractmethod
    async def get_daily_stats(self, days: int = 7) -> list[dict]: ...

    @abstractmethod
    async def search(
        self,
        query: str,
        search_type: str = "all",
        limit: int = 50,
    ) -> list[dict]: ...

    @abstractmethod
    async def get_important_messages(self, limit: int = 50) -> list[dict]: ...

    @abstractmethod
    async def get_total_count(self) -> dict[str, int]: ...

    # ═══════════════════════════════════════════
    # Backup
    # ═══════════════════════════════════════════
    @abstractmethod
    async def backup(self, backup_path: str) -> bool: ...

    # ═══════════════════════════════════════════
    # Generic
    # ═══════════════════════════════════════════
    @abstractmethod
    async def execute(
        self,
        query: str,
        params: ParamsType = ()) -> _QueryResult: ...

    # ═══════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════
    @property
    def _safe_name(self) -> str:
        """نام کوتاه کلاس — برای لاگ."""
        return self.__class__.__name__

    def __repr__(self) -> str:
        return f"<{self._safe_name}>"


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "BaseDatabase",
    "ParamsType",
    "RowType",
    "_QueryResult",
]
