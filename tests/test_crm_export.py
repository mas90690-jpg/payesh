# tests/test_crm_export.py
import os
from datetime import datetime

import pytest

import core.services.crm_export as crm


@pytest.mark.asyncio
async def test_crm_export_creates_files(temp_db, tmp_path, monkeypatch):
    """خروجیِ CRM فایل می‌سازد و سرنخِ امتیازدار را شامل می‌شود."""
    await temp_db.save_message(
        {
            "id": "telegram_1",
            "platform": "telegram",
            "text": "قیمت یاراترا فوری",
            "chat_name": "گروه فروش",
            "chat_link": "https://t.me/x",
            "keywords": "یاراترا",
            "timestamp": datetime.now(),
            "opportunity_score": 70,
            "opportunity_type": "داغ",
        }
    )
    # دیتابیس و پوشهٔ خروجی را به مسیرهای تست هدایت کن
    monkeypatch.setattr(crm, "DB_PATH", temp_db.db_path)
    monkeypatch.setattr(crm, "REPORTS_DIR", str(tmp_path))

    res = crm.export_crm(0)  # همهٔ سرنخ‌ها
    assert res and res["count"] >= 1
    assert os.path.exists(res["csv"])
    assert os.path.exists(res["json"])
    # محتوای CSV شاملِ سطحِ فرصت و امتیاز باشد
    with open(res["csv"], encoding="utf-8-sig") as f:
        content = f.read()
    assert "داغ" in content and "یاراترا" in content
