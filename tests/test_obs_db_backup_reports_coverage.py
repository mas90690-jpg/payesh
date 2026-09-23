"""Coverage for obs_server, database helpers, backup_service, daily_reporter and crm_export (offline, tmp paths)."""
import asyncio
import csv
import os
import sqlite3
from datetime import datetime

import pytest

from core.database import (
    PostgresDatabase,
    SQLiteDatabase,
    _QueryResult,
    create_database,
)
from core.ops.obs_server import _gather_counts, _sys_stats, render_prometheus
from core.services.backup_service import BackupService
from core.services.crm_export import _record, _rows, export_crm
from core.services.daily_reporter import (
    DailyReporter,
    dedup_rows,
    generate_detailed_report,
    generate_executive_report,
    group_by_platform,
    platform_label,
    platform_rank,
    platform_summary_line,
    search_lead,
)


# ─────────── obs_server ───────────
def test_render_prometheus():
    text = render_prometheus(
        {"messages": 1, "leads": 2, "hot": 0, "by_platform": {"bale": 1}},
        {"cpu": 1, "ram": 2, "uptime": 3},
        {"bale": True},
    )
    assert 'platform="bale"' in text
    assert "system_cpu_percent 1" in text
    assert "app_uptime_seconds 3" in text


def test_sys_stats():
    stats = _sys_stats()
    assert "uptime" in stats
    assert stats["cpu"] >= 0


@pytest.mark.asyncio
async def test_gather_counts(tmp_path):
    db_path = str(tmp_path / "messages.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE messages (id TEXT, platform TEXT, keywords TEXT, opportunity_type TEXT)"
    )
    conn.execute("INSERT INTO messages VALUES ('1','bale','یاراترا','داغ')")
    conn.commit()
    conn.close()

    _p = db_path

    class _DB:
        db_path = _p

        async def get_total_count(self):
            return {"messages": 1, "leads": 1}

    counts = await _gather_counts(_DB())
    assert counts["messages"] == 1
    assert counts["hot"] == 1
    assert counts["by_platform"]["bale"] == 1


# ─────────── database helpers ───────────
def test_create_database_sqlite(monkeypatch):
    monkeypatch.setattr("core.database.DATABASE_TYPE", "sqlite")
    assert isinstance(create_database(), SQLiteDatabase)


def test_create_database_postgres(monkeypatch):
    monkeypatch.setattr("core.database.DATABASE_TYPE", "postgres")
    assert isinstance(create_database(), PostgresDatabase)


def test_query_result_iteration():
    q = _QueryResult([1, 2])

    async def _t():
        assert await q.fetchone() == 1
        assert await q.fetchone() == 2
        assert await q.fetchone() is None
        assert await _QueryResult([3]).fetchall() == [3]

    asyncio.run(_t())


def test_postgres_ts_helpers():
    now = datetime.now()
    assert PostgresDatabase._ts(now) == now
    assert isinstance(PostgresDatabase._ts("2025-01-01T00:00:00"), datetime)
    assert isinstance(PostgresDatabase._ts("bad"), datetime)
    assert PostgresDatabase._ts(None) is not None


