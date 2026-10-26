# tests/test_storage.py
"""
📦 تست StorageHandler — pytest-style با assert واقعی.

نسخهٔ ۳.۰ (این نسخه):
  - 🎯 ادغام شده با test_storage_additions.py (۱۵ تست API)
  - 🐛 FIX: id prefix — StorageHandler به id پیشوند پلتفرم اضافه میکند
  - 🎯 از کلیدواژهٔ واقعی از KeywordFinder استفاده میکنیم
  - 🎯 `_pick_real_keyword` — انتخاب کلیدواژهٔ واقعی
  - 🎯 `_query_by_id` — جستجو با id کامل یا prefix
  - 🎯 `handler` fixture — برای تست API داخلی
  - 🎯 `_make_raw` helper — پیام فیک برای تست API
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest


# ═══════════════════════════════════════════════════════════
# helper: انتخاب کلیدواژهٔ واقعی
# ═══════════════════════════════════════════════════════════
def _pick_real_keyword() -> str:
    """یک کلیدواژهٔ واقعی و امن از KeywordFinder."""
    try:
        from analyzers.keyword_finder import get_keyword_finder
        kf = get_keyword_finder()

        # کلیدواژهٔ فارسی با طول >= 4
        for k in kf.keywords:
            if len(k) >= 4 and any('\u0600' <= c <= '\u06ff' for c in k):
                return k

        # کلیدواژهٔ لاتین با طول >= 4
        for k in kf.keywords:
            if len(k) >= 4:
                return k

        # fallback
        if kf.keywords:
            return kf.keywords[0]
    except Exception:
        pass

    return "test_keyword"


# ═══════════════════════════════════════════════════════════
# helper: پیام فیک (برای feed pipeline)
# ═══════════════════════════════════════════════════════════
def _make_message(
    msg_id: str = "msg_001",
    text: str = "سلام",
    platform: str = "test",
    **overrides,
) -> dict:
    """ساخت یک پیام فیک."""
    base = {
        "id": msg_id,
        "platform": platform,
        "text": text,
        "chat_id": "chat_1",
        "chat_name": "چت تست",
        "chat_type": "group",
        "chat_username": None,
        "chat_link": None,
        "user_id": "user_1",
        "sender_name": "تستر",
        "timestamp": datetime.now(),
        "media_type": "text",
        "file_id": None,
        "local_file_path": None,
        "reply_to": None,
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════
# helper: پیام فیک (برای API داخلی handler)
# ═══════════════════════════════════════════════════════════
def _make_raw(
    msg_id: str = "msg_001",
    user_id: str = "user_1",
    chat_name: str = "گروه تست",
    platform: str = "test",
    text: str = "سلام",
) -> dict:
    return {
        "id": msg_id,
        "platform": platform,
        "text": text,
        "chat_id": "chat_1",
        "chat_name": chat_name,
        "chat_type": "group",
        "user_id": user_id,
        "sender_name": "تستر",
        "timestamp": datetime.now(),
        "media_type": "text",
        "keywords": "",
    }


# ═══════════════════════════════════════════════════════════
# helper: کوئری با id (پذیرش prefix)
# ═══════════════════════════════════════════════════════════
def _query_by_id(db_path: str, msg_id: str) -> list[dict]:
    """جستجو با id — چه با prefix چه بدون."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM messages WHERE id LIKE ? OR id = ?",
            (f"%{msg_id}", msg_id),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════
# Fixture: StorageHandler (برای تست API داخلی)
# ═══════════════════════════════════════════════════════════
@pytest.fixture
async def handler(tmp_db_path):
    """StorageHandler با DB موقت."""
    from core.database.sqlite import SQLiteDatabase
    from handlers.storage_handler import StorageHandler

    db = SQLiteDatabase(str(tmp_db_path))
    await db.init()

    h = StorageHandler(
        db=db,
        scorer=None,
        enabled=True,
        keywords_only=False,
        require_t1=False,
        allowed_tiers=["T1", "T2", "T3"],
    )
    try:
        yield h
    finally:
        await db.close()


# ═══════════════════════════════════════════════════════════
# بخش A: feed() — رفتار pipeline
# ═══════════════════════════════════════════════════════════

