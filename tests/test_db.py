# tests/test_db.py
"""
🗄️ تست دیتابیس — pytest-style.

دو دسته تست:
  A) DB اصلی (read-only) — smoke
  B) SQLiteDatabase با DB موقت — کامل

اجرا:
    pytest tests/test_db.py -v
    pytest tests/test_db.py -v -m db
    pytest tests/test_db.py -v -m smoke
"""
from __future__ import annotations
import os 
import sqlite3
from datetime import datetime, timedelta

import pytest


# ═══════════════════════════════════════════════════════════
# دسته A: DB اصلی — smoke (read-only)
# ═══════════════════════════════════════════════════════════
@pytest.mark.db
@pytest.mark.smoke
def test_main_db_exists(db_path):
    """DB اصلی باید موجود و غیرخالی باشد."""
    assert db_path.exists(), f"DB پیدا نشد: {db_path}"
    assert db_path.stat().st_size > 0, "DB خالی است"
    assert db_path.suffix == ".db"


@pytest.mark.db
@pytest.mark.smoke
def test_main_db_tables(db_path):
    """جدول‌های اصلی باید موجود باشند."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()

    expected = {"messages", "leads", "migrations"}
    missing = expected - tables
    assert not missing, f"جدول‌های گم‌شده: {missing}"


@pytest.mark.db
@pytest.mark.smoke
def test_main_db_integrity(db_path):
    """integrity_check باید OK باشد."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check")
        result = cur.fetchone()[0]
    finally:
        conn.close()

    assert result == "ok", f"integrity_check: {result}"


@pytest.mark.db
@pytest.mark.smoke
def test_main_db_schema_version(db_path):
    """schema version باید >= 6 باشد."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA user_version")
        version = cur.fetchone()[0]
    finally:
        conn.close()

    assert version >= 6, f"schema version قدیمی: v{version}"


@pytest.mark.db
@pytest.mark.smoke
def test_main_db_messages_columns(db_path):
    """جدول messages باید ستون‌های کلیدی را داشته باشد."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(messages)")
        cols = {row[1] for row in cur.fetchall()}
    finally:
        conn.close()

    required = {
        "id", "platform", "text", "chat_name",
        "timestamp", "keywords",
    }
    missing = required - cols
    assert not missing, f"ستون‌های گم‌شده: {missing}"


# ═══════════════════════════════════════════════════════════
# دسته B: SQLiteDatabase با DB موقت
# ═══════════════════════════════════════════════════════════
@pytest.mark.db
@pytest.mark.asyncio
async def test_sqlite_init_and_save(temp_db_path):
    """SQLiteDatabase ساخته می‌شود و پیام ذخیره می‌کند."""
    from core.database.sqlite import SQLiteDatabase

    db = SQLiteDatabase(str(temp_db_path))
    await db.init()

    try:
        msg = {
            "id": "test_001",
            "platform": "test",
            "text": "سلام",
            "chat_name": "تست",
            "timestamp": datetime.now(),
        }

        rowid = await db.save_message(msg)
        assert rowid >= 1

        # چک مستقیم
        conn = sqlite3.connect(str(temp_db_path))
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, platform FROM messages")
            rows = cur.fetchall()
        finally:
            conn.close()

        assert len(rows) == 1
        assert rows[0][0] == "test_001"
        assert rows[0][1] == "test"

    finally:
        await db.close()


@pytest.mark.db
@pytest.mark.asyncio
async def test_sqlite_idempotency(temp_db_path):
    """ذخیره دوباره همان id نباید تکراری بسازد."""
    from core.database.sqlite import SQLiteDatabase

    db = SQLiteDatabase(str(temp_db_path))
    await db.init()

    try:
        msg = {
            "id": "dup_001",
            "platform": "test",
            "text": "سلام",
            "chat_name": "تست",
            "timestamp": datetime.now(),
        }

        await db.save_message(msg)
        await db.save_message(msg)  # دوباره

        conn = sqlite3.connect(str(temp_db_path))
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM messages")
            count = cur.fetchone()[0]
        finally:
            conn.close()

        assert count == 1, f"idempotency شکست: {count} رکورد"

    finally:
        await db.close()


