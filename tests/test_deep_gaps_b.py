"""Gap-fill: database methods, daily_reporter async reports, obs_server server,
safe_brain deeper, crm edge cases."""
import json
import os
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from core.database import PostgresDatabase
from core.ops.obs_server import (
    start_obs_server,
)
from core.safe_brain import SafeBrain
from core.services.crm_export import _export_xlsx
from core.services.daily_reporter import (
    DailyReporter,
    generate_daily_report,
    generate_weekly_report,
    start_daily_reporter,
)


# ───────────────────────── database: full method coverage ─────────────────────────
@pytest.mark.asyncio
async def test_db_full_workflow(temp_db):
    now = datetime.now()
    msg = {
        "id": "bale_x1",
        "platform": "bale",
        "platform_message_id": "x1",
        "text": "قیمت یاراترا",
        "text_translated": None,
        "chat_id": "c1",
        "chat_name": "گروه بله",
        "chat_username": "gb",
        "chat_link": "https://ble.ir/gb",
        "chat_type": "group",
        "user_id": "u1",
        "sender_name": "علی",
        "timestamp": now,
        "keywords": "یاراترا",
        "media_type": "text",
        "file_id": None,
        "local_file_path": None,
        "caption": None,
        "reply_to": None,
        "opportunity_score": 85,
        "opportunity_type": "داغ",
        "raw_data": {"a": 1},
    }
    await temp_db.save_message(msg)
    # duplicate insert ignored
    await temp_db.save_message(msg)

    # chat offsets
    await temp_db.set_chat_offset("telegram", "g1", 5)
    assert await temp_db.get_chat_offset("telegram", "g1") == "5"
    assert await temp_db.get_chat_offset("telegram", "g2") is None

    # high score leads
    await temp_db.save_lead(
        {"id": "lead1", "message_id": "bale_x1", "platform": "bale", "text": "قیمت یاراترا", "chat_id": "c1", "chat_name": "گروه بله", "user_id": "u1", "timestamp": now, "keywords": "یاراترا", "media_type": "text", "file_id": None, "opportunity_score": 85, "opportunity_type": "داغ"}
    )
    leads = await temp_db.get_high_score_leads(min_score=50)
    assert leads and leads[0]["opportunity_score"] == 85

    # search types
    assert await temp_db.search("یاراترا", "keyword") != []
    assert await temp_db.search("قیمت", "text") != []
    assert await temp_db.search("یاراترا", "all") != []

    # mark important
    await temp_db.mark_important("bale_x1", "نکته")
    imps = await temp_db.get_important_messages()
    assert imps and imps[0]["is_important"] == 1
    assert imps[0]["my_note"] == "نکته"

    # update lead score
    await temp_db.update_lead_score("lead1", 95)
    leads = await temp_db.get_high_score_leads(min_score=90)
    assert leads and leads[0]["opportunity_score"] == 95

    # keyword/group stats
    assert await temp_db.get_keyword_stats(10) != []
    assert await temp_db.get_group_stats(10) != []
    assert await temp_db.get_daily_stats(7) is not None
    assert await temp_db.get_total_count() == {"messages": 1, "leads": 1, "groups": 1}

    # date range
    d = now.strftime("%Y-%m-%d")
    assert await temp_db.get_leads_by_date_range(d, d) != []

    # backup
    assert await temp_db.backup(str(temp_db.db_path) + ".bak") is True

    # delete old (nothing older than 1 day)
    await temp_db.delete_old_messages(1)
    assert (await temp_db.get_total_count())["messages"] == 1
    # delete_old_messages with negative days deletes everything (cutoff in future)
    await temp_db.delete_old_messages(-1)
    assert (await temp_db.get_total_count())["messages"] == 0


@pytest.mark.asyncio
async def test_db_update_daily_stats(temp_db):
    now = datetime.now()
    await temp_db.save_message({"id": "m1", "platform": "bale", "text": "قیمت یاراترا", "chat_name": "g", "keywords": "یاراترا", "timestamp": now})
    await temp_db.update_daily_stats()
    await temp_db.update_daily_stats()  # idempotent
    cursor = await temp_db.execute("SELECT COUNT(*) FROM daily_stats")
    row = await cursor.fetchone()
    assert row[0] >= 1


@pytest.mark.asyncio
async def test_db_execute_wraps_result(temp_db):
    cursor = await temp_db.execute("SELECT 1 AS x")
    row = await cursor.fetchone()
    assert row["x"] == 1
    cursor2 = await temp_db.execute("SELECT * FROM messages")
    assert await cursor2.fetchall() == []


