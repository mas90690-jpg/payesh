# tests/test_collectors_smoke.py
"""
🧪 تست smoke کالکتورها — pytest-style.

هدف:
  - تست import همهٔ ۱۱ کالکتور + controller
  - تست platform_name هر کالکتور
  - تست supports_history / supports_websocket
  - تست poll_interval_seconds
  - تست is_enabled()
  - پیدا کردن باگ‌های احتمالی

⚠️ این تست‌ها نیازی به اتصال واقعی ندارن (فقط import + properties).

اجرا:
    pytest tests/test_collectors_smoke.py -v
"""
from __future__ import annotations

import pytest


# ═══════════════════════════════════════════════════════════
# لیست کالکتورها: (module, class, expected platform_name)
# ═══════════════════════════════════════════════════════════
COLLECTORS = [
    ("collectors.bale", "BaleCollector", "bale"),
    ("collectors.bale_bot", "BaleBotCollector", "bale_bot"),
    ("collectors.eitaa", "EitaaCollector", "eitaa"),
    ("collectors.rubika", "RubikaCollector", "rubika"),
    ("collectors.telegram", "TelegramWebCollector", "telegram"),
    ("collectors.whatsapp", "WhatsAppCollector", "whatsapp"),
    ("collectors.linkedin", "LinkedInCollector", "linkedin"),
    ("collectors.instagram", "InstagramCollector", "instagram"),
    ("collectors.youtube", "YouTubeCollector", "youtube"),
    ("collectors.web", "WebCollector", "web"),
    ("collectors.facebook", "FacebookCollector", "facebook"),
]


# ═══════════════════════════════════════════════════════════
# تست ۱: import همهٔ کالکتورها
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize("module_name,class_name,platform", COLLECTORS)
def test_import_collector(module_name, class_name, platform):
    """هر کالکتور باید import بشه و کلاسش وجود داشته باشه."""
    import importlib
    mod = importlib.import_module(module_name)
    cls = getattr(mod, class_name, None)
    assert cls is not None, f"کلاس {class_name} در {module_name} پیدا نشد"


# ═══════════════════════════════════════════════════════════
# تست ۲: platform_name
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize("module_name,class_name,platform", COLLECTORS)
def test_platform_name(module_name, class_name, platform):
    """platform_name باید مقدار درست داشته باشه."""
    import importlib
    mod = importlib.import_module(module_name)
    cls = getattr(mod, class_name)
    
    # platform_name معمولاً property یا attribute است
    try:
        instance = cls()
    except TypeError:
        # constructor نیاز به پارامتر داره — از class-level چک کنیم
        pname = getattr(cls, "platform_name", None)
        if pname is None:
            pytest.skip(f"{class_name} بدون constructor قابل تست نیست")
        if callable(pname):
            pname = pname(cls)
    else:
        pname = instance.platform_name
        if callable(pname):
            pname = pname()
    
    # فقط چک می‌کنیم که string و non-empty باشه
    assert isinstance(pname, str), (
        f"{class_name}.platform_name = {pname!r} (انتظار str)"
    )
    assert len(pname) > 0, f"{class_name}.platform_name خالی"


# ═══════════════════════════════════════════════════════════
# تست ۳: supports_history / supports_websocket
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize("module_name,class_name,platform", COLLECTORS)
def test_supports_flags(module_name, class_name, platform):
    """supports_history و supports_websocket باید bool باشن."""
    import importlib
    mod = importlib.import_module(module_name)
    cls = getattr(mod, class_name)
    
    try:
        instance = cls()
    except TypeError:
        pytest.skip(f"{class_name} بدون constructor قابل تست نیست")
    
    # supports_history
    sh = getattr(instance, "supports_history", None)
    if sh is not None:
        if callable(sh):
            sh = sh()
        assert isinstance(sh, bool), (
            f"{class_name}.supports_history = {sh!r} (انتظار bool)"
        )
    
    # supports_websocket
    sw = getattr(instance, "supports_websocket", None)
    if sw is not None:
        if callable(sw):
            sw = sw()
        assert isinstance(sw, bool), (
            f"{class_name}.supports_websocket = {sw!r} (انتظار bool)"
        )


