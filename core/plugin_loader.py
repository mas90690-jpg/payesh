# -*- coding: utf-8 -*-
import os
import importlib
import inspect
from typing import List, Dict, Any
from core.base_collector import BaseCollector
from core.base_handler import BaseHandler
from core.logger import log_info, log_success, log_error, log_warning


class PluginLoader:
    """بارگذاری خودکار همه پلاگین‌ها - بدون نیاز به تغییر کد اصلی"""

    def __init__(self):
        self.collectors = []
        self.handlers = []
        self._loaded_plugins = {}
        self._dependencies = {}  # وابستگی‌های تزریقی (مثلاً db, scorer)

    def set_dependencies(self, **deps):
        """تنظیم وابستگی‌های مورد نیاز برای ساخت پلاگین‌ها"""
        self._dependencies.update(deps)
        log_info(f"وابستگی‌های سراسری ثبت شد: {list(deps.keys())}")

    def discover_collectors(
        self, collectors_dir: str = "collectors"
    ) -> List[BaseCollector]:
        """پیدا کردن خودکار همه کلکتورها از پوشه collectors"""
        log_info("جستجوی کلکتورها...")

        collectors = []
        collectors_path = os.path.join(os.getcwd(), collectors_dir)

        if not os.path.exists(collectors_path):
            log_warning(f"پوشه {collectors_dir} وجود ندارد")
            return collectors

        for filename in os.listdir(collectors_path):
            if filename.endswith(".py") and not filename.startswith("__"):
                module_name = filename[:-3]
                try:
                    module = importlib.import_module(f"{collectors_dir}.{module_name}")

                    for name, obj in inspect.getmembers(module, inspect.isclass):
                        if issubclass(obj, BaseCollector) and obj != BaseCollector:
                            if self._is_enabled(obj):
                                collector_instance = self._create_instance(obj)
                                if collector_instance:
                                    collectors.append(collector_instance)
                                    log_success(
                                        f"✅ کلکتور بارگذاری شد: {name} ({obj.__name__})"
                                    )
                            else:
                                log_warning(f"⚠️ کلکتور غیرفعال: {name}")

                except Exception as e:
                    log_error(f"خطا در بارگذاری {filename}: {e}")

        self.collectors = collectors
        log_success(f"تعداد کل کلکتورهای بارگذاری شده: {len(collectors)}")
        return collectors

    def discover_handlers(self, handlers_dir: str = "handlers") -> List[BaseHandler]:
        """پیدا کردن خودکار همه هندلرها از پوشه handlers"""
        log_info("جستجوی هندلرها...")

        handlers = []
        handlers_path = os.path.join(os.getcwd(), handlers_dir)

        if not os.path.exists(handlers_path):
            log_warning(f"پوشه {handlers_dir} وجود ندارد")
            return handlers

        for filename in os.listdir(handlers_path):
            if filename.endswith(".py") and not filename.startswith("__"):
                module_name = filename[:-3]
                try:
                    module = importlib.import_module(f"{handlers_dir}.{module_name}")

                    for name, obj in inspect.getmembers(module, inspect.isclass):
                        if issubclass(obj, BaseHandler) and obj != BaseHandler:
                            if self._is_enabled(obj):
                                handler_instance = self._create_instance(obj)
                                if handler_instance:
                                    handlers.append(handler_instance)
                                    log_success(f"✅ هندلر بارگذاری شد: {name}")
                            else:
                                log_warning(f"⚠️ هندلر غیرفعال: {name}")

                except Exception as e:
                    log_error(f"خطا در بارگذاری {filename}: {e}")

        self.handlers = handlers
        log_success(f"تعداد کل هندلرهای بارگذاری شده: {len(handlers)}")
        return handlers

    def _is_enabled(self, plugin_class) -> bool:
        """
        بررسی فعال بودن پلاگین از config
        اولویت:
        1. اگر در config پلاگین کلید 'enabled' با مقدار False وجود داشته باشد -> غیرفعال
        2. اگر لیست enabled_plugins غیر خالی باشد و نام پلاگین در آن نباشد -> غیرفعال
        3. اگر متد is_enabled در کلاس تعریف شده باشد -> نتیجه آن
        در غیر این صورت -> فعال
        """
        from config import PLUGINS_CONFIG

        # نام پلاگین: نام کلاس به صورت کوچک (مثلاً BaleCollector -> balecollector)
        plugin_name = plugin_class.__name__.lower()

        # 1. بررسی تنظیمات اختصاصی پلاگین
        plugin_config = PLUGINS_CONFIG.get(plugin_name, {})
        if "enabled" in plugin_config and plugin_config["enabled"] is False:
            return False

        # 2. بررسی لیست enabled_plugins (اگر لیست خالی نباشد)
        enabled_list = PLUGINS_CONFIG.get("enabled_plugins", [])
        if enabled_list:
            # اگر لیست فعال‌ها پر است، فقط آنهایی که در لیست هستند فعال می‌شوند
            # توجه: ممکن است نام در لیست با نام کلاس یا نام ساده شده باشد
            if (
                plugin_name not in enabled_list
                and plugin_class.__name__ not in enabled_list
            ):
                return False

        # 3. بررسی متد is_enabled در خود کلاس
        if hasattr(plugin_class, "is_enabled"):
            try:
                return plugin_class.is_enabled()
            except:
                pass

        # در غیر این صورت فعال است
        return True

    def _create_instance(self, plugin_class):
        """ساخت instance از پلاگین با ترکیب وابستگی‌های تزریقی و تنظیمات config"""
        from config import PLUGINS_CONFIG

        plugin_name = plugin_class.__name__.lower()
        params = {}

        # 1. وابستگی‌های تزریقی (اولویت بالاتر)
        sig = inspect.signature(plugin_class.__init__)
        for param_name in sig.parameters:
            if param_name == "self":
                continue
            if param_name in self._dependencies:
                params[param_name] = self._dependencies[param_name]

        # 2. پارامترهای اختصاصی از config (برای پارامترهایی که مقداردهی نشده‌اند)
        config_params = PLUGINS_CONFIG.get(plugin_name, {})
        for key, value in config_params.items():
            if key not in params:
                params[key] = value

        try:
            return plugin_class(**params)
        except Exception as e:
            log_error(f"خطا در ساخت instance از {plugin_name}: {e}")
            try:
                return plugin_class()
            except:
                return None
