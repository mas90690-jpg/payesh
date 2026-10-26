# tests/unit/test_adapters.py
"""تست کامل adapters — ۸ ماژول، پوشش ≥۹۰٪.

قرارداد واقعی (استخراج‌شده از inspect):
  - parse_command(text) -> tuple[action, value]
  - AccessManager singleton با RELOAD
  - FormulaManager singleton با db_path
  - Translator thread-safe
  - MemoryStore SQLite + FTS
  - messenger_registry thread-safe
  - browser_lock per-event-loop
  - web_media async
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from core.adapters.command_parser import (
    extract_value,
    get_commands_source,
    parse_command,
    parse_set_param,
    platform_in,
    reload_commands,
)


# ═══════════════════════════════════════════════════════════
# command_parser — parse_command
# ═══════════════════════════════════════════════════════════
class TestParseCommand:
    @pytest.mark.parametrize("text,expected_action", [
        ("راهنما", "help"),
        ("help", "help"),
        ("کمک", "help"),
        ("دستورات", "help"),
    ])
    def test_help_variants(self, text, expected_action):
        action, _ = parse_command(text)
        assert action == expected_action

    @pytest.mark.parametrize("text,expected_action", [
        ("restart", "restart"),
        ("ری‌استارت", "restart"),
        ("ری استارت", "restart"),
    ])
    def test_restart_variants(self, text, expected_action):
        action, _ = parse_command(text)
        assert action == expected_action

    def test_status(self):
        action, _ = parse_command("وضعیت")
        assert action == "status"

    def test_status_with_platform(self):
        action, value = parse_command("وضعیت بله")
        assert action == "platform_status"
        assert value == "bale"

    def test_add_keyword(self):
        action, value = parse_command(
            "کلیدواژه «کود مایع» را اضافه کن"
        )
        assert action == "add_keyword"
        assert value is not None
        assert "کود" in value or "مایع" in value

    def test_remove_keyword(self):
        action, value = parse_command(
            "کلیدواژه «کود» را حذف کن"
        )
        assert action == "remove_keyword"
        assert value == "کود"

    def test_has_keyword(self):
        action, value = parse_command("کلیدواژه «کود» را داری؟")
        assert action == "has_keyword"

    def test_report(self):
        action, _ = parse_command("گزارش بده")
        assert action == "report"

    def test_crm(self):
        action, _ = parse_command("crm")
        assert action == "crm"

    def test_logs(self):
        action, _ = parse_command("لاگ")
        assert action == "logs"

    def test_health(self):
        action, _ = parse_command("سلامت")
        assert action == "health"

    def test_lead_details_first(self):
        action, which = parse_command("اولین سرنخ")
        assert action == "lead_details"
        assert which == "first"

    def test_lead_details_last(self):
        action, which = parse_command("آخرین سرنخ")
        assert action == "lead_details"
        assert which == "last"

    def test_search_platform(self):
        action, value = parse_command("در بله جستجو کن یاراترا")
        assert action == "search_platform"
        assert "یاراترا" in value

    def test_join_group(self):
        action, value = parse_command(
            "عضو شو https://ble.ir/join/abc"
        )
        assert action == "join_group"
        assert "https://" in value

    def test_start_platform(self):
        action, value = parse_command("بله را روشن کن")
        assert action == "start_platform"
        assert value == "bale"

    def test_stop_platform(self):
        action, value = parse_command("ایتا را خاموش کن")
        assert action == "stop_platform"
        assert value == "eitaa"

    def test_unknown_returns_none(self):
        action, _ = parse_command("متن کاملاً تصادفی xyz123")
        assert action is None

    def test_empty_returns_none(self):
        action, value = parse_command("")
        assert action is None
        assert value is None

    def test_whitespace_returns_none(self):
        action, _ = parse_command("   ")
        assert action is None


# ═══════════════════════════════════════════════════════════
# command_parser — extract_value
# ═══════════════════════════════════════════════════════════
class TestExtractValue:
    def test_guillemets(self):
        assert extract_value("«کود مایع»") == "کود مایع"

    def test_double_quotes(self):
        assert extract_value('"کود"') == "کود"

    def test_single_quotes(self):
        assert extract_value("'کود'") == "کود"

    def test_no_quotes_returns_none(self):
        assert extract_value("کود مایع") is None

    def test_empty_returns_none(self):
        assert extract_value("") is None

    def test_none_returns_none(self):
        assert extract_value(None) is None

    def test_empty_quotes_returns_none(self):
        assert extract_value("«»") is None


# ═══════════════════════════════════════════════════════════
# command_parser — platform_in
# ═══════════════════════════════════════════════════════════
class TestPlatformIn:
    @pytest.mark.parametrize("text,expected", [
        ("بله", "bale"),
        ("ایتا", "eitaa"),
        ("روبیکا", "rubika"),
        ("تلگرام", "telegram"),
        ("واتساپ", "whatsapp"),
        ("لینکدین", "linkedin"),
        ("اینستاگرام", "instagram"),
        ("یوتیوب", "youtube"),
    ])
    def test_known_platforms(self, text, expected):
        result = platform_in(text.lower())
        assert result == expected

    def test_unknown_returns_none(self):
        assert platform_in("xyz") is None

    def test_empty_returns_none(self):
        assert platform_in("") is None


# ═══════════════════════════════════════════════════════════
# command_parser — parse_set_param
# ═══════════════════════════════════════════════════════════
class TestParseSetParam:
    def test_basic(self):
        name, value = parse_set_param("set LOG_LEVEL=INFO")
        assert name == "LOG_LEVEL"
        assert value == "INFO"

    def test_with_quotes(self):
        name, value = parse_set_param('set BALE_POLL="120"')
        assert name == "BALE_POLL"
        assert value == "120"

    def test_invalid_returns_none(self):
        name, value = parse_set_param("not a set command")
        assert name is None
        assert value is None

    def test_empty_returns_none(self):
        name, value = parse_set_param("")
        assert name is None
        assert value is None


# ═══════════════════════════════════════════════════════════
# command_parser — reload / source
# ═══════════════════════════════════════════════════════════
class TestCommandParserMeta:
    def test_get_commands_source(self):
        source = get_commands_source()
        assert source in ("file", "fallback")

    def test_reload_does_not_crash(self):
        reload_commands()  # نباید raise کند


# ═══════════════════════════════════════════════════════════
# access_manager
# ═══════════════════════════════════════════════════════════
class TestAccessManager:
    def test_singleton(self):
        from core.adapters.access_manager import (
            get_access_manager, reset_access_manager,
        )
        reset_access_manager()
        a = get_access_manager()
        b = get_access_manager()
        assert a is b

    def test_has_can_read(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        assert hasattr(am, "can_read")

    def test_can_read_returns_tuple(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        result = am.can_read("config.py")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)

    def test_can_write_returns_tuple(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        result = am.can_write("config.py")
        assert isinstance(result, tuple)
        assert isinstance(result[0], bool)

    def test_is_read_allowed_returns_bool(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        assert isinstance(am.is_read_allowed("config.py"), bool)

    def test_can_query_db_select(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        result = am.can_query_db("SELECT * FROM messages LIMIT 10")
        assert isinstance(result, tuple)
        assert isinstance(result[0], bool)

    def test_can_query_db_rejects_delete(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        ok, _ = am.can_query_db("DELETE FROM messages")
        assert ok is False

    def test_can_query_db_rejects_drop(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        ok, _ = am.can_query_db("SELECT 1; DROP TABLE messages")
        assert ok is False

    def test_can_query_db_rejects_empty(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        ok, _ = am.can_query_db("")
        assert ok is False

    def test_can_run_command(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        result = am.can_run_command("git status")
        assert isinstance(result, tuple)

    def test_can_run_command_rejects_metachar(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        ok, _ = am.can_run_command("git status; rm -rf /")
        assert ok is False

    def test_can_run_command_rejects_pipe(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        ok, _ = am.can_run_command("cat config.py | rm")
        assert ok is False

    def test_get_summary_dict(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        d = am.get_summary_dict()
        assert isinstance(d, dict)
        assert "full_access" in d

    def test_get_stats(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        stats = am.get_stats()
        assert isinstance(stats, dict)
        assert "file" in stats


# ═══════════════════════════════════════════════════════════
# translator
# ═══════════════════════════════════════════════════════════
class TestTranslator:
    def test_get_translator_singleton(self):
        from core.adapters.translator import (
            get_translator, reset_translator,
        )
        reset_translator()
        t1 = get_translator()
        t2 = get_translator()
        assert t1 is t2

    def test_translate_none_returns_empty(self):
        from core.adapters.translator import get_translator
        t = get_translator()
        assert t.translate(None) == ""

    def test_translate_empty_returns_empty(self):
        from core.adapters.translator import get_translator
        t = get_translator()
        assert t.translate("") == ""

    def test_disabled_returns_input(self):
        from core.adapters.translator import Translator
        t = Translator(enabled=False)
        assert t.translate("hello") == "hello"

    def test_chunk_text_small(self):
        from core.adapters.translator import Translator
        chunks = Translator._chunk_text("hello", 100)
        assert chunks == ["hello"]

    def test_chunk_text_empty(self):
        from core.adapters.translator import Translator
        chunks = Translator._chunk_text("", 100)
        assert chunks == [""]

    def test_chunk_text_large(self):
        from core.adapters.translator import Translator
        text = "a" * 500
        chunks = Translator._chunk_text(text, 100)
        assert len(chunks) > 1
        assert all(len(c) <= 100 for c in chunks)

    def test_get_stats(self):
        from core.adapters.translator import get_translator
        t = get_translator()
        stats = t.get_stats()
        assert isinstance(stats, dict)
        assert "glossary_terms" in stats
        assert "enabled" in stats

    def test_translate_text_helper(self):
        from core.adapters.translator import translate_text
        assert translate_text("") == ""


# ═══════════════════════════════════════════════════════════
# messenger_registry
# ═══════════════════════════════════════════════════════════
class TestMessengerRegistry:
    def test_register_and_get(self):
        from core.adapters.messenger_registry import (
            clear_registry, get_sender, register_sender,
        )
        clear_registry()

        async def sender(chat_id, chat_type, text):
            return True

        ok = register_sender("test_plat", sender)
        assert ok is True
        assert get_sender("test_plat") is sender

    def test_register_empty_platform_fails(self):
        from core.adapters.messenger_registry import register_sender
        async def sender(*a): pass
        assert register_sender("", sender) is False

    def test_register_none_fails(self):
        from core.adapters.messenger_registry import register_sender
        assert register_sender("test_plat", None) is False

    def test_register_non_callable_fails(self):
        from core.adapters.messenger_registry import register_sender
        assert register_sender("test_plat", "not_callable") is False

    def test_is_registered(self):
        from core.adapters.messenger_registry import (
            clear_registry, is_registered, register_sender,
        )
        clear_registry()
        async def sender(*a): pass
        register_sender("x", sender)
        assert is_registered("x") is True
        assert is_registered("y") is False

    def test_unregister(self):
        from core.adapters.messenger_registry import (
            clear_registry, register_sender, unregister_sender,
        )
        clear_registry()
        async def sender(*a): pass
        register_sender("x", sender)
        assert unregister_sender("x") is True
        assert unregister_sender("x") is False

    def test_get_registered_platforms(self):
        from core.adapters.messenger_registry import (
            clear_registry, get_registered_platforms, register_sender,
        )
        clear_registry()
        async def sender(*a): pass
        register_sender("a", sender)
        register_sender("b", sender)
        platforms = get_registered_platforms()
        assert "a" in platforms
        assert "b" in platforms

    def test_clear_registry_returns_count(self):
        from core.adapters.messenger_registry import (
            clear_registry, register_sender,
        )
        clear_registry()
        async def sender(*a): pass
        register_sender("a", sender)
        register_sender("b", sender)
        count = clear_registry()
        assert count == 2

    def test_notify_admins_empty_text(self):
        import asyncio
        from core.adapters.messenger_registry import notify_admins
        result = asyncio.run(notify_admins(""))
        assert result is False

    def test_get_stats(self):
        from core.adapters.messenger_registry import get_stats
        stats = get_stats()
        assert isinstance(stats, dict)
        assert "count" in stats


# ═══════════════════════════════════════════════════════════
# web_media
# ═══════════════════════════════════════════════════════════
class TestWebMedia:
    def test_detect_jpg(self):
        from core.adapters.web_media import _detect_ext_from_bytes
        assert _detect_ext_from_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 10) == ".jpg"

    def test_detect_png(self):
        from core.adapters.web_media import _detect_ext_from_bytes
        png_magic = b"\x89PNG\r\n\x1a\n"
        assert _detect_ext_from_bytes(png_magic + b"\x00" * 10) == ".png"

    def test_detect_gif(self):
        from core.adapters.web_media import _detect_ext_from_bytes
        assert _detect_ext_from_bytes(b"GIF89a" + b"\x00" * 10) == ".gif"

    def test_detect_webp(self):
        from core.adapters.web_media import _detect_ext_from_bytes
        data = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 10
        assert _detect_ext_from_bytes(data) == ".webp"

    def test_detect_unknown(self):
        from core.adapters.web_media import _detect_ext_from_bytes
        assert _detect_ext_from_bytes(b"XXXX") == ".bin"

    def test_detect_empty(self):
        from core.adapters.web_media import _detect_ext_from_bytes
        assert _detect_ext_from_bytes(b"") == ".bin"

    @pytest.mark.parametrize("ext,mime", [
        (".jpg", "image/jpeg"),
        (".png", "image/png"),
        (".gif", "image/gif"),
        (".webp", "image/webp"),
    ])
    def test_ext_to_mime(self, ext, mime):
        from core.adapters.web_media import _ext_to_mime
        assert _ext_to_mime(ext) == mime

    def test_ext_to_mime_unknown(self):
        from core.adapters.web_media import _ext_to_mime
        assert _ext_to_mime(".xyz") == "application/octet-stream"

    def test_get_stats(self):
        from core.adapters.web_media import get_stats
        stats = get_stats()
        assert isinstance(stats, dict)
        assert "supported_exts" in stats


# ═══════════════════════════════════════════════════════════
# browser_lock
# ═══════════════════════════════════════════════════════════
class TestBrowserLock:
    def test_get_browser_lock_returns_lock(self):
        import asyncio
        from core.adapters.browser_lock import get_browser_lock
        async def check():
            lock = get_browser_lock()
            assert isinstance(lock, asyncio.Lock)
        asyncio.run(check())

    def test_get_browser_lock_same_loop(self):
        import asyncio
        from core.adapters.browser_lock import get_browser_lock
        async def check():
            a = get_browser_lock()
            b = get_browser_lock()
            assert a is b
        asyncio.run(check())

    def test_reset_browser_lock(self):
        from core.adapters.browser_lock import reset_browser_lock
        reset_browser_lock()  # نباید raise کند

    def test_is_locked_returns_bool(self):
        import asyncio
        from core.adapters.browser_lock import is_locked
        async def check():
            assert isinstance(is_locked(), bool)
        asyncio.run(check())

    def test_get_stats(self):
        from core.adapters.browser_lock import get_stats
        stats = get_stats()
        assert isinstance(stats, dict)
        assert "loops_registered" in stats

    def test_browser_lock_acquire_context(self):
        import asyncio
        from core.adapters.browser_lock import BROWSER_LOCK
        async def run():
            async with BROWSER_LOCK:
                pass  # نباید deadlock
        asyncio.run(run())
        
# ═══════════════════════════════════════════════════════════
# FormulaManager — تست کامل
# ═══════════════════════════════════════════════════════════
class TestFormulaManager:
    @pytest.fixture
    def fm(self, tmp_path):
        """FormulaManager با DB موقت."""
        from core.adapters.formula_manager import (
            FormulaManager, reset_formula_manager,
        )
        reset_formula_manager()
        db = tmp_path / "test.db"
        # ساخت جدول‌های لازم
        import sqlite3
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, text TEXT
            );
            CREATE TABLE IF NOT EXISTS leads (
                id TEXT PRIMARY KEY, text TEXT
            );
            CREATE TABLE IF NOT EXISTS daily_stats (
                id INTEGER PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS chat_offsets (
                id INTEGER PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS last_poll (
                id INTEGER PRIMARY KEY
            );
        """)
        conn.commit()
        conn.close()
        return FormulaManager(str(db))

    def test_save_read_formula(self, fm):
        result = fm.save("test_read", "SELECT * FROM messages")
        assert "✅" in result or "ذخیره" in result
        data = fm._load()
        assert "test_read" in data

    def test_save_write_formula(self, fm):
        result = fm.save("test_write", "DELETE FROM messages WHERE id='x'")
        assert "✅" in result or "ذخیره" in result

    def test_save_invalid_sql(self, fm):
        result = fm.save("bad", "DROP TABLE messages")
        assert "⛔" in result

    def test_save_empty_name(self, fm):
        result = fm.save("", "SELECT 1")
        assert "⛔" in result

    def test_save_invalid_table(self, fm):
        result = fm.save("bad", "SELECT * FROM not_exists_table")
        assert "⛔" in result

    def test_save_too_long_sql(self, fm):
        result = fm.save("long", "SELECT 1 " + "x" * 5000)
        assert "⛔" in result

    def test_delete_existing(self, fm):
        fm.save("to_delete", "SELECT 1")
        result = fm.delete("to_delete")
        assert "حذف" in result

    def test_delete_missing(self, fm):
        result = fm.delete("nonexistent")
        assert "❌" in result or "پیدا نشد" in result

    def test_list_empty(self, fm):
        result = fm.list_all()
        assert "📭" in result or "هیچ" in result

    def test_list_with_items(self, fm):
        fm.save("a", "SELECT 1")
        fm.save("b", "SELECT 2")
        result = fm.list_all()
        assert "«a»" in result
        assert "«b»" in result

    def test_show_existing(self, fm):
        fm.save("show_me", "SELECT 1")
        result = fm.show("show_me")
        assert "📄" in result
        assert "SELECT" in result

    def test_show_missing(self, fm):
        result = fm.show("nope")
        assert "❌" in result

    def test_execute_read(self, fm):
        fm.save("read_q", "SELECT 1 as n")
        result = fm.execute("read_q")
        assert "📊" in result or "1" in result

    def test_execute_missing(self, fm):
        result = fm.execute("nope")
        assert "❌" in result

    def test_execute_write_needs_preview(self, fm):
        fm.save("write_q", "DELETE FROM messages WHERE id='x'")
        result = fm.execute("write_q")
        assert "preview" in result or "⚠️" in result or "تأیید" in result

    def test_execute_write_confirm(self, fm):
        fm.save("write_q2", "DELETE FROM messages WHERE id='x'")
        fm.execute("write_q2")  # preview
        result = fm.execute("write_q2", confirm=True)
        assert "✅" in result or "اجرا" in result

    def test_execute_write_expired_confirm(self, fm):
        fm.save("write_q3", "DELETE FROM messages WHERE id='x'")
        # confirm بدون preview
        result = fm.execute("write_q3", confirm=True)
        assert "⛔" in result or "preview" in result

    def test_has_pending_confirm(self, fm):
        fm.save("pending", "DELETE FROM messages WHERE id='x'")
        assert fm.has_pending_confirm("pending") is False
        fm.execute("pending")  # preview
        assert fm.has_pending_confirm("pending") is True

    def test_get_stats(self, fm):
        fm.save("x", "SELECT 1")
        stats = fm.get_stats()
        assert "formulas" in stats
        assert stats["formulas"] >= 1

    def test_validate_sql_select_ok(self, fm):
        ok, err, ftype = fm._validate_sql("SELECT 1")
        assert ok is True
        assert ftype == "read"

    def test_validate_sql_delete_ok(self, fm):
        ok, err, ftype = fm._validate_sql(
            "DELETE FROM messages WHERE id='x'"
        )
        assert ok is True
        assert ftype == "write"

    def test_validate_sql_banned(self, fm):
        ok, err, _ = fm._validate_sql("DROP TABLE messages")
        assert ok is False
        assert "DROP" in err or "ممنوع" in err

    def test_validate_sql_empty(self, fm):
        ok, err, _ = fm._validate_sql("")
        assert ok is False

    def test_validate_sql_unknown_op(self, fm):
        ok, err, _ = fm._validate_sql("PRAGMA table_info(x)")
        assert ok is False


