"""تست‌های core/brain/experience_learner.py — یادگیری از تجربه."""

from __future__ import annotations

import pytest

from core.brain import experience_learner as el_mod
from core.brain.experience_learner import ExperienceLearner


@pytest.fixture
def learner(tmp_path, monkeypatch):
    db = tmp_path / "exp.db"
    monkeypatch.setattr(el_mod, "DB_FILE", db)
    monkeypatch.setattr(el_mod, "BASE_DIR", tmp_path)
    return ExperienceLearner()


class TestRecord:
    def test_record_success(self, learner):
        learner.record("bale", "login", "https://x", success=True)
        assert learner.get_best("bale", "login") == "https://x"

    def test_record_empty_platform_ignored(self, learner):
        learner.record("", "login", "x", success=True)
        assert learner.get_best("", "login") is None

    def test_record_empty_action_ignored(self, learner):
        learner.record("bale", "", "x", success=True)
        assert learner.get_best("bale", "") is None

    def test_record_empty_value_ignored(self, learner):
        learner.record("bale", "login", "", success=True)
        assert learner.get_best("bale", "login") is None

    def test_record_increments_count(self, learner):
        learner.record("bale", "login", "x", success=True)
        learner.record("bale", "login", "x", success=True)
        successes = learner.get_successes("bale", "login")
        assert successes[0][1] == 2

    def test_record_case_insensitive(self, learner):
        learner.record("BALE", "LOGIN", "x", success=True)
        assert learner.get_best("bale", "login") == "x"


class TestGetBest:
    def test_get_best_none_when_empty(self, learner):
        assert learner.get_best("bale", "login") is None

    def test_get_best_empty_args(self, learner):
        assert learner.get_best("", "") is None

    def test_get_best_returns_highest_count(self, learner):
        learner.record("bale", "login", "a", success=True)
        learner.record("bale", "login", "b", success=True)
        learner.record("bale", "login", "b", success=True)
        assert learner.get_best("bale", "login") == "b"


class TestFailures:
    def test_is_known_failure_true(self, learner):
        learner.record("bale", "login", "bad", success=False)
        assert learner.is_known_failure("bale", "login", "bad") is True

    def test_is_known_failure_false(self, learner):
        assert learner.is_known_failure("bale", "login", "unknown") is False

    def test_get_failures(self, learner):
        learner.record("bale", "login", "bad1", success=False)
        learner.record("bale", "login", "bad2", success=False)
        failures = learner.get_failures("bale", "login")
        assert "bad1" in failures
        assert "bad2" in failures


class TestNotes:
    def test_add_and_get_note(self, learner):
        learner.add_note("bale", "test note")
        notes = learner.get_notes("bale")
        assert "test note" in notes

    def test_get_notes_by_category(self, learner):
        learner.add_note("bale", "n1", category="cat1")
        learner.add_note("bale", "n2", category="cat2")
        assert "n1" in learner.get_notes("bale", category="cat1")
        assert "n2" not in learner.get_notes("bale", category="cat1")

    def test_add_note_empty_ignored(self, learner):
        learner.add_note("", "note")
        learner.add_note("bale", "")
        assert learner.get_notes("bale") == []


class TestStats:
    def test_get_stats_structure(self, learner):
        learner.record("bale", "login", "x", success=True)
        stats = learner.get_stats()
        assert isinstance(stats, dict)

    def test_export_json(self, learner, tmp_path):
        learner.record("bale", "login", "x", success=True)
        out = tmp_path / "export.json"
        result = learner.export_json(str(out))
        assert out.exists()
        assert isinstance(result, str)

    def test_build_context(self, learner):
        learner.record("bale", "login", "x", success=True)
        ctx = learner.build_context("bale")
        assert isinstance(ctx, str)
