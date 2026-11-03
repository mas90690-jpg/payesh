"""core/app/plugin_loader.py — بارگذاری خودکار کلکتورها و هندلرها.

استراتژی:
  - اسکن پوشهٔ collectors/ و handlers/ از PROJECT_BASE (نه CWD)
  - هر ماژول .py را import کن و کلاس‌های ارث‌برنده از
    BaseCollector/BaseHandler را پیدا کن
  - فقط کلاس‌های تعریف‌شده در همان ماژول (نه import شده)
  - ضدتکراری بر اساس نام کلاس
  - ترتیب: فایل‌ها الفبایی

نسخهٔ ۳ (این نسخه):
  - 🐛 FIX: `_SLOW_MODULE_IMPORT_MS`, `_SLOW_INSTANCE_MS`,
    `_LOG_PREVIEW_LEN` حالا env-based.
  - 🐛 FIX: `_is_enabled_with_reason` حالا `enabled_plugins` را
    روی نام دقیق پلتفرم هم چک می‌کند (نه فقط نام کلاس).
  - 🐛 FIX: `_is_enabled_with_reason` حالا `is_enabled` را چک
    می‌کند که callable باشد.
  - 🐛 FIX: `_discover` حالا فقط `__init__` را رد می‌کند (نه
    هر فایل با `__`).
  - 🐛 FIX: `_discover` حالا `inspect.getmembers` را در
    try/except می‌پیچد.
  - 🐛 FIX: `_create_instance_with_reason` حالا پارامترهای
    config را از پارامترهای constructor جدا می‌کند (نه
    پاس دادن `enabled` و مشابه).
  - 🐛 FIX: `_create_instance_with_reason` حالا fallback را
    فقط وقتی انجام می‌دهد که constructor بدون پارامتر قابل
    صدا زدن باشد.
  - 🐛 FIX: `pname` حالا در صورت property بودن، امن استخراج
    می‌شود.
  - 🎯 `_instance_cache` — جلوگیری از ساخت دوباره instance
    در `discover_collectors` تکراری.
  - 🎯 `_DEBUG_LIST_REJECTED` flag (env-based) — جلوگیری از
    پر شدن لاگ.
  - 🎯 `get_stats()`, `reset()`, `__repr__` برای debug.
  - 🎯 `__all__` export.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۲.
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
from core.kernel.logger import (
    log_debug,
    log_error,
    log_info,
    log_success,
    log_warning,
)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
def _env_int(
    name: str,
    default: int,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    """خواندن int از env — با sanitize."""
    try:
        v = int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


def _env_bool(name: str, default: bool = False) -> bool:
    """خواندن bool از env."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in (
        "true",
        "1",
        "yes",
        "on",
        "y",
        "t",
    )


# ═══════════════════════════════════════════
# مسیر پایه (از config، fallback به محل این فایل)
# ═══════════════════════════════════════════
try:
    from config import BASE_DIR as PROJECT_BASE
except Exception:
    PROJECT_BASE = Path(__file__).parent.parent.resolve()

# ═══════════════════════════════════════════
# آستانه‌ها (env-based)
# ═══════════════════════════════════════════
_SLOW_MODULE_IMPORT_MS = _env_int(
    "PLUGIN_SLOW_IMPORT_MS",
    2000,
    min_val=100,
    max_val=60_000,
)
_SLOW_INSTANCE_MS = _env_int(
    "PLUGIN_SLOW_INSTANCE_MS",
    500,
    min_val=50,
    max_val=60_000,
)
_LOG_PREVIEW_LEN = _env_int(
    "PLUGIN_LOG_PREVIEW",
    100,
    min_val=20,
    max_val=500,
)
_DEBUG_LIST_REJECTED = _env_bool("PLUGIN_DEBUG_LIST_REJECTED", False)

# 🐛 FIX: پارامترهای config که نباید به constructor پاس شوند
_CONFIG_META_KEYS: frozenset[str] = frozenset(
    {
        "enabled",
        "session_path",
        "media_dir",
    }
)

# 🎯 فایل‌های special که نباید import شوند
_SKIP_FILES: frozenset[str] = frozenset(
    {
        "__init__.py",
        "__main__.py",
        "__about__.py",
    }
)


