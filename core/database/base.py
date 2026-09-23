"""کلاس پایه + رپر نتیجهٔ کوئری."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any


class _QueryResult:
    """fetchone/fetchall روی ردیف‌های از پیش خوانده‌شده.

    چون اتصالِ aiosqlite بعد از execute بسته می‌شود، نمی‌توان روی cursor
    بسته fetch کرد. این رپر هم برای SQLite و هم برای Postgres یکسان است.
    """

    __slots__ = ("_rows", "_i")

    def __init__(self, rows: list):
        self._rows = rows or []
        self._i = 0

    async def fetchone(self):
        if self._i < len(self._rows):
            row = self._rows[self._i]
            self._i += 1
            return row
        return None

    async def fetchall(self):
        return self._rows


class BaseDatabase(ABC):
    """قرارداد مشترک بین SQLite و Postgres."""

    @abstractmethod
    async def init(self): ...

    @abstractmethod
    async def migrate(self): ...

    @abstractmethod
    async def save_message(self, message: dict[str, Any]) -> int: ...

    @abstractmethod
    async def save_lead(self, lead: dict[str, Any]) -> int: ...

    @abstractmethod
    async def get_last_poll(self, platform: str) -> datetime | None: ...

    @abstractmethod
    async def update_last_poll(self, platform: str, timestamp: datetime): ...

    @abstractmethod
    async def get_chat_offset(
        self, platform: str, chat_id: str
    ) -> str | None: ...

    @abstractmethod
    async def set_chat_offset(
        self, platform: str, chat_id: str, last_message_id
    ): ...

    @abstractmethod
    async def get_high_score_leads(
        self, min_score: int = 0, limit: int = 50
    ) -> list[dict]: ...

    @abstractmethod
    async def get_leads_by_date_range(
        self, start_date: str, end_date: str
    ) -> list[dict]: ...

    @abstractmethod
    async def get_keyword_stats(
        self, limit: int = 20
    ) -> list[tuple[str, int]]: ...

    @abstractmethod
    async def get_group_stats(
        self, limit: int = 20
    ) -> list[tuple[str, int]]: ...

    @abstractmethod
    async def get_daily_stats(
        self, days: int = 7
    ) -> list[dict]: ...

    @abstractmethod
    async def search(
        self, query: str, search_type: str = "all", limit: int = 50
    ) -> list[dict]: ...

    @abstractmethod
    async def mark_important(
        self, message_id: str, note: str = None
    ): ...

    @abstractmethod
    async def get_important_messages(
        self, limit: int = 50
    ) -> list[dict]: ...

    @abstractmethod
    async def update_lead_score(self, lead_id: str, score: int): ...

    @abstractmethod
    async def update_daily_stats(self): ...

    @abstractmethod
    async def get_total_count(self) -> dict[str, int]: ...

    @abstractmethod
    async def delete_old_messages(self, days: int = 30): ...

    @abstractmethod
    async def backup(self, backup_path: str) -> bool: ...

    @abstractmethod
    async def execute(
        self, query: str, params=()
    ) -> _QueryResult: ...

    @abstractmethod
    async def close(self): ...
