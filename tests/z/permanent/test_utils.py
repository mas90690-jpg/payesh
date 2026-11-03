"""تست‌های core/brain/utils.py — توابع کمکی."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.brain.utils import (
    PersianBrainHandler,
    _deep_merge,
    _env_int,
    human_bytes,
    safe_path,
    sanitize_console_output,
)


# ═══════════════════════════════════════════
# human_bytes
# ═══════════════════════════════════════════
class TestHumanBytes:
    def test_zero(self):
        assert human_bytes(0) == "0.0 B"

    def test_bytes(self):
        assert human_bytes(512) == "512.0 B"

    def test_kilobytes(self):
        assert human_bytes(1024) == "1.0 KB"

    def test_megabytes(self):
        assert human_bytes(1024 * 1024) == "1.0 MB"

    def test_gigabytes(self):
        assert human_bytes(1024**3) == "1.0 GB"

    def test_negative(self):
        assert human_bytes(-1024) == "-1.0 KB"

    def test_invalid_returns_question(self):
        assert human_bytes("abc") == "?"

    def test_none_returns_question(self):
        assert human_bytes(None) == "?"


# ═══════════════════════════════════════════
# safe_path
# ═══════════════════════════════════════════
class TestSafePath:
    def test_empty_raises(self):
        with pytest.raises(ValueError):
            safe_path("")

    def test_none_raises(self):
        with pytest.raises(ValueError):
            safe_path(None)

    def test_relative_inside_base(self, tmp_path):
        result = safe_path("sub/file.txt", base=tmp_path)
        assert str(result).startswith(str(tmp_path.resolve()))

    def test_escape_blocked(self, tmp_path):
        with pytest.raises(ValueError):
            safe_path("../outside.txt", base=tmp_path)

    def test_absolute_inside_base(self, tmp_path):
        target = tmp_path / "a.txt"
        result = safe_path(str(target), base=tmp_path)
        assert result == target.resolve()


# ═══════════════════════════════════════════
# sanitize_console_output
# ═══════════════════════════════════════════
class TestSanitizeConsoleOutput:
    def test_empty(self):
        assert sanitize_console_output("") == ""

    def test_none(self):
        assert sanitize_console_output(None) == ""

    def test_ansi_removed(self):
        assert sanitize_console_output("\x1b[31mred\x1b[0m") == "red"

    def test_control_chars_removed(self):
        assert sanitize_console_output("a\x00b\x07c") == "abc"

    def test_normal_text_unchanged(self):
        assert sanitize_console_output("hello world") == "hello world"

    def test_non_str_converted(self):
        assert sanitize_console_output(123) == "123"


# ═══════════════════════════════════════════
# PersianBrainHandler
# ═══════════════════════════════════════════
class TestPersianBrainHandler:
    def test_fix_none(self):
        assert PersianBrainHandler.fix(None) == ""

    def test_fix_empty(self):
        assert PersianBrainHandler.fix("") == ""

    def test_fix_arabic_yeh(self):
        assert PersianBrainHandler.fix("يك") == "یک"

    def test_fix_arabic_kaf(self):
        assert PersianBrainHandler.fix("كتاب") == "کتاب"

    def test_fix_non_str(self):
        assert PersianBrainHandler.fix(123) == "123"

    def test_fix_removes_rtl_marks(self):
        assert PersianBrainHandler.fix("a\u200eb") == "ab"

    def test_setup_environment_idempotent(self):
        PersianBrainHandler.setup_environment()
        PersianBrainHandler.setup_environment()  # نباید خطا بده
        assert PersianBrainHandler._env_setup_done is True


# ═══════════════════════════════════════════
# _env_int
# ═══════════════════════════════════════════
class TestEnvInt:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("TEST_X", raising=False)
        assert _env_int("TEST_X", 42) == 42

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("TEST_X", "99")
        assert _env_int("TEST_X", 42) == 99

    def test_invalid_returns_default(self, monkeypatch):
        monkeypatch.setenv("TEST_X", "abc")
        assert _env_int("TEST_X", 42) == 42

    def test_min_clamp(self, monkeypatch):
        monkeypatch.setenv("TEST_X", "1")
        assert _env_int("TEST_X", 42, min_val=10) == 10

    def test_max_clamp(self, monkeypatch):
        monkeypatch.setenv("TEST_X", "999")
        assert _env_int("TEST_X", 42, max_val=100) == 100


# ═══════════════════════════════════════════
# _deep_merge
# ═══════════════════════════════════════════
class TestDeepMerge:
    def test_simple_override(self):
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_nested_merge(self):
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"b": 3}}
        assert _deep_merge(base, override) == {"x": {"a": 1, "b": 3}}

    def test_new_key_added(self):
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_no_mutation(self):
        base = {"x": {"a": 1}}
        _deep_merge(base, {"x": {"a": 2}})
        assert base == {"x": {"a": 1}}