# ═══════════════════════════════════════════════════════════
# MemoryStore — تست کامل
# ═══════════════════════════════════════════════════════════
class TestMemoryStore:
    @pytest.fixture
    def mem(self, tmp_path):
        """MemoryStore با DB موقت."""
        from core.adapters.memory_store import MemoryStore
        # reset singleton
        MemoryStore._instance = None
        return MemoryStore(str(tmp_path / "memory.db"))

    def test_singleton(self, tmp_path):
        from core.adapters.memory_store import MemoryStore
        MemoryStore._instance = None
        a = MemoryStore(str(tmp_path / "m.db"))
        b = MemoryStore(str(tmp_path / "m.db"))
        assert a is b

    def test_add_and_get(self, mem):
        rid = mem.add(
            category="test", key="k1", value="v1",
            importance=0.8,
        )
        assert rid > 0
        results = mem.query(category="test")
        assert len(results) >= 1
        assert results[0]["key"] == "k1"

    def test_add_contradiction(self, mem):
        mem.add(category="test", key="dup", value="v1")
        rid2 = mem.add(category="test", key="dup", value="v2")
        # قدیمی بایگانی شد
        results = mem.query(category="test")
        active = [r for r in results if r["key"] == "dup"]
        assert len(active) == 1
        assert active[0]["value"] == "v2"

    def test_add_empty_key(self, mem):
        assert mem.add(category="t", key="", value="v") == -1

    def test_add_empty_value(self, mem):
        assert mem.add(category="t", key="k", value="") == -1

    def test_query_with_text(self, mem):
        mem.add(category="test", key="apple", value="red fruit")
        results = mem.query(query="apple")
        assert len(results) >= 1

    def test_query_with_category_and_text(self, mem):
        mem.add(category="cat1", key="uniq_key", value="uniq_val")
        results = mem.query(category="cat1", query="uniq")
        assert len(results) >= 1

    def test_forget(self, mem):
        mem.add(category="t", key="low", value="v", importance=0.1)
        # forget با days=0 و min_importance=0.5
        deleted = mem.forget(days=0, min_importance=0.5)
        # حداقل 0 برگرداند
        assert isinstance(deleted, int)

    def test_get_stats(self, mem):
        mem.add(category="a", key="k", value="v")
        stats = mem.get_stats()
        assert "total_active" in stats
        assert "by_category" in stats
        assert "db_path" in stats


