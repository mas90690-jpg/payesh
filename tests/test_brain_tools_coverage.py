"""Deep coverage for brain.py tools (offline, mocked clients) — the largest module."""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.brain as brain_mod
from core.brain import (
    APIMemory,
    Brain,
    BrainMemory,
    CodeReviewer,
    PersianBrainHandler,
    SelfHealer,
    SmartCache,
    TaskPlanner,
    WebSearch,
)


@pytest.fixture
def brain(tmp_path, monkeypatch):
    """Brain fully offline with chromadb avoided."""
    for attr in ("BRAIN_DIR", "CACHE_DIR", "MEMORY_DIR", "BACKUP_DIR", "HOOKS_DIR"):
        monkeypatch.setattr(f"core.brain.{attr}", tmp_path)
    monkeypatch.setattr("core.brain.PROJECT_BASE", tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(
        "core.brain.BrainMemory",
        lambda **kw: MagicMock(
            short_term=[],
            summaries=[],
            api_memory=None,
            collection=None,
            add=lambda *a, **k: None,
            get_context=lambda q="": [],
            search=lambda *a, **k: [],
            clear=lambda: None,
        ),
    )
    monkeypatch.setattr(
        "core.brain.ProjectSemanticSearch",
        lambda: MagicMock(
            collection=None, search=lambda q, n=5: [], index_files=lambda *a, **k: 0
        ),
    )
    b = Brain(read_only=True)
    return b


# ─────────── file tools ───────────
@pytest.mark.asyncio
async def test_brain_read_file(brain, tmp_path):
    (tmp_path / "a.txt").write_text("hello world", encoding="utf-8")
    out = await brain._read_file("a.txt")
    assert "hello world" in out
    assert "وجود ندارد" in await brain._read_file("nope.txt")
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02\xff")
    assert "باینری" in await brain._read_file("bin.dat")


@pytest.mark.asyncio
async def test_brain_write_edit_delete_move(brain, tmp_path):
    assert "فقط‌خواندنی" in await brain._write_file("data/x.txt", "hi")
    brain.read_only = False
    out = await brain._write_file("data/y.txt", "new")
    assert "نوشته شد" in out
    assert (tmp_path / "data" / "y.txt").read_text(encoding="utf-8") == "new"
    # edit
    out = await brain._edit_file("data/y.txt", "new", "newer")
    assert "ویرایش شد" in out
    assert "یافت نشد" in await brain._edit_file("data/y.txt", "zzz", "x")
    assert "وجود ندارد" in await brain._edit_file("data/missing.txt", "a", "b")
    # delete
    out = await brain._delete_file("data/y.txt")
    assert "حذف شد" in out
    assert "وجود ندارد" in await brain._delete_file("data/y.txt")
    # move
    (tmp_path / "m.txt").write_text("m", encoding="utf-8")
    assert "→" in await brain._move_file("m.txt", "data/m2.txt")
    assert "وجود ندارد" in await brain._move_file("missing.txt", "data/m3.txt")


@pytest.mark.asyncio
async def test_brain_list_directory(brain, tmp_path):
    (tmp_path / "f1.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    out = await brain._list_directory(".")
    assert "f1.txt" in out and "sub" in out
    assert "وجود ندارد" in await brain._list_directory("nope")


@pytest.mark.asyncio
async def test_brain_git_tools(brain):
    for coro in (
        brain._git_status(),
        brain._git_diff(),
        brain._git_diff("x"),
        brain._git_log(),
    ):
        out = await coro
        assert isinstance(out, str)


@pytest.mark.asyncio
async def test_brain_execute_command_whitelist(brain, monkeypatch):
    assert "فقط‌خواندنی" in await brain._execute_command("ls")
    brain.read_only = False
    assert "whitelist" in await brain._execute_command("rm -rf /")
    r = MagicMock(stdout="hi", stderr="")
    monkeypatch.setattr(brain_mod.subprocess, "run", lambda *a, **k: r)
    assert "hi" in await brain._execute_command("echo hi")


@pytest.mark.asyncio
async def test_brain_system_and_processes(brain, monkeypatch):
    out = await brain._get_system_info()
    assert "CPU" in out
    out = await brain._list_processes()
    assert isinstance(out, str)
    # kill patch
    p = MagicMock()
    p.name.return_value = "python"
    p.terminate.return_value = None
    p.is_running.return_value = False
    import psutil as psutil_mod
    monkeypatch.setattr(psutil_mod, "Process", lambda pid: p)
    brain.read_only = False
    assert "کشته شد" in await brain._kill_process(123)
    # ping patch
    r = MagicMock(stdout="Reply from 127.0.0.1")
    monkeypatch.setattr(brain_mod.subprocess, "run", lambda *a, **k: r)
    assert "Reply" in await brain._ping_host("localhost")


@pytest.mark.asyncio
async def test_brain_run_tests_and_security(brain, monkeypatch):
    r = MagicMock(stdout="1 passed", stderr="")
    monkeypatch.setattr(brain_mod.subprocess, "run", lambda *a, **k: r)
    assert "1 passed" in await brain._run_tests()
    assert "گزارش پوشش" in await brain._run_tests_with_coverage("x")
    r2 = MagicMock(stdout="No issues found")
    monkeypatch.setattr("core.brain.subprocess.run", lambda *a, **k: r2)
    assert "No issues" in await brain._analyze_security("x")
    monkeypatch.setattr(
        "core.brain.subprocess.run",
        MagicMock(side_effect=FileNotFoundError),
    )
    assert "bandit" in await brain._analyze_security("x")


@pytest.mark.asyncio
async def test_brain_backup_project(brain, tmp_path):
    assert "فقط‌خواندنی" in await brain._backup_project()
    brain.read_only = False
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    out = await brain._backup_project()
    assert "بکاپ" in out
    assert list((tmp_path).glob("Project_Backup_*.zip"))


@pytest.mark.asyncio
async def test_brain_execute_python(brain):
    out = await brain._execute_python("import os; os.system('x')")
    assert "خطرناک" in out
    r = MagicMock(stdout="42\n", stderr="")
    with patch.object(brain_mod.subprocess, "run", return_value=r):
        out = await brain._execute_python("print(42)")
        assert "42" in out


@pytest.mark.asyncio
async def test_brain_analyze_code_syntax_error(brain, tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def (:", encoding="utf-8")
    assert "خطای نحوی" in await brain._analyze_code("bad.py")


@pytest.mark.asyncio
async def test_brain_grep_search(brain, tmp_path):
    (tmp_path / "a.py").write_text("def foo(): pass\n", encoding="utf-8")
    out = await brain._grep_search("foo")
    assert "a.py:1" in out
    assert "چیزی یافت نشد" in await brain._grep_search("zzznothing")


@pytest.mark.asyncio
async def test_brain_query_database(brain):
    class _QR:
        async def fetchall(self):
            return [(1, "x")]

    class _DB:
        async def execute(self, q, params=()):
            return _QR()

    brain.db = _DB()
    out = await brain._query_database("SELECT * FROM t")
    assert "نتایج" in out


@pytest.mark.asyncio
async def test_brain_browse_web_clipboard_zip(brain, tmp_path, monkeypatch):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.text = "<html><body><p>content here</p></body></html>"
    import requests as requests_mod
    monkeypatch.setattr(requests_mod, "get", lambda *a, **k: resp)
    out = await brain._browse_web("http://x")
    assert "content here" in out
    # clipboard
    import pyperclip as pyperclip_mod
    monkeypatch.setattr(pyperclip_mod, "paste", lambda: "clip data")
    assert "clip data" in await brain._clipboard_get()
    # zip compress + extract
    src = tmp_path / "zsrc"
    src.mkdir()
    (src / "f.txt").write_text("x", encoding="utf-8")
    out = await brain._zip_archive("zsrc", "z.zip", "compress")
    assert "آرشیو" in out
    out2 = await brain._zip_archive("z.zip", "zout", "extract")
    assert "باز شد" in out2
    assert "وجود ندارد" in await brain._zip_archive("nope", "z", "compress")


@pytest.mark.asyncio
async def test_brain_project_context_and_dashboard(brain):
    assert "پروژه" in await brain._get_project_context()
    out = await brain._system_dashboard()
    assert isinstance(out, str)


@pytest.mark.asyncio
async def test_brain_think_and_agent_offline(brain):
    assert "آفلاین" in await brain._think("x")
    brain.client = None
    out = await brain.agent("task")
    assert isinstance(out, str)
    brain.busy = True
    assert "در حال انجام کار" in await brain.agent("task")
    brain.busy = False


@pytest.mark.asyncio
async def test_brain_process_admin_command(brain, monkeypatch):
    assert "مغز هوشمند" in await brain.process_admin_command("/status")
    assert "راهنما" in await brain.process_admin_command("/help")
    assert "پاک" in await brain.process_admin_command("/clear")
    assert "آمار" in await brain.process_admin_command("/stats")
    assert "فعال شد" in await brain.process_admin_command("/mode code")
    # AI path mocked
    ai = MagicMock()
    ai.available = AsyncMock(return_value=True)
    ai.parse_intent = AsyncMock(return_value={"action": "status", "args": {}})
    import core.services.ai_assistant as ai_mod
    monkeypatch.setattr(ai_mod, "get_ai_assistant", lambda: ai, raising=False)
    out = await brain.process_admin_command("وضعیت")
    assert isinstance(out, str)
    # offline path
    monkeypatch.setattr("core.brain.AI_ENABLED", False)
    assert "آفلاین" in await brain.process_admin_command("هر چیزی")


@pytest.mark.asyncio
async def test_brain_handle_action(brain, monkeypatch):
    db = MagicMock()
    db.get_keyword_stats = AsyncMock(return_value=[("k", 2)])
    db.get_last_poll = AsyncMock(return_value=datetime.now())
    db.get_total_count = AsyncMock(return_value={"messages": 1, "leads": 1, "groups": 1})
    db.get_daily_stats = AsyncMock(return_value=[{"date": "d", "count": 1}])
    db.get_high_score_leads = AsyncMock(
        return_value=[{"id": "1", "platform": "bale", "chat_name": "g", "text": "t", "keywords": "k", "opportunity_score": 5, "timestamp": "t"}]
    )
    brain.db = db
    monkeypatch.setattr("core.brain.AI_ENABLED", False)
    assert "مغز هوشمند" in (await brain._handle_action("status", {}, "وضعیت"))
    assert "کلیدواژه" in (await brain._handle_action("keywords", {}, "x"))
    out = await brain._handle_action("platform_status", {"value": "bale"}, "x")
    assert "فعال" in out or "دقیقه" in out
    assert "پیام" in (await brain._handle_action("report", {}, "x"))
    assert "CRM" in (await brain._handle_action("crm", {}, "x"))
    logs_out = await brain._handle_action("logs", {}, "x")
    assert "لاگ" in logs_out
    assert "جزئیات سرنخ" in (await brain._handle_action("lead_details", {"value": "last"}, "x"))
    assert "افزودن کلیدواژه" in (await brain._handle_action("add_keyword", {"value": "k"}, "x"))
    assert "حذف کلیدواژه" in (await brain._handle_action("remove_keyword", {"value": "k"}, "x"))
    assert "وجود دارد" in (await brain._handle_action("has_keyword", {"value": "یاراترا"}, "x"))
    assert await brain._handle_action("unknown", {}, "x") is None


@pytest.mark.asyncio
async def test_brain_health_keywords_reports(brain, monkeypatch):
    db = MagicMock()
    db.get_keyword_stats = AsyncMock(return_value=[("k", 2)])
    db.get_last_poll = AsyncMock(return_value=datetime.now())
    db.get_total_count = AsyncMock(return_value={"messages": 1, "leads": 1, "groups": 1})
    db.get_daily_stats = AsyncMock(return_value=[{"date": "d", "count": 1}])
    db.get_high_score_leads = AsyncMock(return_value=[])
    brain.db = db
    brain.db_path = None
    hc = MagicMock()
    hc.check_all = AsyncMock(return_value={"overall_status": "healthy"})
    import core.ops.health_check as hc_mod
    monkeypatch.setattr(hc_mod, "HealthChecker", lambda db: hc)
    out = await brain._get_health_report()
    assert "healthy" in out
    out = await brain._get_keywords_report()
    assert "k" in out
    out = await brain._get_platform_status("bale")
    assert "فعال" in out or "دقیقه" in out
    out = await brain._get_report()
    assert "پیام" in out
    out = await brain._get_lead_details()
    assert "سرنخ" in out
    assert "وجود دارد" in await brain._check_keyword("یاراترا")
    assert "لطفاً" in await brain._check_keyword("")
    brain.db = None
    assert "دیتابیس متصل نیست" in await brain._get_keywords_report()
    assert "دیتابیس متصل نیست" in await brain._get_report()
    assert "دیتابیس متصل نیست" in await brain._get_lead_details()


def test_brain_get_recent_logs(brain, tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text("line1\nline2\n", encoding="utf-8")
    monkeypatch.setattr(brain_mod, "LOGS_DIR", logs)
    out = brain._get_recent_logs()
    assert "app.log" in out


def test_brain_memory_helpers(tmp_path, monkeypatch):
    monkeypatch.setattr("core.brain.MEMORY_DIR", tmp_path)
    m = BrainMemory(max_tokens=100, use_api=False)
    m.collection = None
    m.api_memory = None
    m.add("user", "hello")
    assert len(m.short_term) == 1
    assert m.get_context() == [{"role": "user", "content": "hello", "timestamp": m.short_term[0]["timestamp"]}] or len(m.get_context()) == 1
    assert m.search("x") == []
    for i in range(25):
        m.add("user", f"msg {i}")
    assert m.summaries  # compressed
    m.clear()
    assert m.short_term == []
    asyncio.run(m.close())


def test_brain_memory_with_collection(tmp_path, monkeypatch):
    monkeypatch.setattr("core.brain.MEMORY_DIR", tmp_path)
    coll = MagicMock()
    coll.query.return_value = {
        "documents": [["doc1"]],
        "metadatas": [[{"role": "user"}]],
    }
    m = BrainMemory(max_tokens=1000, use_api=False)
    m.collection = coll
    m.api_memory = None
    ctx = m.get_context("q")
    assert any("doc1" in c["content"] for c in ctx)
    res = m.search("q")
    assert res and res[0]["content"] == "doc1"[:300]


@pytest.mark.asyncio
async def test_api_memory(brain_mod=APIMemory):
    class _Resp:
        status = 200

        async def json(self):
            return {"results": [{"content": "c"}], "context": [{"content": "c"}]}

        async def text(self):
            return ""

    class _CM:
        def __init__(self, resp):
            self.resp = resp

        async def __aenter__(self):
            return self.resp

        async def __aexit__(self, *a):
            return False

    class _Session:
        def __init__(self, *a, **k):
            self.posted = []

        def post(self, url, json=None):
            self.posted.append((url, json))
            return _CM(_Resp())

        async def close(self):
            return None

    mem = APIMemory("http://x")
    mem._session = _Session()
    await mem.add("user", "c", {})
    res = await mem.search("q")
    assert res == [{"content": "c"}]
    ctx = await mem.get_context("q")
    assert ctx == [{"content": "c"}]
    await mem.clear()
    await mem.close()


def test_api_memory_error_handling():
    mem = APIMemory("http://x")

    class _Broken:
        def post(self, url, json=None):
            raise RuntimeError("conn")

    mem._session = _Broken()

    assert asyncio.run(mem.search("q")) == []


def test_web_search_with_session():
    ws = WebSearch()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.text = (
        "<div class='result'><a class='result__a'>Title</a>"
        "<span class='result__snippet'>Snippet text</span></div>"
    )
    ws.session = MagicMock()
    ws.session.get.return_value = resp
    out = ws.search("کود")
    assert "Title" in out and "Snippet" in out
    resp.text = "<div>no results</div>"
    assert "یافت نشد" in ws.search("کود")


def test_web_search_session_none():
    ws = WebSearch()
    ws.session = None
    assert "requests" in ws.search("x")


def test_task_planner_execute_plan(tmp_path, monkeypatch):
    monkeypatch.setattr("core.brain.BRAIN_DIR", tmp_path)
    agent = MagicMock(client=None)
    agent.agent = MagicMock(return_value="result")
    tp = TaskPlanner(agent)
    out = tp.execute_plan("ساخت x")
    assert "برنامه تکمیل شد" in out
    agent.agent.side_effect = ["r1", "r2"]
    tp.plans = {"t": [{"step": 1, "action": "a", "description": "d", "dependencies": []},
                 {"step": 2, "action": "b", "description": "e", "dependencies": [1]}]}
    out = tp.execute_plan("t")
    assert "مرحله" in out


def test_self_healer_online():
    agent = MagicMock()
    client = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content="```python\nprint(1)\n```"))]
    client.chat.completions.create.return_value = resp
    agent.client = client
    agent._read_file = lambda p: "code"
    agent._write_file = lambda p, c: "✅"
    agent._run_tests = lambda p: "✅ tests passed"
    out = SelfHealer(agent).fix_bug("x.py", "err")
    assert "باگ رفع شد" in out


def test_code_reviewer_online():
    agent = MagicMock()
    client = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content="Review findings"))]
    client.chat.completions.create.return_value = resp
    agent.client = client
    agent._read_file = lambda p: "code"
    out = CodeReviewer(agent).review("x.py")
    assert "بازبینی کد" in out


def test_smart_cache_ttl_and_model_scope(tmp_path, monkeypatch):
    monkeypatch.setattr("core.brain.CACHE_DIR", tmp_path)
    c = SmartCache("t")
    c.set("q", "a", "m1")
    assert c.get("q", "m1") == "a"
    assert c.get("q", "m2") is None
    # get() enforces TTL (timestamp from epoch -> expired -> None)
    c.cache[c._key("old", "m")] = {"answer": "x", "timestamp": 1}
    assert c.get("old", "m") is None


def test_persian_brain_handler_env():
    PersianBrainHandler.setup_environment()
    assert isinstance(PersianBrainHandler.is_persian("سلام"), bool)
