# tests/test_smoke.py
"""
🔥 Smoke test — تست دود.

این فایل ثابت می‌کند:
  - pytest کار می‌کند
  - fixtureهای conftest.py کار می‌کنند
  - assert واقعاً fail می‌شود
  - پروژه import می‌شود

اجرا:
    pytest tests/test_smoke.py -v
    pytest tests/test_smoke.py -v -m smoke
"""
from __future__ import annotations

import pytest


# ═══════════════════════════════════════════════════════════
# تست ۱: pytest کار می‌کند
# ═══════════════════════════════════════════════════════════
def test_pytest_works():
    """ساده‌ترین تست ممکن — اگر این fail شد، pytest خراب است."""
    assert 1 + 1 == 2
    assert "a" + "b" == "ab"
    assert [1, 2] == [1, 2]


# ═══════════════════════════════════════════════════════════
# تست ۲: fixture پایه (project_root) کار می‌کند
# ═══════════════════════════════════════════════════════════
def test_project_root_fixture(project_root):
    """fixture project_root از conftest.py."""
    assert project_root is not None
    assert project_root.exists()
    assert (project_root / "config.py").exists()
    assert (project_root / "core").is_dir()


# ═══════════════════════════════════════════════════════════
# تست ۳: DB اصلی وجود دارد
# ═══════════════════════════════════════════════════════════
def test_db_exists(db_path):
    """DB اصلی باید موجود باشد."""
    assert db_path.exists(), f"DB پیدا نشد: {db_path}"
    assert db_path.stat().st_size > 0, "DB خالی است"
    assert db_path.suffix == ".db"


# ═══════════════════════════════════════════════════════════
# تست ۴: fixture پیام فیک
# ═══════════════════════════════════════════════════════════
def test_fake_message_fixture(fake_message):
    """fixture fake_message از conftest.py."""
    assert fake_message["id"] == "test_msg_001"
    assert fake_message["platform"] == "test"
    assert "کود مایع" in fake_message["text"]
    assert fake_message["timestamp"] is not None


# ═══════════════════════════════════════════════════════════
# تست ۵: pytest.raises کار می‌کند (assert واقعی)
# ═══════════════════════════════════════════════════════════
def test_pytest_raises():
    """pytest.raises باید exception را catch کند."""
    with pytest.raises(ValueError):
        raise ValueError("این باید catch شود")

    with pytest.raises(ZeroDivisionError):
        _ = 1 / 0


# ═══════════════════════════════════════════════════════════
# تست ۶: import پروژه کار می‌کند
# ═══════════════════════════════════════════════════════════
def test_core_imports():
    """import ماژول‌های اصلی پروژه."""
    from core.kernel.event_bus import EventBus
    from core.kernel.models import Message
    from core.contracts.base_collector import BaseCollector

    assert EventBus is not None
    assert Message is not None
    assert BaseCollector is not None


# ═══════════════════════════════════════════════════════════
# تست ۷: pytest.mark کار می‌کند
# ═══════════════════════════════════════════════════════════
@pytest.mark.smoke
def test_smoke_marker():
    """marker سفارشی smoke کار می‌کند."""
    assert True


# ═══════════════════════════════════════════════════════════
# تست ۸: parametrize کار می‌کند
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize("a,b,expected", [
    (1, 1, 2),
    (2, 3, 5),
    (-1, 1, 0),
    (0, 0, 0),
])
def test_parametrize(a, b, expected):
    """parametrize ۴ حالت را جداگانه تست می‌کند."""
    assert a + b == expected


# ═══════════════════════════════════════════════════════════
# تست ۹: async test (asyncio_mode = auto)
# ═══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_async_works():
    """تست async بدون decorate کردن مخصوص، کار می‌کند."""
    import asyncio

    async def add(a, b):
        await asyncio.sleep(0)
        return a + b

    result = await add(2, 3)
    assert result == 5


# ═══════════════════════════════════════════════════════════
# تست ۱۰: fixture pipeline کار می‌کند (نیاز به async)
# ═══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pipeline_fixture(pipeline):
    """fixture pipeline از conftest.py — TestPipeline آماده."""
    assert pipeline is not None
    assert pipeline._db_path is not None
    assert pipeline._storage is not None
    assert pipeline._db is not None

    # تست ذخیره‌سازی یک پیام
    count_before = await pipeline.count_messages()
    assert count_before == 0, "DB تست باید خالی باشد"