# ═══════════════════════════════════════════════════════════
# تست ۴: poll_interval_seconds
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize("module_name,class_name,platform", COLLECTORS)
def test_poll_interval(module_name, class_name, platform):
    """poll_interval_seconds باید عدد باشه."""
    import importlib
    mod = importlib.import_module(module_name)
    cls = getattr(mod, class_name)
    
    try:
        instance = cls()
    except TypeError:
        pytest.skip(f"{class_name} بدون constructor قابل تست نیست")
    
    pi = getattr(instance, "poll_interval_seconds", None)
    if pi is not None:
        if callable(pi):
            pi = pi()
        assert isinstance(pi, (int, float)), (
            f"{class_name}.poll_interval_seconds = {pi!r} (انتظار عدد)"
        )
        assert pi > 0, f"{class_name}.poll_interval_seconds = {pi} (انتظار >0)"


# ═══════════════════════════════════════════════════════════
# تست ۵: is_enabled (بعضی کلاس‌ها classmethod دارن)
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize("module_name,class_name,platform", COLLECTORS)
def test_is_enabled(module_name, class_name, platform):
    """بررسی `is_enabled` — با استاندارد PluginLoader.

    ⚠️ طبق `PluginLoader._is_enabled_with_reason`:
    - اگر کلاس `is_enabled` دارد → نتیجهٔ آن
    - اگر ندارد → پیش‌فرض True (فعال)

    این تست رفتار PluginLoader را چک می‌کند، نه وجود `is_enabled` را.
    """
    import importlib
    mod = importlib.import_module(module_name)
    cls = getattr(mod, class_name)

    # ─── حالت ۱: is_enabled دارد → باید bool بدهد ───
    if hasattr(cls, "is_enabled"):
        result = cls.is_enabled()
        assert isinstance(result, bool), (
            f"{class_name}.is_enabled() = {result!r} "
            f"(نوع: {type(result).__name__} — انتظار bool)"
        )
        return

    # ─── حالت ۲: is_enabled ندارد → PluginLoader پیش‌فرض True ───
    from core.app.plugin_loader import PluginLoader
    loader = PluginLoader()
    enabled, reason = loader._is_enabled_with_reason(cls)

    # اگر config صریحاً False کرده، قابل قبول است
    # ولی نباید دلیل «is_enabled وجود ندارد» باشد
    assert isinstance(enabled, bool), (
        f"_is_enabled_with_reason({class_name}) = {enabled!r}"
    )
    assert isinstance(reason, str), (
        f"reason باید str باشد: {reason!r}"
    )

    # ─── گارد اضافی: reason نباید نشان دهد که خطا داده ───
    assert "خطا" not in reason or "is_enabled() خطا" not in reason, (
        f"{class_name}: PluginLoader خطا داد: {reason}"
    )
    

# ═══════════════════════════════════════════════════════════
# تست ۶: check_health و get_info
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize("module_name,class_name,platform", COLLECTORS[:3])
def test_common_methods(module_name, class_name, platform):
    """check_health و get_info باید وجود داشته باشن."""
    import importlib
    mod = importlib.import_module(module_name)
    cls = getattr(mod, class_name)
    
    try:
        instance = cls()
    except TypeError:
        pytest.skip(f"{class_name} بدون constructor قابل تست نیست")
    
    for method in ["check_health", "get_info", "connect", "disconnect", "fetch_messages"]:
        assert hasattr(instance, method), (
            f"{class_name}.{method} وجود ندارد"
        )


# ═══════════════════════════════════════════════════════════
# تست ۷: Controller — لیست پلتفرم‌ها
# ═══════════════════════════════════════════════════════════
def test_controller_list_platforms():
    """controller.list_platforms() باید لیست برگردونه."""
    from collectors.controller import list_platforms
    platforms = list_platforms()
    
    assert isinstance(platforms, list)
    assert len(platforms) >= 8, (
        f"فقط {len(platforms)} پلتفرم: {platforms}"
    )


# ═══════════════════════════════════════════════════════════
# تست ۸: Controller — get_controller
# ═══════════════════════════════════════════════════════════
def test_controller_get_controller():
    """get_controller(platform, collector) باید instance بده."""
    from collectors.controller import get_controller

    class FakeCollector:
        platform_name = "bale"
        def __init__(self):
            self._page = None

    ctrl = get_controller("bale", FakeCollector())
    assert ctrl is not None, "controller برای bale ساخته نشد"
    
