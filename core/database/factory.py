"""کارخانهٔ ساخت دیتابیس.

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX: دسترسی به `POSTGRES_CONFIG` حالا با `.get()` و fallback
    (قبلاً KeyError می‌داد اگر کلیدی نبود).
  - 🐛 FIX: `DATABASE_TYPE` ناشناخته حالا warning می‌دهد (قبلاً
    بی‌صدا SQLite برمی‌گرداند).
  - 🐛 FIX: هشدار در صورت خالی بودن `DB_PASSWORD` برای Postgres.
  - 🐛 FIX: مسیر SQLite حالا absolute می‌شود (نسبی → مطلق).
  - 🎯 لاگ‌های دقیق‌تر با prefix `[DB:factory:*]`.
  - 🎯 type hints صریح.
  - 🎯 `__all__` export.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۱.
"""
from __future__ import annotations

import os
from typing import Any

from config import (
    DATABASE_TYPE,
    POSTGRES_CONFIG,
    SQLITE_CONFIG,
)
from core.database.base import BaseDatabase
from core.database.postgres import PostgresDatabase
from core.database.sqlite import SQLiteDatabase
from core.kernel.logger import log_info, log_warning


# ═══════════════════════════════════════════
# ثابت‌ها
# ═══════════════════════════════════════════
_VALID_TYPES = ("sqlite", "postgres")


def create_database() -> BaseDatabase:
    """ساخت instance بر اساس `DATABASE_TYPE` در `.env`.

    Returns:
        BaseDatabase: SQLiteDatabase یا PostgresDatabase.

    Raises:
        ValueError: اگر `DATABASE_TYPE` نامعتبر باشد.
    """
    db_type = (DATABASE_TYPE or "sqlite").strip().lower()

    # 🐛 FIX: چک نوع نامعتبر
    if db_type not in _VALID_TYPES:
        log_warning(
            f"⚠️ [DB:factory] DATABASE_TYPE نامعتبر | "
            f"value={DATABASE_TYPE!r} | "
            f"معتبر={list(_VALID_TYPES)} — "
            f"fallback به sqlite"
        )
        db_type = "sqlite"

    # ═══════════════════════════════════════════
    # Postgres
    # ═══════════════════════════════════════════
    if db_type == "postgres":
        host = POSTGRES_CONFIG.get("host", "localhost")
        port = POSTGRES_CONFIG.get("port", 5432)
        user = POSTGRES_CONFIG.get("user", "postgres")
        password = POSTGRES_CONFIG.get("password", "")  # noqa: SEC-HARDCODED-SECRET
        database = POSTGRES_CONFIG.get("database", "lead_monitor")

        # 🐛 FIX: هشدار در صورت خالی بودن password
        if not password:
            log_warning(
                "⚠️ [DB:factory] DB_PASSWORD خالی است — "
                "اتصال Postgres ممکن است شکست بخورد"
            )

        log_info(
            f"🐘 [DB:factory] انتخاب Postgres | "
            f"host={host}:{port} | "
            f"db={database} | "
            f"user={user} | "
            f"password={'✅' if password else '❌'}"
        )

        try:
            return PostgresDatabase(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
            )
        except Exception as e:
            log_warning(
                f"❌ [DB:factory] Postgres ساخت ناموفق | "
                f"{type(e).__name__}: {e} — "
                f"fallback به sqlite"
            )
            db_type = "sqlite"

    # ═══════════════════════════════════════════
    # SQLite
    # ═══════════════════════════════════════════
    if db_type == "sqlite":
        db_path: str = SQLITE_CONFIG.get(
            "database_path", ""
        ) or "storage/database/messages.db"

        # 🐛 FIX: absolute path
        if not os.path.isabs(db_path):
            try:
                from config import BASE_DIR
                db_path = os.path.join(str(BASE_DIR), db_path)
            except Exception:
                db_path = os.path.abspath(db_path)

        log_info(
            f"📁 [DB:factory] انتخاب SQLite | "
            f"path={db_path}"
        )

        # اطمینان از وجود پوشهٔ parent
        try:
            parent = os.path.dirname(db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        except Exception as e:
            log_warning(
                f"⚠️ [DB:factory] makedirs | "
                f"{type(e).__name__}: {e}"
            )

        return SQLiteDatabase(db_path)

    # ─── نماد غیرقابل‌دسترس (نباید اجرا شود) ───
    raise ValueError(
        f"⛔ DATABASE_TYPE ناشناخته: {DATABASE_TYPE!r}"
    )


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = ["create_database"]