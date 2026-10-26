# tests/conftest.py
"""
═══════════════════════════════════════════════════════════════════════════
Fixtureهای مشترک پروژه — Payesh Plat
═══════════════════════════════════════════════════════════════════════════

قوانین:
  ✅ هر fixture مستقل و بدون side-effect روی بقیه
  ✅ DB موقت (tmp_path) — هیچ فایل واقعی دست نمی‌خوره
  ✅ Async-first (pytest-asyncio mode=auto)
  ✅ Singleton-safe (reset قبل از هر تست)

ساختار:
  ۱) پایه (root, tmp paths)
  ۲) DB (sqlite, temp_db)
  ۳) پیام‌ها (fake_message, factory)
  ۴) Handlerها (storage_handler)
  ۵) Mockها (MockAI, MockDB, MockPage)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ═══════════════════════════════════════════════════════════════════════════
# مسیر پروژه
# ═══════════════════════════════════════════════════════════════════════════
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ═══════════════════════════════════════════════════════════════════════════
# ۱) Fixtureهای پایه
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="session")
def project_root() -> Path:
    """ریشه پروژه."""
    return ROOT


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """مسیر DB موقت (فایل واقعی هنوز ساخته نشده)."""
    return tmp_path / "test.db"


@pytest.fixture
def tmp_formulas_file(tmp_path: Path) -> Path:
    """فایل فرمول‌های خالی — برای FormulaManager."""
    f = tmp_path / "my_formulas.json"
    f.write_text("{}", encoding="utf-8")
    return f


# ═══════════════════════════════════════════════════════════════════════════
# ۲) DB — SQLite واقعی در tmp_path
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
async def sqlite_db(tmp_db_path: Path):
    """
    SQLiteDatabase آماده با schema.

    - init شده
    - در پایان بسته می‌شه
    - هر تست DB تازه داره
    """
    from core.database.sqlite import SQLiteDatabase

    db = SQLiteDatabase(str(tmp_db_path))
    await db.init()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
async def temp_db(tmp_path: Path):
    """alias قدیمی برای sqlite_db (backward-compat)."""
    from core.database.sqlite import SQLiteDatabase

    db = SQLiteDatabase(str(tmp_path / "temp.db"))
    await db.init()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
async def db_path(temp_db) -> Path:
    """مسیر DB اصلی — از temp_db."""
    return Path(temp_db.db_path)


# ═══════════════════════════════════════════════════════════════════════════
# ۳) پیام‌های فیک
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def fake_message() -> dict[str, Any]:
    """
    یک پیام استاندارد فیک.

    شامل تمام ۱۵ فیلد اجباری + کلمه کلیدی «یاراترا».
    """
    from datetime import datetime

    return {
        "id": "test_msg_001",
        "platform": "test",
        "text": "یاراترا موجوده؟ قیمت چنده",
        "chat_id": "chat_1",
        "chat_name": "گروه کشاورزی",
        "chat_type": "group",
        "chat_username": None,
        "chat_link": None,
        "user_id": "user_1",
        "sender_name": "کشاورز",
        "timestamp": datetime(2026, 10, 25, 12, 0, 0),
        "media_type": "text",
        "file_id": None,
        "local_file_path": None,
        "reply_to": None,
    }


@pytest.fixture
def fake_message_factory():
    """
    سازنده پیام فیک با override.

    مثال:
        msg = fake_message_factory(text="متن خاص", chat_name="گروه X")
    """
    from datetime import datetime

    def _make(**overrides: Any) -> dict[str, Any]:
        base = {
            "id": "test_msg_001",
            "platform": "test",
            "text": "متن تست",
            "chat_id": "chat_1",
            "chat_name": "گروه تست",
            "chat_type": "group",
            "chat_username": None,
            "chat_link": None,
            "user_id": "user_1",
            "sender_name": "تستر",
            "timestamp": datetime(2026, 10, 25, 12, 0, 0),
            "media_type": "text",
            "file_id": None,
            "local_file_path": None,
            "reply_to": None,
        }
        base.update(overrides)
        return base

    return _make


# ═══════════════════════════════════════════════════════════════════════════
# ۴) Handlerها
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
async def storage_handler(sqlite_db):
    """
    StorageHandler آماده برای تست.

    - DB موقت
    - scorer=None (اختیاری)
    - keywords_only=False
    - allowed_tiers=["T1","T2","T3"]
    """
    from handlers.storage_handler import StorageHandler

    return StorageHandler(
        db=sqlite_db,
        scorer=None,
        enabled=True,
        keywords_only=False,
        require_t1=False,
        allowed_tiers=["T1", "T2", "T3"],
    )


@pytest.fixture
async def storage_handler_keywords_only(sqlite_db):
    """StorageHandler با keywords_only=True."""
    from handlers.storage_handler import StorageHandler

    return StorageHandler(
        db=sqlite_db,
        scorer=None,
        enabled=True,
        keywords_only=True,
        require_t1=False,
        allowed_tiers=["T1", "T2", "T3"],
    )


# ═══════════════════════════════════════════════════════════════════════════
# ۵) Mockها — برای تست‌های بدون شبکه
# ═══════════════════════════════════════════════════════════════════════════

class MockLocator:
    """Mock Playwright locator."""

    def __init__(self, count: int = 1, texts: list[str] | None = None):
        self._count = count
        self._texts = texts or ["mock text"]

    async def count(self) -> int:
        return self._count

    async def inner_text(self, timeout: int = 0) -> str:
        return self._texts[0] if self._texts else ""

    async def get_attribute(self, name: str, timeout: int = 0) -> str | None:
        return f"attr-{name}"

    async def click(self, *a, **kw) -> None:
        pass

    async def fill(self, value: str, *a, **kw) -> None:
        pass


class MockPage:
    """Mock Playwright page."""

    def __init__(self, url: str = "https://example.com"):
        self.url = url
        self._locators: dict[str, MockLocator] = {}

    def set_locator(self, selector: str, locator: MockLocator) -> None:
        self._locators[selector] = locator

    def locator(self, selector: str) -> MockLocator:
        return self._locators.get(selector, MockLocator(0, []))

    async def goto(self, url: str, **kw) -> None:
        self.url = url

    async def wait_for_timeout(self, ms: int) -> None:
        pass

    async def close(self) -> None:
        pass


class MockAI:
    """Mock AIAssistant — بدون شبکه."""

    def __init__(self, available: bool = True):
        self._available = available
        self._calls: list[str] = []

    async def available(self) -> bool:
        return self._available

    async def chat(self, text: str, system: str = "") -> str:
        self._calls.append(text)
        return f"پاسخ mock: {text[:20]}"

    async def chat_with_status(self, text: str, system: str = ""):
        self._calls.append(text)
        return (f"پاسخ mock: {text[:20]}", True)

    async def parse_intent(self, text: str) -> dict:
        self._calls.append(text)
        return {"action": "status", "args": {}}

    def _provider_order(self) -> list[str]:
        return ["mock"]

    def get_stats(self) -> dict:
        return {"enabled": True, "priority": "mock", "calls": len(self._calls)}


class MockDB:
    """Mock Database — در حافظه."""

    def __init__(self):
        self._messages: list[dict] = []
        self._leads: list[dict] = []

    async def init(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def save_message(self, msg: dict) -> int:
        self._messages.append(msg)
        return len(self._messages)

    async def get_total_count(self) -> dict:
        return {"messages": len(self._messages), "leads": len(self._leads)}


@pytest.fixture
def mock_page() -> MockPage:
    """MockPage استاندارد."""
    return MockPage()


@pytest.fixture
def mock_ai() -> MockAI:
    """MockAI (available=True)."""
    return MockAI(available=True)


@pytest.fixture
def mock_ai_offline() -> MockAI:
    """MockAI (available=False)."""
    return MockAI(available=False)


@pytest.fixture
def mock_db() -> MockDB:
    """MockDB در حافظه."""
    return MockDB()


# ═══════════════════════════════════════════════════════════════════════════
# ۶) Singleton reset — برای جلوگیری از نشت state بین تست‌ها
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def reset_all_singletons():
    """
    Reset همه singletonها قبل از تست.

    استفاده:
        def test_x(reset_all_singletons):
            ...
    """
    from core.adapters.access_manager import reset_access_manager
    from core.adapters.formula_manager import reset_formula_manager
    from core.adapters.translator import reset_translator
    from core.adapters.browser_lock import reset_browser_lock
    from core.services.ai_assistant import reset_ai_assistant
    from core.safe_brain import reset_safe_brain
    from core.brain.brain_memory import reset_brain_memory

    reset_access_manager()
    reset_formula_manager()
    reset_translator()
    reset_browser_lock()
    reset_ai_assistant()
    reset_safe_brain()
    reset_brain_memory()
    yield


# ═══════════════════════════════════════════════════════════════════════════
# ۷) Pipeline (برای تست‌های integration)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
async def pipeline():
    """
    TestPipeline (از tests/pipeline_helpers.py) — end-to-end.

    - DB موقت
    - StorageHandler
    - EventBus

    استفاده:
        async def test_x(pipeline):
            await pipeline.feed([msg])
            count = await pipeline.count_messages()
    """
    from tests.pipeline_helpers import TestPipeline

    p = TestPipeline(
        keywords_only=False,
        require_t1=False,
        allowed_tiers=("T1", "T2", "T3"),
    )
    await p.start()
    try:
        yield p
    finally:
        await p.stop()