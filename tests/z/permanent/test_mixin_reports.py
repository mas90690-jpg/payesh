"""تست‌های core/brain/mixin_reports.py — گزارش‌ها (ساختاری)."""

from __future__ import annotations

import inspect

import pytest

from core.brain.mixin_reports import ReportsMixin


class TestMethodsExist:
    @pytest.mark.parametrize("method", [
        "_get_setting", "_set_setting", "_toggle_platform",
        "_platform_status", "_change_poll_interval", "_get_access_summary",
        "get_status", "get_help", "get_stats", "set_mode",
        "_get_health_report", "_get_keywords_report", "_get_platform_status",
        "_get_report", "_get_recent_logs", "_get_lead_details",
        "_check_keyword", "generate_report", "analyze_logs",
    ])
    def test_method_exists(self, method):
        assert hasattr(ReportsMixin, method), f"missing: {method}"

    @pytest.mark.parametrize("method", [
        "_get_setting", "_set_setting", "_toggle_platform",
        "_platform_status", "_get_health_report",
    ])
    def test_method_is_async(self, method):
        assert inspect.iscoroutinefunction(getattr(ReportsMixin, method))

    @pytest.mark.parametrize("method", [
        "get_status", "get_help", "get_stats", "generate_report",
    ])
    def test_sync_methods(self, method):
        assert not inspect.iscoroutinefunction(getattr(ReportsMixin, method))
