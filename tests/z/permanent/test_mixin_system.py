"""تست‌های core/brain/mixin_system.py — ابزارهای سیستم.

⚠️ تمرکز روی باگ fix شده: log_info در backup_project.
"""

from __future__ import annotations

import inspect

from core.brain import mixin_system


class TestImports:
    def test_log_info_imported(self):
        """باگ قبلی: log_info استفاده می‌شد ولی import نبود."""
        assert hasattr(mixin_system, "log_info")
        assert callable(mixin_system.log_info)

    def test_log_error_imported(self):
        assert hasattr(mixin_system, "log_error")

    def test_log_success_imported(self):
        assert hasattr(mixin_system, "log_success")

    def test_log_warning_imported(self):
        assert hasattr(mixin_system, "log_warning")


class TestModuleStructure:
    def test_has_mixin_class(self):
        # باید حداقل یک کلاس Mixin داشته باشه
        classes = [
            name for name, obj in inspect.getmembers(mixin_system, inspect.isclass)
            if obj.__module__ == mixin_system.__name__
        ]
        assert len(classes) > 0

    def test_backup_method_exists(self):
        """متد backup_project باید وجود داشته باشه."""
        found = False
        for name, obj in inspect.getmembers(mixin_system, inspect.isclass):
            if obj.__module__ != mixin_system.__name__:
                continue
            if hasattr(obj, "_backup_project") or hasattr(obj, "backup_project"):
                found = True
                break
        assert found, "backup method not found"

    def test_backup_is_async(self):
        """backup باید async باشه (چون to_thread داره)."""
        for name, obj in inspect.getmembers(mixin_system, inspect.isclass):
            if obj.__module__ != mixin_system.__name__:
                continue
            for meth_name in ("_backup_project", "backup_project"):
                if hasattr(obj, meth_name):
                    meth = getattr(obj, meth_name)
                    assert inspect.iscoroutinefunction(meth), f"{meth_name} not async"
                    return