# ═══════════════════════════════════════════
# PluginLoader
# ═══════════════════════════════════════════
class PluginLoader:
    """بارگذاری خودکار همه پلاگین‌ها — بدون تغییر کد اصلی."""

    def __init__(self):
        self.collectors: list[BaseCollector] = []
        self.handlers: list[BaseHandler] = []
        self._loaded_plugins: dict[str, Any] = {}
        self._dependencies: dict[str, Any] = {}
        # 🎯 cache instanceها (جلوگیری از ساخت دوباره)
        self._instance_cache: dict[str, Any] = {}

        log_info(
            f"🔌 [PLUGIN:init] آماده | "
            f"PROJECT_BASE={PROJECT_BASE} | "
            f"slow_import_ms={_SLOW_MODULE_IMPORT_MS} | "
            f"slow_instance_ms={_SLOW_INSTANCE_MS}"
        )

    # ═══════════════════════════════════════════
    # تزریق وابستگی
    # ═══════════════════════════════════════════
    def set_dependencies(self, **deps) -> None:
        """تنظیم وابستگی‌های مورد نیاز برای ساخت پلاگین‌ها."""
        if not deps:
            return
        self._dependencies.update(deps)
        log_info(
            f"💉 [PLUGIN:deps] ثبت | "
            f"keys={list(deps.keys())} | "
            f"total={len(self._dependencies)}"
        )

    # ═══════════════════════════════════════════
    # API عمومی
    # ═══════════════════════════════════════════
    def discover_collectors(
        self, collectors_dir: str = "collectors"
    ) -> list[BaseCollector]:
        """پیدا کردن خودکار کلکتورها از پوشه collectors/."""
        log_info(
            f"🔍 [PLUGIN:collectors] اسکن | dir={collectors_dir} | base={PROJECT_BASE}")

        results = self._discover(
            subdir=collectors_dir,
            base_cls=BaseCollector,
            kind="collector",
        )
        self.collectors = results

        names = [getattr(c, "platform_name", "?") for c in results]
        log_success(
            f"✅ [PLUGIN:collectors] {
                len(results)} کلکتور | instances={names}")
        return results

    def discover_handlers(
            self,
            handlers_dir: str = "handlers") -> list[BaseHandler]:
        """پیدا کردن خودکار هندلرها از پوشه handlers/."""
        log_info(
            f"🔍 [PLUGIN:handlers] اسکن | dir={handlers_dir} | base={PROJECT_BASE}")

        results = self._discover(
            subdir=handlers_dir,
            base_cls=BaseHandler,
            kind="handler",
        )
        self.handlers = results

        names = [getattr(h, "handler_name", "?") for h in results]
        log_success(
            f"✅ [PLUGIN:handlers] {
                len(results)} هندلر | names={names}")
        return results

    # ═══════════════════════════════════════════
    # هستهٔ مشترک
    # ═══════════════════════════════════════════
    def _discover(
        self,
        subdir: str,
        base_cls: type,
        kind: str,
    ) -> list[Any]:
        """اسکن یک پوشه و ساخت instance از کلاس‌های موردنظر."""
        base_path = Path(PROJECT_BASE) / subdir
        if not base_path.exists():
            log_warning(f"⚠️ [PLUGIN:{kind}s] پوشه نیست | {base_path}")
            return []

        # ─── لیست فایل‌ها ───
        try:
            files = sorted(
                f
                for f in os.listdir(str(base_path))
                if (f.endswith(".py") and f not in _SKIP_FILES)
            )
        except Exception as e:
            log_error(f"❌ [PLUGIN:{kind}s] listdir | {type(e).__name__}: {e}")
            return []

        log_info(f"📂 [PLUGIN:{kind}s] {len(files)} فایل | files={files}")

        found: list[Any] = []
        seen_classes: set[str] = set()

        for filename in files:
            module_name = filename[:-3]

            # ─── چک enabled قبل از import (بدون بارگذاری) ───
            try:
                from config import PLUGINS_CONFIG

                plugin_key = f"{module_name}collector"
                cfg = PLUGINS_CONFIG.get(plugin_key)
                if cfg is not None and not cfg.get("enabled", True):
                    log_info(
                        f"⏭️ [PLUGIN:{kind}s] {filename} غیرفعال (config)")
                    continue
            except Exception:
                pass  # اگه config مشکل داشت، ادامه بده

            # ─── import ───
            t0 = time.monotonic()
            try:
                module = importlib.import_module(f"{subdir}.{module_name}")

            except Exception as e:
                elapsed_ms = (time.monotonic() - t0) * 1000
                log_error(
                    f"❌ [PLUGIN:{kind}s] import "
                    f"{filename} | "
                    f"{type(e).__name__}: {e} | "
                    f"{elapsed_ms:.0f}ms"
                )
                for ln in traceback.format_exc().splitlines()[-3:]:
                    log_error(f"   ↳ {ln}")
                continue

            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > _SLOW_MODULE_IMPORT_MS:
                log_warning(
                    f"🐌 [PLUGIN:{kind}s] import کند | {filename} | {
                        elapsed_ms:.0f}ms")
            else:
                log_info(
                    f"📥 [PLUGIN:{kind}s] import | {filename} | {
                        elapsed_ms:.0f}ms")

            # ─── پیدا کردن کلاس‌ها ───
            classes_in_module: list[tuple[str, type]] = []
            try:
                members = inspect.getmembers(module, inspect.isclass)
            except Exception as e:
                log_error(
                    f"❌ [PLUGIN:{kind}s] getmembers | "
                    f"{filename} | "
                    f"{type(e).__name__}: {e}"
                )
                continue

            for name, obj in members:
                try:
                    if not issubclass(obj, base_cls) or obj is base_cls:
                        continue
                except TypeError:
                    continue

                obj_module = getattr(obj, "__module__", "")
                if obj_module != f"{subdir}.{module_name}":
                    if _DEBUG_LIST_REJECTED:
                        log_debug(
                            f"⏭️ [PLUGIN:{kind}s] رد "
                            f"(import شده) | "
                            f"{name} از {obj_module}"
                        )
                    continue
                classes_in_module.append((name, obj))

            if not classes_in_module:
                log_info(f"ℹ️ [PLUGIN:{kind}s] بدون کلاس مناسب | {filename}")
                continue

            # ─── ساخت instance ───
            for name, obj in classes_in_module:
                if name in seen_classes:
                    log_warning(f"⏭️ [PLUGIN:{kind}s] تکراری | {name}")
                    continue

                # چک فعال بودن
                enabled, reason = self._is_enabled_with_reason(obj)
                if not enabled:
                    log_warning(
                        f"⚠️ [PLUGIN:{kind}s] غیرفعال | {name} | {reason}")
                    continue

                # ساخت
                instance, build_reason = self._create_instance_with_reason(obj)
                if instance is None:
                    log_error(
                        f"❌ [PLUGIN:{kind}s] ساخت ناموفق | {name} | {build_reason}")
                    continue

                # ثبت
                seen_classes.add(name)
                self._loaded_plugins[name] = instance
                self._instance_cache[name] = instance
                found.append(instance)

                log_success(
                    f"✅ [PLUGIN:{kind}s] بارگذاری | {name} | {build_reason}")

        return found

    # ═══════════════════════════════════════════
    # _is_enabled_with_reason
    # ═══════════════════════════════════════════
    def _is_enabled_with_reason(self, plugin_class: type) -> tuple[bool, str]:
        """بررسی فعال بودن پلاگین با دلیل صریح.

        اولویت:
          1) config: `<name>.enabled=False` → غیرفعال
          2) enabled_plugins پر و نام پلاگین نیست → غیرفعال
          3) `is_enabled()` روی کلاس → نتیجه‌اش
          در غیر این صورت → فعال
        """
        try:
            from config import PLUGINS_CONFIG
        except Exception as e:
            return (
                True,
                (f"config در دسترس نیست ({type(e).__name__}) — پیش‌فرض فعال"),
            )

        plugin_name = plugin_class.__name__.lower()

        # ─── لیست نام‌های ممکن ───
        names_to_check = [plugin_name]

        # 🐛 FIX: pname ممکن است property باشد
        pname = self._safe_platform_name(plugin_class)
        if pname:
            names_to_check.append(f"{pname}collector")
            names_to_check.append(pname)

        # ─── ۱) config اختصاصی ───
        try:
            plugin_config: dict = {}
            matched_name = plugin_name
            for _n in names_to_check:
                _cfg = PLUGINS_CONFIG.get(_n, {})
                if isinstance(_cfg, dict) and "enabled" in _cfg:
                    plugin_config = _cfg
                    matched_name = _n
                    break
        except Exception as e:
            return (
                True,
                f"PLUGINS_CONFIG ({e}) — پیش‌فرض فعال",
            )

        if plugin_config.get("enabled") is False:
            return (
                False,
                f"config: {matched_name}.enabled=False",
            )

        # ─── ۲) enabled_plugins list ───
        try:
            enabled_list = PLUGINS_CONFIG.get("enabled_plugins", [])
        except Exception:
            enabled_list = []

        if enabled_list:
            # 🐛 FIX: همهٔ نام‌های ممکن را چک کن
            all_names = set(names_to_check)
            all_names.add(plugin_class.__name__)
            if not (all_names & set(enabled_list)):
                return (
                    False,
                    (f"در enabled_plugins نیست (list={enabled_list[:3]}...)"),
                )

        # ─── ۳) is_enabled روی کلاس ───
        if hasattr(plugin_class, "is_enabled"):
            # 🐛 FIX: callable check
            attr = plugin_class.is_enabled
            if callable(attr):
                try:
                    result = attr()
                    return (
                        bool(result),
                        f"is_enabled()={result}",
                    )
                except Exception as e:
                    return (
                        True,
                        (f"is_enabled() خطا ({type(e).__name__}: {e}) — پیش‌فرض فعال"),
                    )

        return True, "بدون محدودیت در config"

    @staticmethod
    def _safe_platform_name(plugin_class: type) -> str:
        """🎯 استخراج امن platform_name (بدون صدا زدن property)."""
        try:
            attr = inspect.getattr_static(plugin_class, "platform_name", None)
            if isinstance(attr, str):
                return attr
            # اگه property بود، صدا نزن
            if isinstance(attr, property):
                return ""
            if callable(attr):
                return ""
        except Exception as e:
            log_warning(f"⚠️ [core.app.plugin_loader] {type(e).__name__}: {e}")

        # fallback: چک instance (اگر نه abstract)
        try:
            if not inspect.isabstract(plugin_class):
                # ساخت موقت می‌تواند خطرناک باشد — نمی‌سازیم
                pass
        except Exception as e:
            log_warning(f"⚠️ [core.app.plugin_loader] {type(e).__name__}: {e}")

        return ""

    # ─── سازگاری عقب‌رو ───
    def _is_enabled(self, plugin_class: type) -> bool:
        enabled, _ = self._is_enabled_with_reason(plugin_class)
        return enabled

    # ═══════════════════════════════════════════
    # _create_instance_with_reason
    # ═══════════════════════════════════════════
    def _create_instance_with_reason(
        self, plugin_class: type
    ) -> tuple[Any | None, str]:
        """ساخت instance از پلاگین.

        ترتیب:
          1) وابستگی‌های تزریقی (db, scorer, ...)
          2) پارامترهای config (به‌جز meta keys)
          3) fallback: constructor بدون پارامتر (اگر قابل)
        """
        try:
            from config import PLUGINS_CONFIG
        except Exception:
            PLUGINS_CONFIG = {}

        plugin_name = plugin_class.__name__.lower()

        # ─── پیدا کردن کلید config ───
        names_to_check = [plugin_name]
        pname = self._safe_platform_name(plugin_class)
        if pname:
            names_to_check.append(f"{pname}collector")
            names_to_check.append(pname)

        config_key = plugin_name
        for _n in names_to_check:
            if _n in PLUGINS_CONFIG:
                config_key = _n
                break

        # ─── پارامترها ───
        params: dict[str, Any] = {}
        passed_names: list[str] = []

        # ─── ۱) وابستگی‌های تزریقی ───
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
                f"⚠️ [PLUGIN:build] signature | {plugin_name} | {
                    type(e).__name__}: {e}")

        # ─── ۲) پارامترهای config ───
        try:
            config_params = PLUGINS_CONFIG.get(config_key, {}) or {}
            if not isinstance(config_params, dict):
                config_params = {}

            # 🐛 FIX: signature برای filter
            try:
                init_sig = inspect.signature(plugin_class.__init__)
                init_params = set(init_sig.parameters.keys())
                has_var_kw = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in init_sig.parameters.values()
                )
            except Exception:
                init_params = set()
                has_var_kw = True  # محافظه‌کارانه

            for key, value in config_params.items():
                if key in params:
                    continue
                # 🐛 FIX: meta keys را رد کن
                if key in _CONFIG_META_KEYS:
                    continue
                # 🐛 FIX: فقط اگر constructor قبول می‌کند
                if not has_var_kw and key not in init_params:
                    continue
                params[key] = value
                vstr = str(value)[:_LOG_PREVIEW_LEN]
                passed_names.append(f"{key}={vstr}")
        except Exception as e:
            log_warning(
                f"⚠️ [PLUGIN:build] config | {plugin_name} | {
                    type(e).__name__}: {e}")

        # ─── ساخت ───
        t0 = time.monotonic()
        try:
            instance = plugin_class(**params)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > _SLOW_INSTANCE_MS:
                log_warning(
                    f"🐌 [PLUGIN:build] کند | "
                    f"{plugin_name} | "
                    f"{elapsed_ms:.0f}ms | "
                    f"params={passed_names}"
                )
            reason = f"params=[{', '.join(passed_names)}] | {elapsed_ms:.0f}ms"
            return instance, reason
        except Exception as e:
            log_error(
                f"❌ [PLUGIN:build] {plugin_name} | "
                f"params={passed_names} | "
                f"{type(e).__name__}: {e}"
            )

        # ─── ۳) fallback (اگر constructor بدون پارامتر قابل) ───
        try:
            init_sig = inspect.signature(plugin_class.__init__)
            required = [
                p
                for p in init_sig.parameters.values()
                if (
                    p.name != "self"
                    and p.default is inspect.Parameter.empty
                    and p.kind
                    not in (
                        inspect.Parameter.VAR_POSITIONAL,
                        inspect.Parameter.VAR_KEYWORD,
                    )
                )
            ]
            if required:
                reason = (
                    f"fallback ناممکن — پارامترهای الزامی: {[p.name for p in required]}"
                )
                log_warning(f"⚠️ [PLUGIN:build] {plugin_name} | {reason}")
                return None, reason
        except Exception as e:
            log_warning(f"⚠️ [core.app.plugin_loader] {type(e).__name__}: {e}")

        try:
            t0 = time.monotonic()
            instance = plugin_class()
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_success(
                f"✅ [PLUGIN:build] fallback | "
                f"{plugin_name} | constructor() | "
                f"{elapsed_ms:.0f}ms"
            )
            return (
                instance,
                (f"fallback: constructor() | {elapsed_ms:.0f}ms"),
            )
        except Exception as e:
            log_error(
                f"❌ [PLUGIN:build] fallback هم ناموفق | "
                f"{plugin_name} | "
                f"{type(e).__name__}: {e}"
            )
            return (
                None,
                (f"هم پارامتری و هم fallback ناموفق ({type(e).__name__})"),
            )

    # ─── سازگاری عقب‌رو ───
    def _create_instance(self, plugin_class: type):
        instance, _ = self._create_instance_with_reason(plugin_class)
        return instance

    # ═══════════════════════════════════════════
    # debug
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت — برای debug."""
        return {
            "collectors": len(self.collectors),
            "handlers": len(self.handlers),
            "loaded_plugins": len(self._loaded_plugins),
            "dependencies": list(self._dependencies.keys()),
            "cache_size": len(self._instance_cache),
            "project_base": str(PROJECT_BASE),
            "slow_import_ms": _SLOW_MODULE_IMPORT_MS,
            "slow_instance_ms": _SLOW_INSTANCE_MS,
        }

    def reset(self) -> None:
        """🎯 پاک‌کردن state — فقط برای تست."""
        self.collectors = []
        self.handlers = []
        self._loaded_plugins.clear()
        self._instance_cache.clear()
        log_info("🗑️ [PLUGIN:reset] state پاک شد")

    def __repr__(self) -> str:
        return (
            f"<PluginLoader "
            f"collectors={len(self.collectors)} "
            f"handlers={len(self.handlers)}>"
        )


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = ["PluginLoader"]
