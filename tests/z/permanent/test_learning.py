"""تست‌های core/brain/learning.py — توابع کمکی و ساختاری."""

from __future__ import annotations

import inspect
import json

import pytest

from core.brain.learning import (
    CodeReviewer,
    LearningEngine,
    SelfHealer,
    TaskPlanner,
    WebSearch,
    _atomic_write_json,
    _clean_read_output,
)
from core.brain.learning import TestGenerator as _TG


class TestAtomicWriteJson:
    def test_write_and_read(self, tmp_path):
        f = tmp_path / "test.json"
        assert _atomic_write_json(f, {"a": 1}) is True
        assert json.loads(f.read_text(encoding="utf-8")) == {"a": 1}

    def test_creates_parent_dir(self, tmp_path):
        f = tmp_path / "sub" / "deep" / "test.json"
        assert _atomic_write_json(f, {"x": 2}) is True
        assert f.exists()

    def test_overwrites_existing(self, tmp_path):
        f = tmp_path / "test.json"
        _atomic_write_json(f, {"a": 1})
        _atomic_write_json(f, {"b": 2})
        assert json.loads(f.read_text(encoding="utf-8")) == {"b": 2}

    def test_invalid_path_returns_false(self):
        # مسیر None — نباید crash کنه (باگ fix شده)
        assert _atomic_write_json(None, {}) is False


class TestCleanReadOutput:
    def test_empty(self):
        assert _clean_read_output("") == ""

    def test_none(self):
        assert _clean_read_output(None) is None

    def test_removes_brain_header(self):
        text = "📄 BRAIN:file:read\nactual content"
        result = _clean_read_output(text)
        assert "actual content" in result
        assert "BRAIN:file:read" not in result

    def test_keeps_normal_text(self):
        assert _clean_read_output("normal text") == "normal text"


class TestClassesExist:
    @pytest.mark.parametrize("cls", [
        LearningEngine, TaskPlanner, SelfHealer,
        _TG, WebSearch, CodeReviewer,
    ])
    def test_class_exists(self, cls):
        assert inspect.isclass(cls)

    def test_learning_engine_methods(self):
        for m in ["record_interaction", "suggest_tool_for_task", "to_memory_store"]:
            assert hasattr(LearningEngine, m)

    def test_task_planner_methods(self):
        for m in ["plan_task", "execute_plan", "load", "save"]:
            assert hasattr(TaskPlanner, m)

    def test_self_healer_methods(self):
        assert hasattr(SelfHealer, "fix_bug")

    def test_test_generator_methods(self):
        assert hasattr(_TG, "generate_tests")

    def test_web_search_methods(self):
        assert hasattr(WebSearch, "search")

    def test_code_reviewer_methods(self):
        assert hasattr(CodeReviewer, "review") or hasattr(CodeReviewer, "review_code")

    def test_plan_task_is_async(self):
        assert inspect.iscoroutinefunction(TaskPlanner.plan_task)

    def test_fix_bug_is_async(self):
        assert inspect.iscoroutinefunction(SelfHealer.fix_bug)
