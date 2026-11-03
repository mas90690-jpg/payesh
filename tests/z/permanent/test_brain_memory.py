"""تست‌های core/brain/brain_memory.py — حافظه‌ها."""

from __future__ import annotations

import pytest

from core.brain import brain_memory as bm_mod
from core.brain.brain_memory import (
    LongTermMemory,
    ProceduralMemory,
    WorkingMemory,
    _sanitize_fts_query,
)


class TestSanitizeFtsQuery:
    def test_empty(self):
        assert _sanitize_fts_query("") == ""

    def test_none(self):
        assert _sanitize_fts_query(None) == ""

    def test_normal(self):
        result = _sanitize_fts_query("hello world")
        assert "hello" in result

    def test_special_chars_removed(self):
        result = _sanitize_fts_query('test" OR "1"="1')
        assert '"' not in result


class TestWorkingMemory:
    @pytest.fixture
    def wm(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bm_mod, "WORKING_FILE", tmp_path / "working.json")
        return WorkingMemory()

    def test_add_and_get_recent(self, wm):
        wm.add("q1", "a1")
        recent = wm.get_recent(5)
        assert len(recent) >= 1
        assert recent[-1]["q"] == "q1"

    def test_add_empty_ignored(self, wm):
        wm.add("", "")
        assert len(wm.items) == 0

    def test_get_by_key(self, wm):
        wm.add("question", "answer")
        result = wm.get_by_key("question")
        assert result is not None
        assert result["a"] == "answer"

    def test_get_by_key_missing(self, wm):
        assert wm.get_by_key("__nope__") is None

    def test_trim(self, wm):
        for i in range(20):
            wm.add(f"q{i}", f"a{i}")
        removed = wm.trim(keep=5)
        assert removed >= 0
        assert len(wm.items) <= 5

    def test_clear(self, wm):
        wm.add("q", "a")
        wm.clear()
        assert len(wm.items) == 0


class TestLongTermMemory:
    @pytest.fixture
    def ltm(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bm_mod, "LONG_TERM_DB", tmp_path / "ltm.db")
        return LongTermMemory()

    def test_add_and_get(self, ltm):
        ltm.add("cat", "key1", "value1")
        assert ltm.get("cat", "key1") == "value1"

    def test_get_missing(self, ltm):
        assert ltm.get("cat", "__nope__") is None

    def test_get_category(self, ltm):
        ltm.add("cat", "k1", "v1")
        ltm.add("cat", "k2", "v2")
        items = ltm.get_category("cat")
        assert len(items) >= 2

    def test_add_lesson(self, ltm):
        ltm.add_lesson("wrong", "correct")
        lessons = ltm.search_lessons("wrong")
        assert len(lessons) >= 1

    def test_get_stats(self, ltm):
        stats = ltm.get_stats()
        assert isinstance(stats, dict)


class TestProceduralMemory:
    @pytest.fixture
    def pm(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bm_mod, "PROCEDURAL_FILE", tmp_path / "proc.json")
        return ProceduralMemory()

    def test_mark_done_and_is_done(self, pm):
        pm.mark_done("task1")
        assert pm.is_done("task1") is True

    def test_is_done_unknown(self, pm):
        assert pm.is_done("__nope__") is False

    def test_add_pattern(self, pm):
        pm.add_pattern("pattern1")
        pm.add_pattern("pattern1")
        top = pm.get_top_patterns(5)
        assert any(p[0] == "pattern1" for p in top)
