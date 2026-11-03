"""تست‌های core/brain/tool_router.py — انتخاب ابزار."""

from __future__ import annotations

from core.brain.tool_router import ToolRouter, get_tool_router


class TestToolRouter:
    def test_empty_question(self):
        assert ToolRouter.route("") == []

    def test_none_question(self):
        assert ToolRouter.route(None) == []

    def test_no_match(self):
        assert ToolRouter.route("__zzz_no_keyword_zzz__") == []

    def test_max_five_results(self):
        # هر سؤالی که match کنه نباید بیش از ۵ ابزار بده
        result = ToolRouter.route("فایل بخون و بنویس و تست کن و audit کن")
        assert len(result) <= 5

    def test_build_hint_empty(self):
        assert ToolRouter.build_hint("__zzz__") == ""

    def test_build_hint_has_header(self):
        # اگه match شد، header باید باشه
        result = ToolRouter.build_hint("فایل")
        if result:
            assert "راهنمای ابزار" in result

    def test_get_tool_router_returns_class(self):
        assert get_tool_router() is ToolRouter

    def test_routes_is_dict(self):
        assert isinstance(ToolRouter.ROUTES, dict)