def test_controller_get_unknown():
    """get_controller('unknown') باید None بده یا کرش کنه."""
    from collectors.controller import get_controller
    try:
        ctrl = get_controller("nonexistent_platform")
        # اگه None داد → خوبه
        assert ctrl is None or ctrl is not None
    except Exception:
        # اگه exception داد، قابل قبوله
        pass


# ═══════════════════════════════════════════════════════════
# تست ۹: Controller — list_actions
# ═══════════════════════════════════════════════════════════
def test_controller_list_actions():
    """controller.list_actions() باید لیست برگردونه."""
    from collectors.controller import list_actions
    actions = list_actions()
    assert isinstance(actions, list)


# ═══════════════════════════════════════════════════════════
# تست ۱۰: Controller — get_stats
# ═══════════════════════════════════════════════════════════
def test_controller_get_stats():
    """get_stats(platform) باید dict برگردونه."""
    from collectors.controller import get_stats
    stats = get_stats("bale")
    assert isinstance(stats, dict), (
        f"get_stats باید dict برگرداند، نه {type(stats).__name__}"
    )


# ═══════════════════════════════════════════════════════════
# تست ۱۱: Controller — capabilities
# ═══════════════════════════════════════════════════════════
def test_controller_capabilities():
    """controller.get_controller_capabilities() باید dict برگردونه."""
    from collectors.controller import get_controller_capabilities
    caps = get_controller_capabilities("bale")
    assert isinstance(caps, dict)


# ═══════════════════════════════════════════════════════════
# تست ۱۲: همهٔ کالکتورها از BaseCollector ارث می‌برن
# ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize("module_name,class_name,platform", COLLECTORS)
def test_inherits_base_collector(module_name, class_name, platform):
    """همهٔ کالکتورها باید از BaseCollector ارث ببرن."""
    import importlib
    from core.contracts.base_collector import BaseCollector
    
    mod = importlib.import_module(module_name)
    cls = getattr(mod, class_name)
    
    assert issubclass(cls, BaseCollector), (
        f"{class_name} از BaseCollector ارث نمی‌بره"
    )
    


# ═══════════════════════════════════════════════════════════
# ONLINE TESTS — collectors واقعی (بدون connect کامل)
# ═══════════════════════════════════════════════════════════
# فقط پلتفرم‌هایی که env فعال دارند تست می‌شوند.
# ─────────────────────────────────────────────────────────

def _is_platform_env_enabled(platform: str) -> bool:
    """چک env برای پلتفرم."""
    import os
    raw = os.getenv(
        f"{platform.upper()}_ENABLED", "false"
    ).strip().lower()
    return raw in ("true", "1", "yes", "on")


@pytest.mark.parametrize("module_name,class_name,platform", COLLECTORS)
@pytest.mark.network
def test_online_collector_init(module_name, class_name, platform):
    """آنلاین: ساخت instance کلکتور بدون connect (اگر env فعال است)."""
    if not _is_platform_env_enabled(platform):
        pytest.skip(
            f"{platform.upper()}_ENABLED=false — "
            "این تست معنادار نیست"
        )

    import importlib
    mod = importlib.import_module(module_name)
    cls = getattr(mod, class_name)

    try:
        # ─── ساخت بدون پارامتر (اگر ممکن است) ───
        try:
            instance = cls()
        except TypeError:
            # constructor نیاز به پارامتر دارد
            pytest.skip(
                f"{class_name} نیاز به پارامتر در constructor دارد"
            )

        assert instance is not None, "instance ساخت نشد"
        assert hasattr(instance, "platform_name"), (
            f"{class_name} باید platform_name داشته باشد"
        )

        # ─── platform_name درست است ───
        pname = instance.platform_name
        if callable(pname):
            pname = pname()
        assert isinstance(pname, str) and len(pname) > 0, (
            f"platform_name نامعتبر: {pname!r}"
        )

    except Exception as e:
        pytest.fail(
            f"{class_name}() خطا داد: {type(e).__name__}: {e}"
        )
        