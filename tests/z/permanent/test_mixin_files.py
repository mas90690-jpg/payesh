"""تست‌های core/brain/mixin_files.py — ابزارهای فایل (ساختاری + helper)."""

from __future__ import annotations

import inspect

import pytest

from core.brain import mixin_files
from core.brain.mixin_files import FilesMixin


class TestHelpers:
    def test_format_lines_basic(self):
        result = FilesMixin._format_lines("a\nb\nc")
        assert "a" in result
        assert "b" in result
        assert "c" in result

    def test_format_lines_start_line(self):
        result = FilesMixin._format_lines("a\nb", start_line=10)
        assert "10" in result

    def test_format_lines_empty(self):
        result = FilesMixin._format_lines("")
        assert isinstance(result, str)

    def test_is_safe_write_path_inside(self):
        # storage/reports مجاز است — instance method
        inst = FilesMixin()
        assert inst._is_safe_write_path("storage/reports/x.md") is True

    def test_is_safe_write_path_outside(self):
        inst = FilesMixin()
        assert inst._is_safe_write_path("../../etc/passwd") is False

    def test_is_locked_write_file_knowledge(self):
        # فایل‌های دانشی قفل هستند
        result = FilesMixin._is_locked_write_file("data/prompts/bot_knowledge.md")
        assert result is not None

    def test_is_locked_write_file_normal(self):
        assert FilesMixin._is_locked_write_file("storage/reports/x.md") is None


class TestMethodsExist:
    @pytest.mark.parametrize("method", [
        "_read_file", "_read_all", "_read_chunk", "_read_lines", "_read_range",
        "_file_info", "_file_exists", "_file_hash", "_find_files",
        "_write_file", "_edit_file", "_list_directory",
        "_delete_file", "_move_file", "_append_knowledge",
    ])
    def test_method_exists(self, method):
        assert hasattr(FilesMixin, method), f"missing: {method}"

    @pytest.mark.parametrize("method", [
        "_read_file", "_write_file", "_edit_file", "_delete_file",
    ])
    def test_method_is_async(self, method):
        assert inspect.iscoroutinefunction(getattr(FilesMixin, method))


class TestSafepath:
    def test_safepath_inside(self):
        # باید Path برگردونه
        from pathlib import Path
        result = FilesMixin._safepath(None, "storage/reports/x.md")
        assert isinstance(result, Path)

    def test_safepath_escape_raises(self):
        with pytest.raises(Exception):
            FilesMixin._safepath(None, "../../etc/passwd")