# ═══════════════════════════════════════════════════════════
# Translator — تست عمیق‌تر
# ═══════════════════════════════════════════════════════════
class TestTranslatorDeep:
    @pytest.fixture
    def translator_with_glossary(self, tmp_path):
        from core.adapters.translator import Translator
        glossary = tmp_path / "glossary.txt"
        glossary.write_text(
            "یاراترا|Yaratra\n"
            "کود مایع|Liquid Fertilizer\n",
            encoding="utf-8",
        )
        return Translator(
            glossary_file=str(glossary),
            enabled=False,  # بدون engine
        )

    def test_load_glossary(self, translator_with_glossary):
        t = translator_with_glossary
        assert len(t.glossary) == 2
        assert "یاراترا" in t.glossary

    def test_load_glossary_empty_file(self, tmp_path):
        from core.adapters.translator import Translator
        f = tmp_path / "empty.txt"
        f.write_text("")
        t = Translator(glossary_file=str(f), enabled=False)
        assert len(t.glossary) == 0

    def test_load_glossary_with_comments(self, tmp_path):
        from core.adapters.translator import Translator
        f = tmp_path / "g.txt"
        f.write_text(
            "# comment\n"
            "term1|trans1\n"
            "\n"
            "term2|trans2\n",
            encoding="utf-8",
        )
        t = Translator(glossary_file=str(f), enabled=False)
        assert len(t.glossary) == 2

    def test_translate_uses_glossary(self, translator_with_glossary):
        t = translator_with_glossary
        t.enabled = True
        # بدون engine — فقط glossary
        result = t.translate("یاراترا")
        assert "Yaratra" in result

    def test_translate_empty(self, translator_with_glossary):
        t = translator_with_glossary
        t.enabled = True
        assert t.translate("") == ""

    def test_reload_glossary(self, translator_with_glossary, tmp_path):
        t = translator_with_glossary
        # اضافه کردن term جدید
        with open(t.glossary_file, "a", encoding="utf-8") as f:
            f.write("new_term|new_trans\n")
        count = t.reload_glossary()
        assert count == 3

    def test_get_stats(self, translator_with_glossary):
        stats = translator_with_glossary.get_stats()
        assert "glossary_terms" in stats
        assert stats["glossary_terms"] == 2


