"""پکیج database — دیتابیس SQLite و Postgres."""
from core.database.base import BaseDatabase, _QueryResult
from core.database.factory import create_database
from core.database.postgres import PostgresDatabase
from core.database.sqlite import SQLiteDatabase

__all__ = [
    "BaseDatabase",
    "_QueryResult",
    "SQLiteDatabase",
    "PostgresDatabase",
    "create_database",
]
