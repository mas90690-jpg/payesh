"""تست‌های core/brain/mixin_info.py — ابزارهای اطلاعاتی (ساختاری)."""

from __future__ import annotations

import inspect

import pytest

from core.brain.mixin_info import InfoMixin


class TestMethodsExist:
    @pytest.mark.parametrize("method", [
        "_semantic_search", "_grep_search", "_query_database",
        "_browse_web", "_clipboard_get", "_zip_archive",
        "_web_search", "_generate_tests", "_code_review",
        "_fix_bug", "_plan_task", "_get_project_context",
        "_system_dashboard", "_consolidation_run",
        "_brain_status", "_proactive_diagnosis",
    ])
    def test_method_exists(self, method):
        assert hasattr(InfoMixin, method), f"missing: {method}"

    @pytest.mark.parametrize("method", [
        "_semantic_search", "_grep_search", "_query_database",
        "_browse_web", "_clipboard_get", "_web_search",
    ])
    def test_method_is_async(self, method):
        assert inspect.iscoroutinefunction(getattr(InfoMixin, method))


class TestIsWindows:
    def test_is_windows_returns_bool(self):
        assert isinstance(InfoMixin._is_windows(), bool)
