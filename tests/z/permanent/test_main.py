"""تست‌های core/brain/main.py — کلاس Brain (ساختاری)."""

from __future__ import annotations

import inspect

import pytest

from core.brain.main import Brain, get_brain, reset_brain


class TestBrainClass:
    def test_brain_is_class(self):
        assert inspect.isclass(Brain)

    def test_brain_has_mixins(self):
        # Brain باید از همه mixinها ارث ببره
        mro_names = [c.__name__ for c in Brain.__mro__]
        for mixin in ["FilesMixin", "SystemMixin", "InfoMixin",
                      "ReportsMixin", "AgentMixin", "PlatformMixin"]:
            assert mixin in mro_names, f"missing mixin: {mixin}"

    def test_brain_has_agent_method(self):
        assert hasattr(Brain, "agent")

    def test_brain_has_tool_map(self):
        assert hasattr(Brain, "TOOL_MAP")

    def test_brain_init_signature(self):
        sig = inspect.signature(Brain.__init__)
        params = list(sig.parameters.keys())
        assert "event_bus" in params
        assert "read_only" in params
        assert "full_access" in params


class TestSingleton:
    def test_get_brain_returns_instance(self):
        reset_brain()
        b = get_brain()
        assert isinstance(b, Brain)

    def test_get_brain_singleton(self):
        reset_brain()
        a = get_brain()
        b = get_brain()
        assert a is b

    def test_reset_brain(self):
        reset_brain()
        a = get_brain()
        reset_brain()
        b = get_brain()
        assert a is not b


class TestBuildToolMap:
    def test_build_tool_map_exists(self):
        from core.brain.main import _build_tool_map
        assert callable(_build_tool_map)

    def test_build_tool_map_returns_dict(self):
        from core.brain.main import _build_tool_map
        result = _build_tool_map()
        assert isinstance(result, dict)
        assert len(result) > 0