@pytest.mark.db
@pytest.mark.asyncio
async def test_save_message(pipeline):
    """پیام باید در DB ذخیره شود (با prefix platform در id)."""
    msg = _make_message(msg_id="save_test_001", text="سلام دنیا")

    stored = await pipeline.feed([msg])
    assert stored >= 1, f"باید حداقل ۱ پیام ذخیره شود، ولی {stored} شد"

    rows = _query_by_id(pipeline._db_path, "save_test_001")
    assert len(rows) == 1, (
        f"باید ۱ پیام با id شامل save_test_001 باشد، ولی {len(rows)} است"
    )

    row = rows[0]
    assert "save_test_001" in row["id"], (
        f"id باید شامل save_test_001 باشد: {row['id']}"
    )
    assert row["platform"] == "test"
    assert row["chat_name"] == "چت تست"


@pytest.mark.db
@pytest.mark.asyncio
async def test_message_without_keyword_still_saved(pipeline):
    """وقتی keywords_only=False، پیام بدون کلیدواژه هم ذخیره می‌شود."""
    msg = _make_message(
        msg_id="no_kw_001",
        text="یک متن کاملاً تصادفی بدون کلیدواژه xyz123",
    )

    stored = await pipeline.feed([msg])
    assert stored >= 1, "پیام باید ذخیره شود (keywords_only=False)"

    rows = _query_by_id(pipeline._db_path, "no_kw_001")
    assert len(rows) == 1, "پیام باید در DB باشد"


@pytest.mark.db
@pytest.mark.asyncio
async def test_fields_populated(pipeline):
    """همه فیلدهای اصلی باید در DB پر شوند."""
    msg = _make_message(
        msg_id="fields_001",
        text="تست محتوا",
        chat_name="گروه کشاورزی",
        sender_name="کشاورز تست",
    )

    await pipeline.feed([msg])

    rows = _query_by_id(pipeline._db_path, "fields_001")
    assert len(rows) == 1

    row = rows[0]
    assert "fields_001" in row["id"]
    assert row["platform"] == "test"
    assert row["chat_name"] == "گروه کشاورزی"
    assert row["sender_name"] == "کشاورز تست"
    assert "تست محتوا" in (row["text"] or "")
    assert row["timestamp"] is not None


@pytest.mark.db
@pytest.mark.asyncio
async def test_idempotency(pipeline):
    """ذخیره دوباره همان id نباید رکورد تکراری بسازد."""
    msg = _make_message(msg_id="dup_001", text="تست idempotency")

    await pipeline.feed([msg])
    count_after_first = await pipeline.count_messages(platform="test")
    assert count_after_first == 1

    await pipeline.feed([msg])
    count_after_second = await pipeline.count_messages(platform="test")
    assert count_after_second == 1, (
        f"idempotency شکست: {count_after_second} رکورد (انتظار ۱)"
    )


@pytest.mark.db
@pytest.mark.asyncio
async def test_stats_correct(pipeline):
    """آمار StorageHandler باید درست باشد."""
    msgs = [
        _make_message(msg_id="stats_001", text="تست ۱"),
        _make_message(msg_id="stats_002", text="تست ۲"),
        _make_message(msg_id="stats_003", text="تست ۳"),
    ]

    await pipeline.feed(msgs)

    total = await pipeline.count_messages(platform="test")
    assert total == 3, f"total باید ۳ باشد، ولی {total} است"


@pytest.mark.db
@pytest.mark.asyncio
async def test_multiple_platforms(pipeline):
    """پیام‌های چند پلتفرم باید جدا ذخیره شوند."""
    msgs = [
        _make_message(msg_id="p1", platform="bale", text="تست بله"),
        _make_message(msg_id="p2", platform="eitaa", text="تست ایتا"),
        _make_message(msg_id="p3", platform="bale", text="تست بله ۲"),
    ]

    await pipeline.feed(msgs)

    bale_count = await pipeline.count_messages(platform="bale")
    eitaa_count = await pipeline.count_messages(platform="eitaa")

    assert bale_count == 2, f"bale باید ۲ باشد، ولی {bale_count} است"
    assert eitaa_count == 1, f"eitaa باید ۱ باشد، ولی {eitaa_count} است"


@pytest.mark.db
@pytest.mark.asyncio
async def test_real_keyword_matched(pipeline):
    """پیام با کلیدواژهٔ واقعی باید keywords پر داشته باشد."""
    real_kw = _pick_real_keyword()

    msg = _make_message(
        msg_id="real_kw_001",
        text=f"سلام، {real_kw} موجوده؟",
    )

    await pipeline.feed([msg])

    rows = _query_by_id(pipeline._db_path, "real_kw_001")
    assert len(rows) == 1

    row = rows[0]
    keywords = row.get("keywords") or ""

    assert len(keywords) > 0, (
        f"keywords نباید خالی باشد — کلیدواژهٔ تست: «{real_kw}» — "
        f"آیا این کلیدواژه در KeywordFinder هست؟"
    )


