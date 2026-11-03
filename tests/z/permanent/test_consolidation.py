"""تست‌های core/brain/consolidation.py — خواب مغز."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.brain import consolidation


class TestConsolidationStats:
    def test_load_stats_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setattr(consolidation, "STATS_FILE", tmp_path / "nope.json")
        stats = consolidation._load_stats()
        assert stats["runs"] == 0
        assert stats["moved_total"] == 0
        assert stats["last_run"] is None

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        f = tmp_path / "stats.json"
        monkeypatch.setattr(consolidation, "STATS_FILE", f)
        consolidation._save_stats({"runs": 5, "moved_total": 10, "last_run": None, "started_at": None})
        loaded = consolidation._load_stats()
        assert loaded["runs"] == 5
        assert loaded["moved_total"] == 10

    def test_load_stats_corrupt_json(self, tmp_path, monkeypatch):
        f = tmp_path / "bad.json"
        f.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(consolidation, "STATS_FILE", f)
        stats = consolidation._load_stats()
        assert stats["runs"] == 0

    def test_get_stats_returns_dict(self, tmp_path, monkeypatch):
        monkeypatch.setattr(consolidation, "STATS_FILE", tmp_path / "x.json")
        assert isinstance(consolidation.get_stats(), dict)


class TestRunOnce:
    @pytest.mark.asyncio
    async def test_run_once_moves_important(self, tmp_path, monkeypatch):
        monkeypatch.setattr(consolidation, "STATS_FILE", tmp_path / "s.json")

        fake_bm = MagicMock()
        fake_bm.working.items = [
            {"q": "q1", "a": "a1", "imp": 0.9},
            {"q": "q2", "a": "a2", "imp": 0.3},
        ]
        fake_bm.working.trim.return_value = 0
        fake_bm.long_term.add = MagicMock()

        fake_pm = MagicMock()
        fake_pm.enabled = False

        with patch("core.brain.brain_memory.get_brain_memory", return_value=fake_bm), \
             patch("core.brain.brain_memory.get_persistent_memory", return_value=fake_pm):
            result = await consolidation.run_once()

        assert result["moved"] == 1
        assert result["working_before"] == 2
        fake_bm.long_term.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_once_handles_add_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(consolidation, "STATS_FILE", tmp_path / "s.json")

        fake_bm = MagicMock()
        fake_bm.working.items = [{"q": "q", "a": "a", "imp": 0.9}]
        fake_bm.working.trim.return_value = 0
        fake_bm.long_term.add.side_effect = RuntimeError("boom")

        fake_pm = MagicMock()
        fake_pm.enabled = False

        with patch("core.brain.brain_memory.get_brain_memory", return_value=fake_bm), \
             patch("core.brain.brain_memory.get_persistent_memory", return_value=fake_pm):
            result = await consolidation.run_once()

        assert result["moved"] == 0  # خطا خورد، ولی crash نکرد


class TestLoopControl:
    def test_stop_without_start(self):
        consolidation.stop_consolidation()  # نباید خطا بده

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        task = consolidation.start_consolidation()
        assert task is not None
        consolidation.stop_consolidation()
