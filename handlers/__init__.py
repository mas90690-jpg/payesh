"""
handlers/__init__.py
────────────────────
پکیج handlers — هندلرهای ربات (ذخیره، admin، ChatOps).

هندلرها:
  - StorageHandler    → ذخیرهٔ پیام‌های کلیدواژه‌دار به‌عنوان سرنخ.
  - AdminHandler      → ChatOps از چت خصوصی مدیر (تحلیل، مدیریت).
  - AdminActions      → اجرای دستورهای مدیر (report, crm, set_param).
  - AdminMemory       → حافظهٔ گفتگو برای context AI.
  - KeywordAdmin      → CRUD کلیدواژه‌های سفارشی.
  - AutoSubscriber    → ثبت خودکار هندلرها در EventBus.

نحوه استفاده:
    from handlers import StorageHandler, AdminHandler
    from handlers.admin_actions import AdminActions
    from handlers.keyword_admin import KeywordAdmin
    from handlers.auto_subscribe import AutoSubscriber
"""
from __future__ import annotations

from handlers.admin_actions import AdminActions

# ═══════════════════════════════════════════════════════════
# Admin / ChatOps
# ═══════════════════════════════════════════════════════════
from handlers.admin_handler import AdminHandler
from handlers.admin_memory import AdminMemory

# ═══════════════════════════════════════════════════════════
# Auto-subscribe
# ═══════════════════════════════════════════════════════════
from handlers.auto_subscribe import AutoSubscriber
from handlers.keyword_admin import KeywordAdmin

# ═══════════════════════════════════════════════════════════
# Storage — ذخیرهٔ سرنخ
# ═══════════════════════════════════════════════════════════
from handlers.storage_handler import StorageHandler

__all__: list[str] = [
    "AdminActions",
    # Admin
    "AdminHandler",
    "AdminMemory",
    # Auto-subscribe
    "AutoSubscriber",
    "KeywordAdmin",
    # Storage
    "StorageHandler",
]
