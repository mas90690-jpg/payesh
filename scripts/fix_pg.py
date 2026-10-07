"""Create the lead_monitor database if missing (idempotent).

🔒 SEC FIX:
  - نام دیتابیس اعتبارسنجی می‌شود (regex `[A-Za-z_][A-Za-z0-9_]*`)
    چون PostgreSQL برای `CREATE DATABASE` پارامتر bind نمی‌پذیرد.
  - `DB_PORT` با try/except + بازه معتبر parse می‌شود.
  - زمان انتظار برای اتصال به‌صورت ثابت `_CONNECT_TIMEOUT_S`.
"""
import asyncio
import os
import re

from dotenv import load_dotenv

load_dotenv()


# ─── ثابت‌ها ───
_CONNECT_TIMEOUT_S = 8
_DEFAULT_DB_NAME = "lead_monitor"
_DEFAULT_DB_PORT = 5432

# 🔒 اعتبارسنجی نام دیتابیس — فقط حروف/عدد/زیرخط، شروع با حرف یا زیرخط
_SAFE_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _safe_db_name(name: str) -> str:
    """اعتبارسنجی نام دیتابیس.

    PostgreSQL برای `CREATE DATABASE` پارامتر bind نمی‌پذیرد،
    پس ناچاریم نام را داخل SQL interpolate کنیم. این تابع
    تضمین می‌کند نام فقط کاراکترهای امن دارد.

    Raises:
        ValueError: اگر نام معتبر نباشد.
    """
    if not isinstance(name, str) or not _SAFE_DB_NAME_RE.match(name):
        raise ValueError(
            f"invalid DB_NAME: {name!r} — "
            f"must match [A-Za-z_][A-Za-z0-9_]{{0,62}}"
        )
    return name


def _safe_port(value: str) -> int:
    """parse امن DB_PORT با بازه معتبر (1..65535)."""
    try:
        port = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_DB_PORT
    if not (1 <= port <= 65535):
        return _DEFAULT_DB_PORT
    return port


async def main() -> None:
    import asyncpg

    db_name_raw = os.getenv("DB_NAME", _DEFAULT_DB_NAME)
    try:
        db_name = _safe_db_name(db_name_raw)
    except ValueError as e:
        print(f"❌ {e}")
        raise SystemExit(1)

    conn = await asyncio.wait_for(
        asyncpg.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=_safe_port(os.getenv("DB_PORT", str(_DEFAULT_DB_PORT))),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", ""),
            database="postgres",
        ),
        timeout=_CONNECT_TIMEOUT_S,
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname=$1",
            db_name,
        )
        if not exists:
            # 🔒 SEC FIX: db_name قبلاً با regex اعتبارسنجی شده
            await conn.execute(f'CREATE DATABASE "{db_name}"')
            print(f"Database created: {db_name}")
        else:
            print(f"Database already exists: {db_name}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())