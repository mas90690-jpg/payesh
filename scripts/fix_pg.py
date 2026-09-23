"""Create the lead_monitor database if missing (idempotent)."""
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    import asyncpg

    conn = await asyncio.wait_for(
        asyncpg.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", ""),
            database="postgres",
        ),
        timeout=8,
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname=$1", os.getenv("DB_NAME", "lead_monitor")
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{os.getenv("DB_NAME", "lead_monitor")}"')
            print("Database created.")
        else:
            print("Database already exists.")
    finally:
        await conn.close()


asyncio.run(main())
