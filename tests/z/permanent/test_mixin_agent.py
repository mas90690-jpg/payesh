"""تست‌های core/brain/mixin_agent.py — منطق agent (ساختاری + helper)."""

from __future__ import annotations

import inspect

import pytest

from core.brain.mixin_agent import AgentMixin


class TestMethodsExist:
    @pytest.mark.parametrize("method", [
        "set_event_bus", "set_database", "add_lesson",
        "_init_client", "_build_tools", "_execute_tool",
        "_think", "_select_model", "_build_system_prompt",
        "agent", "_offline_agent", "_auto_run_tests",
        "process_admin_command", "_handle_action",
        "build_from_map", "self_upgrade", "_plan_steps",
        "_inject_knowledge", "_reflect", "ask_multi",
        "get_client_stats",
    ])
    def test_method_exists(self, method):
        assert hasattr(AgentMixin, method), f"missing: {method}"

    @pytest.mark.parametrize("method", [
        "agent", "_think", "_execute_tool", "process_admin_command",
        "self_upgrade", "ask_multi",
    ])
    def test_method_is_async(self, method):
        assert inspect.iscoroutinefunction(getattr(AgentMixin, method))


class TestStaticHelpers:
    def test_is_complex_task_empty(self):
        assert AgentMixin._is_complex_task("") is False

    def test_is_complex_task_none(self):
        assert AgentMixin._is_complex_task(None) is False

    def test_is_complex_task_short(self):
        assert AgentMixin._is_complex_task("سلام") is False

    def test_is_complex_task_long(self):
        long_task = "این یک تسک بسیار طولانی است " * 20
        assert AgentMixin._is_complex_task(long_task) is True

    def test_build_retry_prompt_contains_task(self):
        result = AgentMixin._build_retry_prompt("my task", "my answer")
        assert "my task" in result

    def test_build_retry_prompt_contains_answer(self):
        result = AgentMixin._build_retry_prompt("t", "a")
        assert "a" in result


class TestShouldThink:
    def test_should_think_empty(self):
        assert AgentMixin._should_think("") is False

    def test_should_think_returns_bool(self):
        assert isinstance(AgentMixin._should_think("تست"), bool)

    def test_should_plan_returns_bool(self):
        assert isinstance(AgentMixin._should_plan("تست"), bool)
