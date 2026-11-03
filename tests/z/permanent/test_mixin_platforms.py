"""تست‌های core/brain/mixin_platforms.py — پلتفرم‌ها (ساختاری)."""

from __future__ import annotations

import inspect

import pytest

from core.brain.mixin_platforms import PlatformManager, PlatformMixin


class TestPlatformManager:
    def test_has_get_classmethod(self):
        assert hasattr(PlatformManager, "get")

    def test_has_register(self):
        assert hasattr(PlatformManager, "register")

    def test_has_unregister(self):
        assert hasattr(PlatformManager, "unregister")

    def test_has_list_active(self):
        assert hasattr(PlatformManager, "list_active")

    def test_has_close_all(self):
        assert hasattr(PlatformManager, "close_all")

    @pytest.mark.asyncio
    async def test_get_returns_instance(self):
        mgr = await PlatformManager.get()
        assert isinstance(mgr, PlatformManager)

    @pytest.mark.asyncio
    async def test_get_singleton(self):
        a = await PlatformManager.get()
        b = await PlatformManager.get()
        assert a is b

    @pytest.mark.asyncio
    async def test_list_active_empty(self):
        mgr = await PlatformManager.get()
        result = await mgr.list_active()
        assert isinstance(result, list)


class TestPlatformMixin:
    @pytest.mark.parametrize("method", [
        "_list_platforms", "_start_platform", "_stop_platform",
        "_platform_health", "_platform_diagnose", "_platform_run_once",
        "_diagnose_platform", "_run_platform_as_user",
        "_list_all_platforms", "_control_platform", "_control_stats",
    ])
    def test_method_exists(self, method):
        assert hasattr(PlatformMixin, method), f"missing: {method}"

    @pytest.mark.parametrize("method", [
        "_list_platforms", "_start_platform", "_stop_platform",
        "_platform_health", "_control_platform",
    ])
    def test_method_is_async(self, method):
        assert inspect.iscoroutinefunction(getattr(PlatformMixin, method))

    def test_load_collector_class_exists(self):
        assert hasattr(PlatformMixin, "_load_collector_class")