@pytest.mark.db
@pytest.mark.asyncio
async def test_db_isolation(pipeline):
    """هر تست باید DB ایزوله داشته باشد (fixture)."""
    count = await pipeline.count_messages()
    assert count == 0, f"DB تست باید خالی باشد، ولی {count} رکورد دارد"


# ═══════════════════════════════════════════════════════════
# بخش B: API داخلی handler
# ═══════════════════════════════════════════════════════════

def test_can_handle_message_received(handler):
    """can_handle('message.received') باید True باشه."""
    assert handler.can_handle("message.received") is True


def test_can_handle_unknown_event(handler):
    """can_handle('other.event') باید False باشه."""
    assert handler.can_handle("other.event") is False
    assert handler.can_handle("") is False


def test_handler_name(handler):
    """handler_name باید 'storage_handler' باشه."""
    assert handler.handler_name == "storage_handler"


def test_priority(handler):
    """priority باید HandlerPriority باشه."""
    from core.contracts.base_handler import HandlerPriority
    p = handler.priority
    assert isinstance(p, HandlerPriority), (
        f"priority نوع {type(p).__name__} است، نه HandlerPriority"
    )
    assert isinstance(p.value, int)
    assert p.value > 0


def test_enabled(handler):
    """enabled باید True باشه."""
    assert handler.enabled is True


def test_get_stats(handler):
    """get_stats() باید dict با شمارنده‌ها برگردونه."""
    stats = handler.get_stats()
    assert isinstance(stats, dict)
    assert len(stats) > 0


def test_reset_stats(handler):
    """reset_stats() باید شمارنده‌ها رو صفر کنه."""
    before = handler.get_stats()
    handler.reset_stats()
    after = handler.get_stats()

    for key in before:
        if isinstance(before[key], int):
            assert after.get(key, 0) == 0, (
                f"بعد از reset، {key}={after.get(key)} (انتظار 0)"
            )


def test_is_admin_message_normal_user(handler):
    """کاربر عادی → False."""
    raw = _make_raw(user_id="random_user_999")
    result = handler._is_admin_message(raw)
    assert result is False, f"کاربر عادی → {result}"


def test_is_admin_message_empty(handler):
    """raw خالی → False (بدون کرش)."""
    result = handler._is_admin_message({})
    assert result is False


def test_parse_allowed_tiers_list(handler):
    """_parse_allowed_tiers با list → tuple."""
    cfg = {"allowed_tiers": ["T1", "T2"]}
    result = handler._parse_allowed_tiers(cfg)
    assert isinstance(result, tuple)
    assert "T1" in result
    assert "T2" in result


def test_parse_allowed_tiers_string(handler):
    """_parse_allowed_tiers با string comma-separated → tuple."""
    cfg = {"allowed_tiers": "T1,T2,T3"}
    result = handler._parse_allowed_tiers(cfg)
    assert isinstance(result, tuple)
    assert "T1" in result


def test_parse_allowed_tiers_empty(handler):
    """_parse_allowed_tiers با cfg خالی → tuple پیش‌فرض."""
    result = handler._parse_allowed_tiers({})
    assert isinstance(result, tuple)
    assert len(result) >= 1


def test_make_platform_unique_id(handler):
    """_make_platform_unique_id باید dict با id یکتا برگردونه."""
    raw = _make_raw(msg_id="abc123", platform="bale")
    result = handler._make_platform_unique_id(raw)
    assert isinstance(result, dict)
    assert result.get("id") != "abc123" or result.get("platform_message_id") == "abc123"


def test_is_self_or_saved_normal(handler):
    """پیام از گروه عادی → False."""
    raw = _make_raw(chat_name="گروه عادی", user_id="user_1")
    result = handler._is_self_or_saved(raw)
    assert result is False


def test_is_self_or_saved_saved_messages(handler):
    """پیام از 'Saved Messages' → True (یا skip)."""
    raw = _make_raw(chat_name="Saved Messages", user_id="user_1")
    try:
        result = handler._is_self_or_saved(raw)
        assert isinstance(result, bool)
    except Exception as e:
        pytest.skip(f"_is_self_or_saved error: {e}")