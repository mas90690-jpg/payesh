"""کارخانهٔ ساخت دیتابیس."""
from __future__ import annotations

from config import (
    DATABASE_TYPE,
    POSTGRES_CONFIG,
    SQLITE_CONFIG,
)
from core.database.base import BaseDatabase
from core.database.postgres import PostgresDatabase
from core.database.sqlite import SQLiteDatabase
from core.kernel.logger import log_info


def create_database() -> BaseDatabase:
    """ساخت instance بر اساس DATABASE_TYPE در .env."""
    if DATABASE_TYPE == "postgres":
        log_info(
            f"🐘 [DB] انتخاب Postgres | "
            f"host={POSTGRES_CONFIG.get('host')}:"
            f"{POSTGRES_CONFIG.get('port')} "
            f"db={POSTGRES_CONFIG.get('database')}"
        )
        return PostgresDatabase(
            host=POSTGRES_CONFIG["host"],
            port=POSTGRES_CONFIG["port"],
            user=POSTGRES_CONFIG["user"],
            password=POSTGRES_CONFIG["password"],
            database=POSTGRES_CONFIG["database"],
        )
    log_info(
        f"📁 [DB] انتخاب SQLite | "
        f"path={SQLITE_CONFIG['database_path']}"
    )
    return SQLiteDatabase(SQLITE_CONFIG["database_path"])
