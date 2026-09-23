"""Coverage for brain.py offline helpers + SafeBrain — mocked, no network."""
import time
from unittest.mock import MagicMock

import pytest

from core.brain import (
    Brain,
    CodeReviewer,
    LearningEngine,
    PersianBrainHandler,
    SelfHealer,
    SmartCache,
    TaskPlanner,
    WebSearch,
    human_bytes,
    safe_path,
)
from core.brain import (
    TestGenerator as BrainTestGenerator,  # alias avoids pytest collection
)
from core.safe_brain import (
    SafeBrain,
    SafeCache,
    SafeMemory,
    get_safe_brain,
)


# ─────────── brain.py: pure helpers ───────────
def test_persian_helpers():
    assert PersianBrainHandler.is_persian("سلام چطوری")
    assert not PersianBrainHandler.is_persian("hello world")
    assert not PersianBrainHandler.is_persian("")
    assert PersianBrainHandler.fix("علي كيست") == "علی کیست"
    assert PersianBrainHandler.fix("") == ""
    assert PersianBrainHandler.fix("\u200eabc") == "abc"


def test_human_bytes():
    assert human_bytes(0) == "0.0 B"
    assert human_bytes(2048) == "2.0 KB"
    assert human_bytes(1024**3) == "1.0 GB"


def test_safe_path(tmp_path):
    base = tmp_path / "proj"
    base.mkdir()
    assert str(safe_path("data/x.txt", base)).startswith(str(base))
    with pytest.raises(ValueError):
        safe_path("../../etc/passwd", base)


# ─────────── SmartCache ───────────
def test_smart_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("core.brain.CACHE_DIR", tmp_path)
    c = SmartCache("brain_cache")
    assert c.get("q", "m") is None
    c.set("q", "a", "m")
    assert c.get("q", "m") == "a"
    assert c.get("q", "other") is None
    assert c.stats()["entries"] == 1
    # get() does not enforce TTL; expiry only trims at save. Assert actual.
    c.cache[c._key("old", "m")] = {"answer": "x", "timestamp": time.time() - 99999}
    assert c.get("old", "m") == "x"
    c.clear()
    assert c.stats()["entries"] == 0


def test_smart_cache_max_size(tmp_path, monkeypatch):
    monkeypatch.setattr("core.brain.CACHE_DIR", tmp_path)
    c = SmartCache("brain_cache")
    c.max_size = 3
    for i in range(6):
        c.set(f"q{i}", "a", "m")
    assert c.stats()["entries"] <= 3


def test_smart_cache_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setattr("core.brain.CACHE_DIR", tmp_path)
    (tmp_path / "brain_cache.json").write_text("{bad", encoding="utf-8")
    assert SmartCache("brain_cache").cache == {}


# ─────────── LearningEngine / Planner / Healer / Generators ───────────
def test_learning_engine(tmp_path, monkeypatch):
    monkeypatch.setattr("core.brain.BRAIN_DIR", tmp_path)
    le = LearningEngine(None)
    le.record_interaction(
        "قیمت یاراترا را تحلیل کن و تست بنویس",
        "ok",
        True,
        details={"tools_used": ["run_tests", "grep_search"]},
    )
    le.record_interaction("چیزی خراب شد", "no", False, details={"tools_used": []})
    assert len(le.successful_interactions) == 1
    assert len(le.failed_interactions) == 1
    assert "task_type_test" in le.learned_patterns
    assert (tmp_path / "learned_patterns.json").exists()
    assert le.suggest_tool_for_task("nothing relevant") is None


def test_task_planner_offline(tmp_path, monkeypatch):
    monkeypatch.setattr("core.brain.BRAIN_DIR", tmp_path)
    agent = MagicMock(client=None)
    tp = TaskPlanner(agent)
    assert tp.plan_task("ساخت ماژول")[0]["action"] == "execute"
    tp.save()
    assert (tmp_path / "plans.json").exists()