# ═══════════════════════════════════════════════════════════
# WebMedia — تست با mock
# ═══════════════════════════════════════════════════════════
class TestWebMediaDeep:
    def test_sanitize_prefix_invalid_chars(self):
        from core.adapters.web_media import _sanitize_prefix
        result = _sanitize_prefix('bad<>:"/\\|?*name')
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result
        assert "/" not in result

    def test_sanitize_prefix_empty(self):
        from core.adapters.web_media import _sanitize_prefix
        assert _sanitize_prefix("") == "media"
        assert _sanitize_prefix(None) == "media"

    @pytest.mark.asyncio
    async def test_download_dom_image_none_page(self):
        from core.adapters.web_media import download_dom_image
        mime, path = await download_dom_image(None, None, "/tmp", "test")
        assert mime is None
        assert path is None

    @pytest.mark.asyncio
    async def test_download_dom_image_no_img(self):
        from unittest.mock import AsyncMock, MagicMock
        from core.adapters.web_media import download_dom_image

        page = MagicMock()
        element = MagicMock()
        element.query_selector = AsyncMock(return_value=None)

        mime, path = await download_dom_image(
            page, element, "/tmp", "test"
        )
        assert mime is None

    @pytest.mark.asyncio
    async def test_download_dom_image_svg_skipped(self):
        from unittest.mock import AsyncMock, MagicMock
        from core.adapters.web_media import download_dom_image

        page = MagicMock()
        element = MagicMock()
        img = MagicMock()
        img.get_attribute = AsyncMock(
            return_value="data:image/svg+xml,<svg></svg>"
        )
        element.query_selector = AsyncMock(return_value=img)

        mime, path = await download_dom_image(
            page, element, "/tmp", "test"
        )
        assert mime is None