@pytest.mark.db
@pytest.mark.asyncio
async def test_sqlite_datetime_roundtrip(temp_db_path):
    """datetime → DB → datetime بدون warning."""
    from core.database.sqlite import SQLiteDatabase

    db = SQLiteDatabase(str(temp_db_path))
    await db.init()

    try:
        original = datetime(2026, 10, 25, 12, 30, 45)
        msg = {
            "id": "dt_001",
            "platform": "test",
            "text": "تست datetime",
            "chat_name": "تست",
            "timestamp": original,
        }

        await db.save_message(msg)

        # چک: datetime به ISO string ذخیره شده
        conn = sqlite3.connect(str(temp_db_path))
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT timestamp FROM messages WHERE id='dt_001'"
            )
            stored = cur.fetchone()[0]
        finally:
            conn.close()

        # باید ISO string باشد
        assert stored == "2026-10-25T12:30:45", (
            f"timestamp باید ISO باشد، ولی {stored!r}"
        )

    finally:
        await db.close()


@pytest.mark.db
@pytest.mark.asyncio
async def test_sqlite_count(temp_db_path):
    """get_total_count درست کار می‌کند."""
    from core.database.sqlite import SQLiteDatabase

    db = SQLiteDatabase(str(temp_db_path))
    await db.init()

    try:
        # ذخیره ۳ پیام
        for i in range(3):
            await db.save_message({
                "id": f"count_{i}",
                "platform": "test",
                "text": f"پیام {i}",
                "chat_name": "تست",
                "timestamp": datetime.now(),
                "keywords": "کود" if i < 2 else "",
            })

        counts = await db.get_total_count()
        assert counts["messages"] == 3
        assert counts["leads"] == 2  # ۲ پیام کلیدواژه‌دار

    finally:
        await db.close()


@pytest.mark.db
@pytest.mark.asyncio
async def test_sqlite_migration(temp_db_path):
    """init() باید schema را migrate کند."""
    from core.database.sqlite import SQLiteDatabase

    db = SQLiteDatabase(str(temp_db_path))
    await db.init()

    try:
        conn = sqlite3.connect(str(temp_db_path))
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA user_version")
            version = cur.fetchone()[0]
        finally:
            conn.close()

        assert version >= 6, f"migration اجرا نشد: v{version}"

    finally:
        await db.close()


@pytest.mark.db
@pytest.mark.asyncio
async def test_sqlite_search(temp_db_path):
    """search() متن را در DB پیدا می‌کند."""
    from core.database.sqlite import SQLiteDatabase

    db = SQLiteDatabase(str(temp_db_path))
    await db.init()

    try:
        await db.save_message({
            "id": "search_001",
            "platform": "test",
            "text": "کود مایع یاراترا",
            "chat_name": "تست",
            "timestamp": datetime.now(),
        })

        results = await db.search("یاراترا")
        assert len(results) == 1
        assert "یاراترا" in results[0]["text"]

    finally:
        await db.close()


@pytest.mark.db
@pytest.mark.asyncio
async def test_sqlite_delete_old(temp_db_path):
    """delete_old_messages پیام‌های قدیمی را حذف می‌کند."""
    from core.database.sqlite import SQLiteDatabase

    db = SQLiteDatabase(str(temp_db_path))
    await db.init()

    try:
        now = datetime.now()
        old = now - timedelta(days=60)

        # ۱ پیام جدید + ۱ پیام قدیمی
        await db.save_message({
            "id": "new_001",
            "platform": "test",
            "text": "جدید",
            "chat_name": "تست",
            "timestamp": now,
        })
        await db.save_message({
            "id": "old_001",
            "platform": "test",
            "text": "قدیمی",
            "chat_name": "تست",
            "timestamp": old,
        })

        # حذف پیام‌های قدیمی‌تر از ۳۰ روز
        await db.delete_old_messages(days=30)

        counts = await db.get_total_count()
        assert counts["messages"] == 1, (
            f"باید ۱ پیام باشد، ولی {counts['messages']} است"
        )

    finally:
        await db.close()
        
        
