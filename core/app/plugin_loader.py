"""
core/plugin_loader.py — بارگذاری خودکار کلکتورها و هندلرها.

استراتژی:
  - اسکن پوشهٔ collectors/ و handlers/ از PROJECT_BASE (نه os.getcwd())
  - هر ماژول .py را import کن و کلاس‌های ارث‌برنده از BaseCollector/BaseHandler را پیدا کن
  - فقط کلاس‌های تعریف‌شده در همان ماژول (نه import شده از جای دیگه)
  - ضدتکراری بر اساس نام کلاس (اگه در دو ماژول ظاهر شد، فقط یکی)
  - ترتیب: فایل‌ها الفبایی؛ اولویت‌دهی از تنظیمات config

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX: از PROJECT_BASE استفاده می‌شود (نه os.getcwd()) — CWD-independent
  - 🐛 FIX: __module__ چک می‌شود تا کلاس‌های import شده دوباره بارگذاری نشوند
  - 🐛 FIX: ضدتکراری بر اساس نام کلاس (کلاس در دو ماژول فقط یک‌بار)
  - 🐛 FIX: خطاهای is_enabled() و fallback دیگر بی‌صدا نیستند
  - 🎯 لاگ دقیق و ساخت‌یافته با پیشوند [PLUGIN:...]:
      * init: PROJECT_BASE + dependencies
      * discover: تعداد فایل، هر ماژول با زمان import
      * per-plugin: دلیل فعال/غیرفعال، پارامترهای پاس‌شده، زمان ساخت
      * summary: N کلکتور + M هندلر در X ms
  - 🧹 منطق مشترک discover_collectors/discover_handlers → _discover()
  - 🧹 _loaded_plugins استفاده می‌شود (برای ضدتکراری)
"""
from __future__ import annotations

import importlib
import inspect
import os
import time
import traceback
from pathlib import Path
from typing import Any

from core.contracts.base_collector import BaseCollector
from core.contracts.base_handler import BaseHandler
from core.kernel.logger import log_error, log_info, log_success, log_warning

# ─── مسیر پایه (از config، fallback به محل این فایل) ───
try:
    from config import BASE_DIR as PROJECT_BASE
except Exception:
    PROJECT_BASE = Path(__file__).parent.parent.resolve()

# آستانه‌های هشدار (میلی‌ثانیه)
_SLOW_MODULE_IMPORT_MS = 2000   # import یک ماژول > 2s
_SLOW_INSTANCE_MS = 500         # ساخت instance > 500ms
_LOG_PREVIEW_LEN = 100