def test_healer_and_generators_offline():
    agent = MagicMock(client=None, _read_file=lambda p: "code")
    assert "آفلاین" in SelfHealer(agent).fix_bug("x.py", "err")
    assert "آفلاین" in BrainTestGenerator(agent).generate_tests("x.py")
    assert "آفلاین" in CodeReviewer(agent).review("x.py")
    ws = WebSearch()
    ws.session = None
    assert "requests" in ws.search("x")


# ─────────── Brain: read-only & offline ───────────
@pytest.fixture
def offline_brain(tmp_path, monkeypatch):
    for attr in ("BRAIN_DIR", "CACHE_DIR", "MEMORY_DIR", "BACKUP_DIR", "HOOKS_DIR"):
        monkeypatch.setattr(f"core.brain.{attr}", tmp_path)
    monkeypatch.setattr("core.brain.PROJECT_BASE", tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    b = Brain(read_only=True)
    b.semantic_search.collection = None
    b.semantic_search.search = lambda q, n=5: []
    return b


@pytest.mark.asyncio
async def test_brain_read_only_blocks_writes(offline_brain):
    assert "فقط‌خواندنی" in await offline_brain._write_file("data/x.txt", "hi")
    assert "فقط‌خواندنی" in await offline_brain._edit_file("data/x.txt", "a", "b")
    assert "فقط‌خواندنی" in await offline_brain._delete_file("data/x.txt")
    assert "فقط‌خواندنی" in await offline_brain._move_file("a", "b")
    assert "فقط‌خواندنی" in await offline_brain._execute_command("ls")
    assert "فقط‌خواندنی" in await offline_brain._git_commit("m")
    assert "فقط‌خواندنی" in await offline_brain._backup_project()


@pytest.mark.asyncio
async def test_brain_offline_agent(offline_brain):
    offline_brain.client = None
    assert "آفلاین" in offline_brain._offline_agent("هر چیزی")
    out = await offline_brain.process_admin_command("وضعیت")
    assert "مغز هوشمند" in out or "آفلاین" in out
    assert "راهنمای مغز" in offline_brain.get_help()
    assert "آمار مغز" in offline_brain.get_stats()
    assert "گزارش عملکرد" in offline_brain.generate_report()
    assert isinstance(offline_brain.analyze_logs(), str)
    assert isinstance(offline_brain.build_from_map("x"), str)
    assert isinstance(offline_brain.self_upgrade(), str)


def test_brain_mode_and_prompts(offline_brain):
    offline_brain.set_mode("code")
    assert offline_brain.current_mode == "code"
    offline_brain.set_mode("nonsense")
    assert offline_brain.current_mode == "code"
    assert offline_brain._select_model("یک تابع پایتون بنویس") == "deepseek-reasoner"
    assert offline_brain._select_model("سلام") == offline_brain.model
    assert "SENIOR" in offline_brain._build_system_prompt("code")
    assert "SENIOR" not in offline_brain._build_system_prompt("chat")


@pytest.mark.asyncio
async def test_brain_sandbox_and_tools(offline_brain):
    assert "مجاز نیست" in await offline_brain._execute_tool("rm_rf", {})
    out = await offline_brain._execute_python("import os; os.system('x')")
    assert "خطرناک" in out
    out2 = await offline_brain._execute_python("print('hi')")
    assert "hi" in out2
    out3 = await offline_brain._query_database("DELETE FROM messages")
    assert "SELECT" in out3
    assert await offline_brain._query_database("SELECT 1") == "⚠️ دیتابیس متصل نیست."


@pytest.mark.asyncio
async def test_brain_analyze_code(offline_brain, tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("def foo():\n    return 1\nclass Bar:\n    pass\n", encoding="utf-8")
    out = await offline_brain._analyze_code(str(f))
    assert "foo" in out and "Bar" in out


# ─────────── SafeBrain ───────────
@pytest.fixture
def safe_brain(tmp_path, monkeypatch):
    monkeypatch.setattr("core.safe_brain.SAFE_MEMORY_DIR", tmp_path / "mem")
    monkeypatch.setattr("core.safe_brain.SAFE_CACHE_DIR", tmp_path / "cache")
    (tmp_path / "mem").mkdir(exist_ok=True)
    (tmp_path / "cache").mkdir(exist_ok=True)
    return SafeBrain()


def test_safe_memory_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("core.safe_brain.SAFE_MEMORY_DIR", tmp_path)
    m = SafeMemory("t", max_entries=3)
    for i in range(5):
        m.add("user", f"msg {i}")
    assert m.count == 3
    assert m.get_recent(2)[-1]["content"] == "msg 4"
    assert "msg" in m.get_context(2)
    assert len(m.search("msg 3")) >= 1
    m.clear()
    assert m.count == 0


def test_safe_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("core.safe_brain.SAFE_CACHE_DIR", tmp_path)
    c = SafeCache("t")
    assert c.get("q") is None
    c.set("q", "a")
    assert c.get("q") == "a"
    assert c.size == 1
    c.clear()
    assert c.size == 0


def test_safe_brain_security():
    sb = SafeBrain(full_access=False)
    assert sb._is_safe("rm -rf /") == (False, "الگوی ممنوعه: rm -rf")
    assert sb._is_safe("hello") == (True, "")
    assert sb._is_destructive_action("status") is False
    assert sb._is_destructive_action("restart") is True
    sb2 = SafeBrain(full_access=True)
    assert sb2._is_safe("rm -rf /") == (True, "")


def test_safe_brain_rule_parse():
    sb = SafeBrain()
    assert sb._rule_parse("راهنما")[0] == "help"
    assert sb._rule_parse("وضعیت ربات")[0] == "status"
    assert sb._rule_parse("کلیدواژه «یاراترا» را اضافه کن")[0] == "add_keyword"
    assert sb._rule_parse("گزارش بده")[0] == "report"
    assert sb._rule_parse("لینکدین کار میکنه؟")[0] == "platform_status"
    assert sb._rule_parse("سلام") == (None, None)


@pytest.mark.asyncio
async def test_safe_brain_process(safe_brain):
    out = await safe_brain.process("rm -rf /")
    assert "مسدود" in out
    assert safe_brain.stats["blocked"] == 1
    await safe_brain.process("وضعیت")
    await safe_brain.process("وضعیت")
    assert safe_brain.stats["cached"] >= 1


def test_safe_brain_helpers(safe_brain):
    assert "SafeBrain" in safe_brain._status()
    assert "کلیدواژه" in safe_brain._keywords()
    assert "بله" in safe_brain._has_keyword("یاراترا")
    assert "WinDoctor" in safe_brain._fallback_response("x")
    assert safe_brain._lead_details("last") != ""


def test_safe_brain_add_remove_keyword(tmp_path, monkeypatch):
    import config

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr("core.safe_brain.BASE_DIR", str(tmp_path))
    assert "اضافه" in SafeBrain._add_keyword("کودتستی")
    assert "قبل هست" in SafeBrain._add_keyword("کودتستی")
    assert "حذف شد" in SafeBrain._remove_keyword("کودتستی")
    assert "نیست" in SafeBrain._remove_keyword("کودتستی")


def test_safe_brain_singleton(tmp_path, monkeypatch):
    monkeypatch.setattr("core.safe_brain.SAFE_MEMORY_DIR", tmp_path / "mem")
    monkeypatch.setattr("core.safe_brain.SAFE_CACHE_DIR", tmp_path / "cache")
    (tmp_path / "mem").mkdir(exist_ok=True)
    (tmp_path / "cache").mkdir(exist_ok=True)
    a = get_safe_brain(full_access=True)
    b = get_safe_brain()
    assert a is b
    assert b.full_access is True