@pytest.mark.asyncio
async def test_postgres_backup_no_pg_dump(monkeypatch):
    async def _boom(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr("core.database.asyncio.create_subprocess_exec", _boom)
    db = PostgresDatabase("h", 5432, "u", "p", "db")
    assert await db.backup("x.sql") is False


# ─────────── backup_service ───────────
@pytest.fixture
def backup_svc(tmp_path, monkeypatch):
    monkeypatch.setattr("core.services.backup_service.BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr("core.services.backup_service.DB_PATH", str(tmp_path / "messages.db"))
    svc = BackupService()
    svc.daily_dir = str(tmp_path / "daily")
    svc.full_dir = str(tmp_path / "full")
    svc.hash_file = str(tmp_path / "hashes.json")
    svc.last_backup_file = str(tmp_path / "last.txt")
    svc.last_full_backup_file = str(tmp_path / "last_full.txt")
    svc.stats_file = str(tmp_path / "stats.json")
    svc.init()
    return svc


def test_backup_init_and_stats(backup_svc):
    assert os.path.isdir(backup_svc.daily_dir)
    assert os.path.exists(backup_svc.hash_file)
    assert backup_svc.get_stats() == {}
    backup_svc._update_stats("daily", "p.zip", 10)
    assert backup_svc.get_stats()["daily"][0]["size"] == 10


def test_backup_code_files_excludes_storage(backup_svc, monkeypatch):
    monkeypatch.setattr(
        "core.services.backup_service.BASE_DIR",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    files = backup_svc._get_code_files()
    assert "main.py" in files
    assert not any(f.startswith(("venv", "storage", "data")) for f in files)


def test_backup_check_code_changes(backup_svc, tmp_path, monkeypatch):
    monkeypatch.setattr("core.services.backup_service.BASE_DIR", str(tmp_path))
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    changes = backup_svc.check_code_changes()
    assert any("جدید: a.py" in c for c in changes)
    assert backup_svc.check_code_changes() == []


def test_backup_should_mark_daily(backup_svc):
    assert backup_svc._should_backup_daily() is True
    backup_svc._mark_daily_done()
    assert backup_svc._should_backup_daily() is False
    assert backup_svc._should_backup_full() is True
    backup_svc._mark_full_done()
    assert backup_svc._should_backup_full() is False


def test_backup_cleanup_old(backup_svc, tmp_path, monkeypatch):
    for name in ("a.zip", "b.zip", "c.zip"):
        (tmp_path / "daily" / name).write_text("x")
    monkeypatch.setattr("core.services.backup_service.KEEP_DAILY_BACKUPS", 2)
    backup_svc._cleanup_old()
    remaining = [f for f in os.listdir(backup_svc.daily_dir) if f.endswith(".zip")]
    assert len(remaining) == 2


@pytest.mark.asyncio
async def test_backup_daily_invalid_db(backup_svc, tmp_path):
    (tmp_path / "messages.db").write_text("not a db", encoding="utf-8")
    assert await backup_svc.backup_daily() is None


def test_backup_is_db_valid(backup_svc, tmp_path):
    assert backup_svc._is_db_valid() is False
    conn = sqlite3.connect(str(tmp_path / "messages.db"))
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    assert backup_svc._is_db_valid() is True
    assert backup_svc._get_db_size() > 0


def test_backup_compress_folder(backup_svc, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "f.txt").write_text("hi", encoding="utf-8")
    assert backup_svc._compress_folder(str(src), str(tmp_path / "out.zip")) is True
    assert os.path.exists(tmp_path / "out.zip")


# ─────────── daily_reporter helpers ───────────
def test_platform_label_rank():
    assert "ایتا" in platform_label("eitaa")
    assert platform_label("unknown") == "📍 unknown"
    assert platform_rank("eitaa") < platform_rank("linkedin")
    assert platform_rank("zzz") > platform_rank("linkedin")


def test_group_by_platform_and_dedup():
    rows = [
        {"platform": "telegram", "chat_name": "g1", "text": "a"},
        {"platform": "bale", "chat_name": "g1", "text": "b"},
        {"platform": "telegram", "chat_name": "g1", "text": "a"},
    ]
    grouped = group_by_platform(rows)
    assert [p for p, _ in grouped] == ["bale", "telegram"]
    assert len(dedup_rows(rows)) == 2
    assert platform_summary_line(rows) != ""


def test_group_by_platform_tuple_rows():
    class _Row:
        def __init__(self, p):
            self._p = p

        def __getitem__(self, k):
            if k == "platform":
                return self._p
            raise KeyError(k)

    grouped = group_by_platform([_Row("bale"), _Row("telegram")])
    assert [p for p, _ in grouped] == ["bale", "telegram"]


def test_count_top():
    reporter = DailyReporter()
    items = [
        {"chat_name": "g1", "keywords": "یاراترا, رونزا"},
        {"chat_name": "g1", "keywords": "یاراترا"},
    ]
    assert reporter._count_top(items, "chat_name")[0][1] == 2
    assert reporter._count_top(items, "keywords")[0][0] == "یاراترا"
    assert reporter._count_top([{"chat_name": None, "keywords": ""}], "chat_name") == []


@pytest.fixture
def seeded_db(tmp_path):
    db_path = str(tmp_path / "messages.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE messages (id TEXT, platform TEXT, chat_name TEXT, chat_link TEXT, "
        "chat_username TEXT, chat_type TEXT, text TEXT, timestamp TEXT, keywords TEXT, "
        "media_type TEXT, file_id TEXT, caption TEXT, reply_to TEXT, opportunity_score INTEGER, "
        "opportunity_type TEXT, sender_name TEXT, user_id TEXT)"
    )
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO messages VALUES ('1','bale','گروه بله','https://ble.ir/x','x','group',"
        "'قیمت یاراترا',?, 'یاراترا','text',NULL,NULL,NULL,80,'داغ','علی','u1')",
        (now,),
    )
    conn.execute(
        "INSERT INTO messages VALUES ('2','telegram','گروه تلگرام',NULL,NULL,'group',"
        "'خرید رونزا',?, 'رونزا','photo','f1',NULL,NULL,55,'گرم','رضا','u2')",
        (now,),
    )
    conn.commit()
    conn.close()
    return db_path


def test_generate_executive_report(seeded_db, tmp_path, monkeypatch):
    def test_generate_executive_report(seeded_db, tmp_path, monkeypatch):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        monkeypatch.setattr("core.services.daily_reporter.DB_PATH", seeded_db)
        monkeypatch.setattr("core.services.daily_reporter.REPORTS_DIR", str(reports_dir))
        path = generate_executive_report("2020-01-01", "2030-01-01")
        assert path and os.path.exists(path)
        content = open(path, encoding="utf-8").read()
        assert "2" in content


def test_generate_detailed_report(seeded_db, tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    monkeypatch.setattr("core.services.daily_reporter.DB_PATH", seeded_db)
    monkeypatch.setattr("core.services.daily_reporter.REPORTS_DIR", str(reports_dir))
    path = generate_detailed_report(platform="bale", limit=10)
    assert path and os.path.exists(path)
    assert reports_dir.exists()


def test_search_lead(seeded_db, monkeypatch, capsys):
    monkeypatch.setattr("core.services.daily_reporter.DB_PATH", seeded_db)
    row = search_lead("1", platform="bale")
    assert row is not None and row[1] == "bale"
    assert search_lead("nope") is None


# ─────────── crm_export ───────────
def test_crm_export(seeded_db, tmp_path, monkeypatch):
    monkeypatch.setattr("core.services.crm_export.DB_PATH", seeded_db)
    monkeypatch.setattr("core.services.crm_export.REPORTS_DIR", str(tmp_path / "reports"))
    result = export_crm(30)
    assert result and result["count"] == 2
    assert os.path.exists(result["csv"])
    assert os.path.exists(result["json"])
    with open(result["csv"], encoding="utf-8-sig") as f:
        lines = list(csv.reader(f))
    assert lines[0][0] == "منبع (پلتفرم)"
    assert len(lines) == 3


def test_crm_export_no_db(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("core.services.crm_export.DB_PATH", str(tmp_path / "none.db"))
    assert export_crm(30) is None


def test_crm_export_empty(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE messages (platform TEXT, chat_name TEXT, chat_link TEXT, sender_name TEXT, "
        "user_id TEXT, text TEXT, keywords TEXT, media_type TEXT, timestamp TEXT, id TEXT, "
        "opportunity_score INTEGER, opportunity_type TEXT)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr("core.services.crm_export.DB_PATH", db_path)
    assert export_crm(30) is None


def test_crm_record_fields():
    r = _record(
        {
            "platform": "bale",
            "chat_name": "g",
            "sender_name": "s",
            "user_id": "u",
            "timestamp": "t",
            "opportunity_type": "داغ",
            "opportunity_score": 90,
            "keywords": "k",
            "text": "hello",
            "chat_link": None,
            "media_type": "text",
            "id": "1",
        }
    )
    assert r["source"] == "📨 بله"
    assert r["media"] == ""


def test_crm_rows_dedup_and_sort(seeded_db, monkeypatch):
    monkeypatch.setattr("core.services.crm_export.DB_PATH", seeded_db)
    rows = _rows(30)
    assert [r["platform"] for r in rows] == ["bale", "telegram"]