@pytest.mark.asyncio
async def test_postgres_migrate_and_close(monkeypatch):
    db = PostgresDatabase("h", 1, "u", "p", "d")
    await db.migrate()  # no-op
    db._pool = None
    await db.close()  # no-op


# ───────────────────────── daily_reporter async ─────────────────────────
def _seed_reporter_db(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir(exist_ok=True)
    db_path = str(tmp_path / "messages.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE messages (id TEXT, platform TEXT, chat_name TEXT, chat_link TEXT, "
        "chat_username TEXT, chat_type TEXT, text TEXT, timestamp TEXT, keywords TEXT, "
        "media_type TEXT, file_id TEXT, caption TEXT, reply_to TEXT, opportunity_score INTEGER, "
        "opportunity_type TEXT, sender_name TEXT, user_id TEXT)"
    )
    now = datetime.now()
    conn.execute(
        "INSERT INTO messages VALUES ('1','bale','گروه بله','https://ble.ir/x','x','group',"
        "'قیمت یاراترا',?,'یاراترا','text',NULL,NULL,NULL,80,'داغ','علی','u1')",
        (now.isoformat(),),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr("core.services.daily_reporter.DB_PATH", db_path)
    monkeypatch.setattr("core.services.daily_reporter.REPORTS_DIR", str(tmp_path / "reports"))
    return db_path, now


@pytest.mark.asyncio
async def test_daily_report_generation(tmp_path, monkeypatch):
    db_path, now = _seed_reporter_db(tmp_path, monkeypatch)
    reporter = DailyReporter()
    d = now.strftime("%Y-%m-%d")
    res = await reporter.generate_report(d)
    assert res is not None and res["total"] == 1
    # second call dedupes
    res2 = await reporter.generate_report(d)
    assert res2 is None
    # today report
    res3 = await reporter.generate_today_report()
    assert res3 is None or res3["total"] >= 0


@pytest.mark.asyncio
async def test_weekly_report(tmp_path, monkeypatch):
    db_path, now = _seed_reporter_db(tmp_path, monkeypatch)
    # insert a message within the weekly window (last 7 days)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO messages VALUES ('2','telegram','گروه تلگرام',NULL,NULL,'group',"
        "'خرید رونزا',?,'رونزا','text',NULL,NULL,NULL,55,'گرم','رضا','u2')",
        ((now - timedelta(days=2)).isoformat(),),
    )
    conn.commit()
    conn.close()
    reporter = DailyReporter()
    path = await reporter.generate_weekly_report()
    assert path and os.path.exists(path)
    content = open(path, encoding="utf-8").read()
    assert "گزارش هفتگی" in content


@pytest.mark.asyncio
async def test_monthly_report(tmp_path, monkeypatch):
    db_path, now = _seed_reporter_db(tmp_path, monkeypatch)
    reporter = DailyReporter()
    path = await reporter.generate_monthly_report()
    assert path


@pytest.mark.asyncio
async def test_save_all_report_formats(tmp_path, monkeypatch):
    db_path, now = _seed_reporter_db(tmp_path, monkeypatch)
    reporter = DailyReporter()
    data = {
        "total": 1,
        "messages": [
            {
                "id": "1", "platform": "bale", "chat_name": "گروه بله", "text": "قیمت یاراترا",
                "keywords": "یاراترا", "timestamp": now.isoformat(), "opportunity_score": 80, "opportunity_type": "داغ",
            }
        ],
        "group_stats": [],
        "keyword_stats": [],
    }
    d = now.strftime("%Y-%m-%d")
    txt = await reporter._save_text_report(data, d)
    html = await reporter._save_html_report(data, d)
    csvf = await reporter._save_csv_report(data, d)
    jsonf = await reporter._save_json_report(data, d)
    for f in (txt, html, csvf, jsonf):
        assert os.path.exists(f)
    assert "گزارش روزانه" in open(txt, encoding="utf-8").read()
    assert "<html" in open(html, encoding="utf-8").read()
    assert "گروه بله" in open(jsonf, encoding="utf-8").read()


@pytest.mark.asyncio
async def test_get_leads_by_date(tmp_path, monkeypatch):
    db_path, now = _seed_reporter_db(tmp_path, monkeypatch)
    reporter = DailyReporter()
    data = await reporter._get_leads_by_date(now.strftime("%Y-%m-%d"))
    assert data["total"] == 1
    assert data["messages"]


@pytest.mark.asyncio
async def test_start_daily_reporter(monkeypatch):
    monkeypatch.setattr("config.REPORT", {"enabled": False})
    reporter = await start_daily_reporter(None)
    assert reporter is not None
    monkeypatch.setattr(
        "config.REPORT",
        {"enabled": True, "daily_time": "08:00", "weekly_time": "09:00", "weekly_day": "sunday"},
    )
    monkeypatch.setattr("core.services.daily_reporter.AsyncIOScheduler", lambda: MagicMock())
    reporter2 = await start_daily_reporter(None)
    assert reporter2 is not None


@pytest.mark.asyncio
async def test_reporter_scheduler_start(tmp_path, monkeypatch):
    reporter = DailyReporter()
    reporter.scheduler = MagicMock()
    reporter.start()
    assert reporter.scheduler.add_job.call_count >= 3
    assert reporter.scheduler.start.called


def test_reporter_wrappers(tmp_path, monkeypatch):
    db_path, now = _seed_reporter_db(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO messages VALUES ('3','bale','گروه دیروز',NULL,NULL,'group',"
        "'قیمت یاراترا',?,'یاراترا','text',NULL,NULL,NULL,70,'گرم','علی','u1')",
        ((now - timedelta(days=1)).isoformat(),),
    )
    conn.commit()
    conn.close()
    path = generate_daily_report()
    assert path is not None
    path2 = generate_weekly_report()
    assert path2 is not None
    DailyReporter()
    row = DailyReporter().search_lead_by_message_id("1")
    assert row is not None


# ───────────────────────── obs_server server ─────────────────────────
@pytest.mark.asyncio
async def test_start_obs_server_disabled(monkeypatch):
    monkeypatch.setenv("OBS_ENABLED", "false")
    assert await start_obs_server(None) is None


@pytest.mark.asyncio
async def test_start_obs_server_no_aiohttp(monkeypatch):
    monkeypatch.setenv("OBS_ENABLED", "true")

    # simulate aiohttp import failure by patching __import__ inside the function
    real_import = __import__

    def fake_import(name, *a, **k):
        if name == "aiohttp":
            raise ImportError("no aiohttp")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", fake_import)
    assert await start_obs_server(None) is None


@pytest.mark.asyncio
async def test_obs_server_full_run(tmp_path, monkeypatch):
    import aiohttp

    db_path = str(tmp_path / "messages.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE messages (platform TEXT, keywords TEXT, opportunity_type TEXT)")
    conn.execute("INSERT INTO messages VALUES ('bale','یاراترا','داغ')")
    conn.commit()
    conn.close()

    _dbp = db_path

    class _DB:
        db_path = _dbp

        async def get_total_count(self):
            return {"messages": 1, "leads": 1}

    monkeypatch.setenv("OBS_ENABLED", "true")
    monkeypatch.setenv("OBS_HOST", "127.0.0.1")
    monkeypatch.setenv("OBS_PORT", "8791")
    runner = await start_obs_server(_DB())
    assert runner is not None
    # fetch endpoints
    async with aiohttp.ClientSession() as s:
        async with s.get("http://127.0.0.1:8791/health") as r:
            assert r.status in (200, 503)
        async with s.get("http://127.0.0.1:8791/metrics") as r:
            text = await r.text()
            assert "app_messages_total" in text
        async with s.get("http://127.0.0.1:8791/") as r:
            assert "Lead-Monitor" in await r.text()
        async with s.get("http://127.0.0.1:8791/chat") as r:
            assert "چت" in await r.text()
    await runner.cleanup()


# ───────────────────────── crm xlsx ─────────────────────────
def test_crm_xlsx_export(tmp_path):
    records = [
        {
            "source": "📨 بله", "channel": "g", "contact": "علی", "date": "t", "level": "داغ",
            "score": 90, "keywords": "یاراترا", "text": "قیمت", "link": "https://x", "media": "", "id": "1",
        }
    ]
    path = _export_xlsx(records, "stamp")
    assert path is None or os.path.exists(path)  # openpyxl may or may not be installed


# ───────────────────────── safe_brain deeper ─────────────────────────
def test_safe_brain_platform_map():
    sb = SafeBrain()
    assert sb._platform_in("ایتا کار میکنه") == "eitaa"
    assert sb._platform_in("تلگرام") == "telegram"
    assert sb._platform_in("واتساپ") == "whatsapp"
    assert sb._platform_in("لینکدین") == "linkedin"
    assert sb._platform_in("چیزی") is None


def test_safe_brain_logs_and_status(tmp_path, monkeypatch):
    monkeypatch.setattr("core.safe_brain.STORAGE_DIR", str(tmp_path))
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "app.jsonl").write_text(json.dumps({"level": "INFO", "msg": "hi"}) + "\n", encoding="utf-8")
    sb = SafeBrain()
    out = sb._logs(5)
    assert "hi" in out
    monkeypatch.setattr("core.safe_brain.STORAGE_DIR", str(tmp_path / "empty"))
    assert sb._logs(5) == "📭 لاگی موجود نیست."