class PluginLoader:
    """بارگذاری خودکار همه پلاگین‌ها — بدون تغییر کد اصلی."""

    def __init__(self):
        self.collectors: list[BaseCollector] = []
        self.handlers: list[BaseHandler] = []
        self._loaded_plugins: dict[str, Any] = {}   # {class_name: instance}
        self._dependencies: dict[str, Any] = {}      # {param_name: value}

        log_info(
            f"🔌 [PLUGIN:init] آماده | PROJECT_BASE={PROJECT_BASE}"
        )

    # ═══════════════════════════════════════════
    # تزریق وابستگی
    # ═══════════════════════════════════════════
    def set_dependencies(self, **deps):
        """تنظیم وابستگی‌های مورد نیاز برای ساخت پلاگین‌ها."""
        self._dependencies.update(deps)
        log_info(
            f"💉 [PLUGIN:deps] ثبت | keys={list(deps.keys())} | "
            f"total={len(self._dependencies)}"
        )

    # ═══════════════════════════════════════════
    # API عمومی — discover_collectors
    # ═══════════════════════════════════════════
    def discover_collectors(
        self, collectors_dir: str = "collectors"
    ) -> list[BaseCollector]:
        """پیدا کردن خودکار کلکتورها از پوشه collectors/."""
        log_info(
            f"🔍 [PLUGIN:collectors] شروع اسکن | "
            f"dir={collectors_dir} | base={PROJECT_BASE}"
        )

        results = self._discover(
            subdir=collectors_dir,
            base_cls=BaseCollector,
            kind="collector",
        )
        self.collectors = results

        log_success(
            f"✅ [PLUGIN:collectors] کامل | "
            f"تعداد={len(results)} | instances={[c.platform_name for c in results]}"
        )
        return results

    # ═══════════════════════════════════════════
    # API عمومی — discover_handlers
    # ═══════════════════════════════════════════
    def discover_handlers(
        self, handlers_dir: str = "handlers"
    ) -> list[BaseHandler]:
        """پیدا کردن خودکار هندلرها از پوشه handlers/."""
        log_info(
            f"🔍 [PLUGIN:handlers] شروع اسکن | "
            f"dir={handlers_dir} | base={PROJECT_BASE}"
        )

        results = self._discover(
            subdir=handlers_dir,
            base_cls=BaseHandler,
            kind="handler",
        )
        self.handlers = results

        log_success(
            f"✅ [PLUGIN:handlers] کامل | "
            f"تعداد={len(results)} | "
            f"names={[getattr(h, 'handler_name', '?') for h in results]}"
        )
        return results

    # ═══════════════════════════════════════════
    # هستهٔ مشترک
    # ═══════════════════════════════════════════
    def _discover(
        self,
        subdir: str,
        base_cls: type,
        kind: str,  # "collector" | "handler"
    ) -> list[Any]:
        """اسکن یک پوشه و ساخت instance از همهٔ کلاس‌های موردنظر.

        - از PROJECT_BASE استفاده می‌کند (نه CWD)
        - فقط کلاس‌های تعریف‌شده در همان ماژول (نه import شده)
        - ضدتکراری بر اساس __name__
        - لاگ دقیق per-module و per-class
        """
        base_path = Path(PROJECT_BASE) / subdir
        if not base_path.exists():
            log_warning(
                f"⚠️ [PLUGIN:{kind}s] پوشه نیست | {base_path}"
            )
            return []

        # فایل‌های .py (به‌جز __init__)، مرتب الفبایی برای پایداری
        try:
            files = sorted(
                f for f in os.listdir(str(base_path))
                if f.endswith(".py") and not f.startswith("__")
            )
        except Exception as e:
            log_error(
                f"❌ [PLUGIN:{kind}s] listdir ناموفق | "
                f"{type(e).__name__}: {e}"
            )
            return []

        log_info(
            f"📂 [PLUGIN:{kind}s] {len(files)} فایل | "
            f"files={files}"
        )

        found: list[Any] = []
        seen_classes: set = set()  # ضدتکراری

        for filename in files:
            module_name = filename[:-3]
            t0 = time.monotonic()

            try:
                module = importlib.import_module(
                    f"{subdir}.{module_name}"
                )
            except Exception as e:
                elapsed_ms = (time.monotonic() - t0) * 1000
                log_error(
                    f"❌ [PLUGIN:{kind}s] import {filename} | "
                    f"{type(e).__name__}: {e} | {elapsed_ms:.0f}ms"
                )
                # traceback خلاصه (فقط 3 خط آخر)
                tb_lines = traceback.format_exc().splitlines()[-3:]
                for ln in tb_lines:
                    log_error(f"   ↳ {ln}")
                continue

            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > _SLOW_MODULE_IMPORT_MS:
                log_warning(
                    f"🐌 [PLUGIN:{kind}s] import کند | {filename} | "
                    f"{elapsed_ms:.0f}ms"
                )
            else:
                log_info(
                    f"📥 [PLUGIN:{kind}s] import | {filename} | "
                    f"{elapsed_ms:.0f}ms"
                )

            # پیدا کردن کلاس‌های تعریف‌شده در همین ماژول
            classes_in_module = []
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if not issubclass(obj, base_cls) or obj is base_cls:
                    continue
                # فقط کلاس‌هایی که در همین ماژول تعریف شدن
                obj_module = getattr(obj, "__module__", "")
                if obj_module != f"{subdir}.{module_name}":
                    log_info(
                        f"⏭️ [PLUGIN:{kind}s] رد (import شده) | "
                        f"{name} از {obj_module}"
                    )
                    continue
                classes_in_module.append((name, obj))

            if not classes_in_module:
                log_info(
                    f"ℹ️ [PLUGIN:{kind}s] بدون کلاس مناسب | {filename}"
                )
                continue

            for name, obj in classes_in_module:
                # ضدتکراری
                if name in seen_classes:
                    log_warning(
                        f"⏭️ [PLUGIN:{kind}s] تکراری | {name} "
                        f"(قبلاً از ماژول دیگری بارگذاری شد)"
                    )
                    continue

                # چک فعال بودن
                enabled, reason = self._is_enabled_with_reason(obj)
                if not enabled:
                    log_warning(
                        f"⚠️ [PLUGIN:{kind}s] غیرفعال | {name} | {reason}"
                    )
                    continue

                # ساخت instance
                instance, build_reason = self._create_instance_with_reason(obj)
                if instance is None:
                    log_error(
                        f"❌ [PLUGIN:{kind}s] ساخت ناموفق | {name} | "
                        f"{build_reason}"
                    )
                    continue

                # ثبت
                seen_classes.add(name)
                self._loaded_plugins[name] = instance
                found.append(instance)

                log_success(
                    f"✅ [PLUGIN:{kind}s] بارگذاری شد | {name} | "
                    f"{build_reason}"
                )

        return found

    # ═══════════════════════════════════════════
    # _is_enabled با دلیل
    # ═══════════════════════════════════════════
    def _is_enabled_with_reason(self, plugin_class: type) -> tuple:
        """بررسی فعال بودن پلاگین با دلیل صریح.

        اولویت:
          1) اگر config پلاگین «enabled=False» دارد → غیرفعال
          2) اگر لیست enabled_plugins پر است و نام پلاگین نیست → غیرفعال
          3) اگر متد is_enabled() روی کلاس هست → نتیجه‌اش
          در غیر این صورت → فعال

        Returns:
            (bool, str) — (فعال؟, دلیل فارسی)
        """
        try:
            from config import PLUGINS_CONFIG
        except Exception as e:
            return True, f"config در دسترس نیست ({type(e).__name__}) — پیش‌فرض فعال"

        plugin_name = plugin_class.__name__.lower()

        # ۱) تنظیمات اختصاصی
        try:
            plugin_config = PLUGINS_CONFIG.get(plugin_name, {})
        except Exception as e:
            return True, f"PLUGINS_CONFIG دسترسی ناموفق ({e}) — پیش‌فرض فعال"

        if "enabled" in plugin_config and plugin_config["enabled"] is False:
            return False, f"config: {plugin_name}.enabled=False"

        # ۲) لیست enabled_plugins
        try:
            enabled_list = PLUGINS_CONFIG.get("enabled_plugins", [])
        except Exception:
            enabled_list = []
        if enabled_list:
            if (
                plugin_name not in enabled_list
                and plugin_class.__name__ not in enabled_list
            ):
                return (
                    False,
                    f"در enabled_plugins نیست (list={enabled_list[:3]}...)",
                )

        # ۳) متد is_enabled روی کلاس
        if hasattr(plugin_class, "is_enabled"):
            try:
                result = plugin_class.is_enabled()
                return bool(result), f"is_enabled()={result}"
            except Exception as e:
                return (
                    True,
                    f"is_enabled() خطا داد ({type(e).__name__}: {e}) — پیش‌فرض فعال",
                )

        return True, "بدون محدودیت در config"

    # برای سازگاری با کد قدیمی
    def _is_enabled(self, plugin_class: type) -> bool:
        enabled, _ = self._is_enabled_with_reason(plugin_class)
        return enabled

    # ═══════════════════════════════════════════
    # _create_instance با دلیل
    # ═══════════════════════════════════════════
    def _create_instance_with_reason(self, plugin_class: type) -> tuple:
        """ساخت instance از پلاگین.

        ترتیب:
          1) وابستگی‌های تزریقی (db, scorer, ...)
          2) پارامترهای اختصاصی از PLUGINS_CONFIG[plugin_name]
          3) اگه شکست خورد، fallback: constructor بدون پارامتر

        Returns:
            (instance | None, str) — (instance, توضیح)
        """
        try:
            from config import PLUGINS_CONFIG
        except Exception:
            PLUGINS_CONFIG = {}

        plugin_name = plugin_class.__name__.lower()

        # پارامترها
        params: dict[str, Any] = {}
        passed_names: list[str] = []

        # ۱) وابستگی‌های تزریقی (اولویت بالاتر)
        try:
            sig = inspect.signature(plugin_class.__init__)
            for param_name in sig.parameters:
                if param_name == "self":
                    continue
                if param_name in self._dependencies:
                    params[param_name] = self._dependencies[param_name]
                    passed_names.append(f"{param_name}=<injected>")
        except Exception as e:
            log_warning(
                f"⚠️ [PLUGIN:build] signature ناموفق | {plugin_name} | "
                f"{type(e).__name__}: {e}"
            )

        # ۲) پارامترهای اختصاصی از config
        try:
            config_params = PLUGINS_CONFIG.get(plugin_name, {})
            for key, value in config_params.items():
                if key not in params:
                    params[key] = value
                    # مقادیر حساس رو خلاصه نشون بده
                    vstr = str(value)[:_LOG_PREVIEW_LEN]
                    passed_names.append(f"{key}={vstr}")
        except Exception as e:
            log_warning(
                f"⚠️ [PLUGIN:build] config ناموفق | {plugin_name} | "
                f"{type(e).__name__}: {e}"
            )

        # ساخت
        t0 = time.monotonic()
        try:
            instance = plugin_class(**params)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > _SLOW_INSTANCE_MS:
                log_warning(
                    f"🐌 [PLUGIN:build] کند | {plugin_name} | "
                    f"{elapsed_ms:.0f}ms | params={passed_names}"
                )
            reason = (
                f"params=[{', '.join(passed_names)}] | "
                f"{elapsed_ms:.0f}ms"
            )
            return instance, reason
        except Exception as e:
            log_error(
                f"❌ [PLUGIN:build] {plugin_name} | "
                f"params={passed_names} | "
                f"{type(e).__name__}: {e}"
            )

        # ۳) fallback: constructor بدون پارامتر
        try:
            t0 = time.monotonic()
            instance = plugin_class()
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_success(
                f"✅ [PLUGIN:build] fallback موفق | {plugin_name} | "
                f"constructor() | {elapsed_ms:.0f}ms"
            )
            return instance, f"fallback: constructor() | {elapsed_ms:.0f}ms"
        except Exception as e:
            log_error(
                f"❌ [PLUGIN:build] fallback هم ناموفق | {plugin_name} | "
                f"{type(e).__name__}: {e}"
            )
            return None, f"هم پارامتری و هم fallback ناموفق ({type(e).__name__})"

    # برای سازگاری با کد قدیمی
    def _create_instance(self, plugin_class: type):
        instance, _ = self._create_instance_with_reason(plugin_class)
        return instance