# ═══════════════════════════════════════════════════════════
# Fixture: DB موقت
# ═══════════════════════════════════════════════════════════
@pytest.fixture
async def db(temp_db_path):
    """SQLiteDatabase با DB موقت."""
    from core.database.sqlite import SQLiteDatabase

    database = SQLiteDatabase(str(temp_db_path))
    await database.init()
    try:
        yield database
    finally:
        await database.close()


# ═══════════════════════════════════════════════════════════
# helper: ذخیره پیام با lead
# ═══════════════════════════════════════════════════════════
async def _save_with_lead(db, msg_id: str, text: str, keywords: str = "",
                          score: int = 50, tier: str = "T1"):
    """ذخیره پیام با lead."""
    msg = {
        "id": msg_id,
        "platform": "test",
        "text": text,
        "chat_name": "تست",
        "timestamp": datetime.now(),
        "keywords": keywords,
        "priority_tier": tier,
    }
    lead = {
        "id": f"lead_{msg_id}",
        "message_id": msg_id,
        "platform": "test",
        "text": text,
        "chat_name": "تست",
        "timestamp": datetime.now(),
        "keywords": keywords,
        "opportunity_score": score,
        "priority_tier": tier,
    }
    await db.save_message_and_lead(msg, lead)


# ═══════════════════════════════════════════════════════════
# تست ۱: backup()
# ═══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_backup(db, tmp_path):
    """backup() باید فایل پشتیبان بسازه."""
    # یه پیام ذخیره کن
    await _save_with_lead(db, "bk_001", "تست", "کود", 50)

    backup_path = str(tmp_path / "backup.db")
    result = await db.backup(backup_path)

    assert result is True, "backup() باید True برگردونه"
    assert os.path.isfile(backup_path), f"فایل backup ساخته نشد: {backup_path}"
    assert os.path.getsize(backup_path) > 0, "فایل backup خالیه"


# ═══════════════════════════════════════════════════════════
# تست ۲: get_high_score_leads()
# ═══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_get_high_score_leads(db):
    """get_high_score_leads() باید فقط leadهای با score بالا رو برگردونه."""
    # ۳ lead با score مختلف
    await _save_with_lead(db, "hs_001", "کود مایع", "کود", 80)
    await _save_with_lead(db, "hs_002", "سم", "سم", 50)
    await _save_with_lead(db, "hs_003", "بذر", "بذر", 20)

    # فقط score >= 60
    leads = await db.get_high_score_leads(min_score=60)

    assert len(leads) == 1, f"باید ۱ lead باشه، ولی {len(leads)} است"
    assert leads[0]["opportunity_score"] >= 60


# ═══════════════════════════════════════════════════════════
# تست ۳: get_keyword_stats()
# ═══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_get_keyword_stats(db):
    """get_keyword_stats() باید آمار کلیدواژه‌ها رو برگردونه."""
    # ۳ پیام با keywordهای مختلف
    await _save_with_lead(db, "ks_001", "کود مایع", "کود,مایع", 50)
    await _save_with_lead(db, "ks_002", "کود خشک", "کود,خشک", 50)
    await _save_with_lead(db, "ks_003", "سم", "سم", 50)

    stats = await db.get_keyword_stats(limit=10)

    assert isinstance(stats, list)
    assert len(stats) > 0, "آمار خالی برگشت"

    # "کود" باید ۲ بار باشه (بیشترین)
    kw_dict = dict(stats)
    assert kw_dict.get("کود", 0) == 2, (
        f"«کود» باید ۲ بار باشه: {kw_dict}"
    )


# ═══════════════════════════════════════════════════════════
# تست ۴: get_group_stats()
# ═══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_get_group_stats(db):
    """get_group_stats() باید آمار گروه‌ها رو برگردونه."""
    # ۲ پیام از گروه A، ۱ از B
    for i, (msg_id, chat) in enumerate([
        ("gs_001", "گروه A"),
        ("gs_002", "گروه A"),
        ("gs_003", "گروه B"),
    ]):
        msg = {
            "id": msg_id,
            "platform": "test",
            "text": "کود",
            "chat_name": chat,
            "timestamp": datetime.now(),
            "keywords": "کود",
        }
        await db.save_message(msg)

    stats = await db.get_group_stats(limit=10)
    assert isinstance(stats, list)
    assert len(stats) > 0

    # گروه A باید ۲ بار باشه
    group_dict = dict(stats)
    assert group_dict.get("گروه A", 0) == 2, (
        f"گروه A باید ۲ باشه: {group_dict}"
    )


