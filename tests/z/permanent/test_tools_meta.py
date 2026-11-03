"""تست‌های core/brain/tools_meta.py — متادیتای ابزارها."""

from __future__ import annotations

from core.brain import tools_meta


class TestToolMeta:
    def test_allowed_tools_is_mapping(self):
        assert hasattr(tools_meta.ALLOWED_TOOLS, "get")

    def test_tool_definitions_is_list(self):
        assert isinstance(tools_meta.TOOL_DEFINITIONS, list)

    def test_direct_commands_has_greeting(self):
        assert tools_meta.ALLOWED_DIRECT_COMMANDS.get("سلام") == "greeting"

    def test_build_tool_definitions_returns_copy(self):
        a = tools_meta.build_tool_definitions()
        b = tools_meta.build_tool_definitions()
        assert a == b
        assert a is not b

    def test_get_tool_info_empty(self):
        assert tools_meta.get_tool_info("") is None

    def test_get_tool_info_unknown(self):
        assert tools_meta.get_tool_info("__nonexistent_tool__") is None

    def test_get_tools_by_risk_empty(self):
        assert tools_meta.get_tools_by_risk("") == []

    def test_get_tools_by_risk_invalid(self):
        assert tools_meta.get_tools_by_risk("critical") == []

    def test_get_tools_by_risk_valid(self):
        result = tools_meta.get_tools_by_risk("low")
        assert isinstance(result, list)

    def test_get_tool_count(self):
        assert tools_meta.get_tool_count() == len(tools_meta.ALLOWED_TOOLS)

    def test_get_stats_structure(self):
        stats = tools_meta.get_stats()
        assert "total_tools" in stats
        assert "by_risk" in stats
        assert "require_confirm" in stats
        assert set(stats["by_risk"].keys()) == {"low", "medium", "high"}