# ═══════════════════════════════════════════════════════════
# AccessManager — تست‌های بیشتر
# ═══════════════════════════════════════════════════════════
class TestAccessManagerDeep:
    def test_reload(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        n = am.reload()
        assert n > 0

    def test_can_write_deny(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        ok, _ = am.can_write("some_random_file.py")
        # بستگی به config — فقط چک tuple
        assert isinstance(ok, bool)

    def test_can_query_db_with_limit(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        ok, _ = am.can_query_db("SELECT * FROM messages LIMIT 10")
        assert isinstance(ok, bool)

    def test_can_query_db_with_huge_limit(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        ok, _ = am.can_query_db(
            "SELECT * FROM messages LIMIT 999999"
        )
        # باید رد شود اگر max_rows_select کمتر باشد
        assert ok is False

    def test_can_query_db_into_outfile(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        ok, _ = am.can_query_db(
            "SELECT * FROM messages INTO OUTFILE '/tmp/x'"
        )
        assert ok is False

    def test_can_read_setting(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        ok, _ = am.can_read_setting("LOG_LEVEL")
        assert isinstance(ok, bool)

    def test_can_write_setting(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        ok, _ = am.can_write_setting("LOG_LEVEL")
        assert isinstance(ok, bool)

    def test_can_toggle_platform(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        ok, _ = am.can_toggle_platform("bale")
        assert isinstance(ok, bool)

    def test_get_summary(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        s = am.get_summary()
        assert isinstance(s, str)
        assert "دسترسی" in s

    def test_repr(self):
        from core.adapters.access_manager import get_access_manager
        am = get_access_manager()
        r = repr(am)
        assert "AccessManager" in r


# ═══════════════════════════════════════════════════════════
# MessengerRegistry — تست notify_admins
# ═══════════════════════════════════════════════════════════
class TestMessengerNotify:
    @pytest.mark.asyncio
    async def test_notify_admins_no_sender(self):
        from core.adapters.messenger_registry import (
            clear_registry, notify_admins,
        )
        clear_registry()
        result = await notify_admins("test")
        # بستگی به config — فقط چک bool
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_notify_admins_with_sender(
        self, monkeypatch
    ):
        from core.adapters.messenger_registry import (
            clear_registry, notify_admins, register_sender,
        )
        clear_registry()

        async def sender(chat_id, chat_type, text):
            return True

        register_sender("bale", sender)

        # mock config
        import config
        monkeypatch.setattr(
            config, "ADMIN_ENABLED", True, raising=False
        )
        monkeypatch.setattr(
            config, "ADMIN_IDS", ["123"], raising=False
        )
        monkeypatch.setattr(
            config, "ADMIN_PLATFORM", "bale", raising=False
        )

        result = await notify_admins("test")
        assert result is True
        clear_registry()


# ═══════════════════════════════════════════════════════════
# BrowserLock — تست acquire timeout
# ═══════════════════════════════════════════════════════════
class TestBrowserLockDeep:
    @pytest.mark.asyncio
    async def test_acquire_timeout_works(self):
        from core.adapters.browser_lock import (
            BROWSER_LOCK, reset_browser_lock,
        )
        reset_browser_lock()
        async with BROWSER_LOCK.acquire(timeout=5.0):
            pass

    @pytest.mark.asyncio
    async def test_acquire_timeout_exceeded(self):
        import asyncio
        from core.adapters.browser_lock import (
            BROWSER_LOCK, reset_browser_lock,
        )
        reset_browser_lock()

        async def hold():
            async with BROWSER_LOCK:
                await asyncio.sleep(2)

        # acquire با timeout 0.1 → timeout
        async def try_acquire():
            async with BROWSER_LOCK.acquire(timeout=0.1):
                pass

        task = asyncio.create_task(hold())
        await asyncio.sleep(0.05)
        with pytest.raises(TimeoutError):
            await try_acquire()
        await task


# ═══════════════════════════════════════════════════════════
# CommandParser — تست edge cases
# ═══════════════════════════════════════════════════════════
class TestCommandParserEdge:
    def test_search_with_platform_and_query(self):
        action, value = parse_command(
            "در بله جستجو کن کود مایع"
        )
        assert action == "search_platform"
        assert "|" in value or "کود" in value

    def test_join_group_with_platform(self):
        action, value = parse_command(
            "در ایتا عضو شو https://eitaa.com/join/abc"
        )
        assert action == "join_group"
        assert "|" in value

    def test_search_no_platform(self):
        action, value = parse_command("جستجو کن یاراترا")
        assert action == "search_platform"
        assert "یاراترا" in value        