# ═══════════════════════════════════════════════════════════
# تست ۵: get_important_messages()
# ═══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_get_important_messages(db):
    """get_important_messages() باید فقط مهم‌ها رو برگردونه."""
    await _save_with_lead(db, "im_001", "مهم", "کود", 50)
    await _save_with_lead(db, "im_002", "غیرمهم", "سم", 50)

    # فقط im_001 رو important کن
    await db.mark_important("im_001", "یادداشت تست")

    important = await db.get_important_messages(limit=10)
    assert len(important) >= 1

    ids = [r["id"] for r in important]
    assert "im_001" in ids, f"im_001 در important نیست: {ids}"


# ═══════════════════════════════════════════════════════════
# تست ۶: mark_important()
# ═══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_mark_important(db):
    """mark_important() باید is_important و my_note رو ست کنه."""
    await _save_with_lead(db, "mi_001", "تست", "کود", 50)

    await db.mark_important("mi_001", "یادداشت من")

    # چک از DB
    import sqlite3
    conn = sqlite3.connect(db.db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT is_important, my_note FROM messages WHERE id=?",
            ("mi_001",),
        )
        row = cur.fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == 1, "is_important ست نشد"
    assert row[1] == "یادداشت من", f"my_note={row[1]}"


# ═══════════════════════════════════════════════════════════
# تست ۷: update_lead_score()
# ═══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_update_lead_score(db):
    """update_lead_score() باید score رو به‌روز کنه."""
    await _save_with_lead(db, "us_001", "تست", "کود", 50)

    await db.update_lead_score("lead_us_001", 99)

    import sqlite3
    conn = sqlite3.connect(db.db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT opportunity_score FROM leads WHERE id=?",
            ("lead_us_001",),
        )
        row = cur.fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == 99, f"score={row[0]} (انتظار 99)"


# ═══════════════════════════════════════════════════════════
# تست ۸: execute() — SELECT
# ═══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_execute_select(db):
    """execute() باید برای SELECT کار کنه — fetchall async است."""
    await _save_with_lead(db, "ex_001", "تست", "کود", 50)

    result = await db.execute("SELECT id FROM messages")
    rows = await result.fetchall()   # ← await لازمه

    assert len(rows) == 1, f"باید ۱ ردیف باشه، ولی {len(rows)} است"
    assert "ex_001" in str(rows[0]), f"ex_001 در ردیف نیست: {rows[0]}"
    

# ═══════════════════════════════════════════════════════════
# تست ۹: execute() — INSERT
# ═══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_execute_insert(db):
    """execute() باید برای INSERT کار کنه."""
    result = await db.execute(
        "INSERT INTO last_poll (platform, last_poll) VALUES (?, ?)",
        ("test", datetime.now().isoformat()),
    )
    assert result is not None

    # چک از DB
    import sqlite3
    conn = sqlite3.connect(db.db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM last_poll WHERE platform='test'"
        )
        count = cur.fetchone()[0]
    finally:
        conn.close()

    assert count == 1


# ═══════════════════════════════════════════════════════════
# تست ۱۰: get_leads_by_date_range()
# ═══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_get_leads_by_date_range(db):
    """get_leads_by_date_range() باید فقط بازهٔ داده‌شده رو برگردونه."""
    await _save_with_lead(db, "dr_001", "تست ۱", "کود", 50)

    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    # بازهٔ امروز
    leads = await db.get_leads_by_date_range(today, tomorrow)
    assert len(leads) >= 1, f"باید حداقل ۱ lead باشه، ولی {len(leads)} است"

    # بازهٔ گذشته (خالی)
    old_leads = await db.get_leads_by_date_range(
        "2020-01-01", "2020-01-02"
    )
    assert len(old_leads) == 0, f"باید ۰ باشه، ولی {len(old_leads)} است"        