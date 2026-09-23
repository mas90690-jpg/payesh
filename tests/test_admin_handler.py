# tests/test_admin_handler.py
import json

import pytest

from analyzers.keyword_finder import get_keyword_finder
from handlers.admin_handler import AdminHandler


def test_rule_parse():
    h = AdminHandler(db=None)
    assert h._rule_parse("وضعیت ربات چطوره")[0] == "status"
    assert h._rule_parse("یه گزارش بساز")[0] == "report"
    assert h._rule_parse("لاگ‌ها رو بده")[0] == "logs"
    a, v = h._rule_parse("کلیدواژه «کود مایع» را اضافه کن")
    assert a == "add_keyword" and v == "کود مایع"
    a, v = h._rule_parse("آیا کلیدواژه «یاراترا» را داری؟")
    assert a == "has_keyword" and v == "یاراترا"
    a, v = h._rule_parse("لینکدین کار میکنه؟")
    assert a == "platform_status" and v == "linkedin"
    assert h._rule_parse("ری‌استارت کن")[0] == "restart"


@pytest.mark.asyncio
async def test_execute_status_and_keywords(temp_db, tmp_path, monkeypatch):
    h = AdminHandler(db=temp_db)
    h.db_path = temp_db.db_path

    # مغز هوشمند (Brain/API) نباید در تست شبکه را صدا بزند — مسیر سنتی و قطعی تست شود
    monkeypatch.setattr("handlers.admin_handler._brain_available", False)

    # status (tables exist, counts = 0)
    txt = await h._execute("status", None, "وضعیت")
    assert "پیام" in txt

    # add_keyword writes to a temp custom file (hot-reloaded by finder in real run)
    monkeypatch.setattr("handlers.admin_handler.CUSTOM_KW", str(tmp_path / "kw.json"))
    txt = await h._execute("add_keyword", "کودتستی", "...")
    assert "اضافه" in txt
    assert json.load(open(tmp_path / "kw.json", encoding="utf-8")) == ["کودتستی"]

    # has_keyword on a real product name
    prod = get_keyword_finder().keywords[0]
    txt = await h._execute("has_keyword", prod, "...")
    assert "بله" in txt


def test_rule_parse_feature_and_logs():
    h = AdminHandler(db=None)
    assert h._rule_parse("میخوام بتونی پیامک هم بفرستی")[0] == "feature_request"
    assert h._rule_parse("این لاگ یعنی چی؟")[0] == "explain_logs"
    assert h._rule_parse("لاگ‌ها رو نشون بده")[0] == "logs"  # بدون «یعنی/توضیح» = نمایش


@pytest.mark.asyncio
async def test_feature_request_logged(temp_db, tmp_path, monkeypatch):
    h = AdminHandler(db=temp_db)
    monkeypatch.setattr("handlers.admin_handler.REQUESTS", str(tmp_path / "req.jsonl"))
    txt = await h._execute(
        "feature_request", None, "میخوام بتونی به ایمیل هم گزارش بفرستی"
    )
    assert "ثبت" in txt
    line = open(tmp_path / "req.jsonl", encoding="utf-8").read()
    assert "ایمیل" in line


@pytest.mark.asyncio
async def test_non_admin_ignored(temp_db):
    """پیامِ کاربرِ غیرمجاز نباید پردازش/جواب داده شود."""
    h = AdminHandler(db=temp_db)
    h.admin_ids = {"999"}  # فقط کاربر ۹۹۹ مجاز است
    sent = []

    # rig the reply to capture
    async def fake_reply(raw, text):
        sent.append(text)

    h._reply = fake_reply

    class Ev:
        data = {
            "platform": "bale",
            "chat_type": "private",
            "user_id": "123",
            "text": "وضعیت",
        }

    await h.handle(Ev())
    assert sent == []  # کاربر ۱۲۳ مجاز نیست → هیچ جوابی
