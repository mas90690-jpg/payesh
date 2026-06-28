# core/database.py (نسخه نهایی - بهینه و آماده مهاجرت)
import os
import json
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager

# ==================================================
# تنظیمات دیتابیس
# ==================================================
from config import DATABASE_TYPE, SQLITE_CONFIG, POSTGRES_CONFIG, DB_DIR

# ==================================================
# کلاس پایه انتزاعی
# ==================================================

class BaseDatabase(ABC):
    """کلاس پایه برای همه دیتابیس‌ها"""
    
    @abstractmethod
    async def init(self):
        """ایجاد جداول و ایندکس‌ها"""
        pass
    
    @abstractmethod
    async def migrate(self):
        """مهاجرت خودکار دیتابیس (اضافه کردن ستون‌های جدید)"""
        pass
    
    @abstractmethod
    async def save_message(self, message: Dict[str, Any]) -> int:
        """ذخیره پیام"""
        pass
    
    @abstractmethod
    async def save_lead(self, lead: Dict[str, Any]) -> int:
        """ذخیره سرنخ"""
        pass
    
    @abstractmethod
    async def get_last_poll(self, platform: str) -> Optional[datetime]:
        pass
    
    @abstractmethod
    async def update_last_poll(self, platform: str, timestamp: datetime):
        pass
    
    @abstractmethod
    async def get_high_score_leads(self, min_score: int = 0, limit: int = 50) -> List[Dict]:
        pass
    
    @abstractmethod
    async def get_leads_by_date_range(self, start_date: str, end_date: str) -> List[Dict]:
        """دریافت سرنخ‌ها در بازه زمانی"""
        pass
    
    @abstractmethod
    async def get_keyword_stats(self, limit: int = 20) -> List[Tuple[str, int]]:
        """آمار کلیدواژه‌ها"""
        pass
    
    @abstractmethod
    async def get_group_stats(self, limit: int = 20) -> List[Tuple[str, int]]:
        """آمار گروه‌ها"""
        pass
    
    @abstractmethod
    async def get_daily_stats(self, days: int = 7) -> List[Dict]:
        """آمار روزانه برای نمودار"""
        pass
    
    @abstractmethod
    async def search(self, query: str, search_type: str = "all", limit: int = 50) -> List[Dict]:
        """جستجوی پیشرفته"""
        pass
    
    @abstractmethod
    async def mark_important(self, message_id: str, note: str = None):
        pass
    
    @abstractmethod
    async def get_important_messages(self, limit: int = 50) -> List[Dict]:
        pass
    
    @abstractmethod
    async def update_lead_score(self, lead_id: str, score: int):
        """به‌روزرسانی امتیاز سرنخ"""
        pass
    
    @abstractmethod
    async def update_daily_stats(self):
        """به‌روزرسانی آمار روزانه"""
        pass
    
    @abstractmethod
    async def get_total_count(self) -> Dict[str, int]:
        """دریافت تعداد کل رکوردها"""
        pass
    
    @abstractmethod
    async def delete_old_messages(self, days: int = 30):
        """حذف پیام‌های قدیمی (اختیاری)"""
        pass
    
    @abstractmethod
    async def backup(self, backup_path: str) -> bool:
        """پشتیبان‌گیری از دیتابیس"""
        pass
    
    @abstractmethod
    async def close(self):
        pass


# ==================================================
# پیاده‌سازی SQLite (بهینه)
# ==================================================

class SQLiteDatabase(BaseDatabase):
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._connection = None
        self._current_version = 5
        
    @asynccontextmanager
    async def _get_conn(self):
        import aiosqlite
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys = ON")
            await conn.execute("PRAGMA journal_mode = WAL")  # بهبود عملکرد
            await conn.execute("PRAGMA synchronous = NORMAL")  # تعادل بین سرعت و امنیت
            yield conn

    async def init(self):
        """ایجاد جداول و ایندکس‌ها"""
        async with self._get_conn() as conn:
            # ========== جدول messages ==========
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    platform_message_id TEXT,
                    text TEXT,
                    text_translated TEXT,
                    chat_id TEXT,
                    chat_name TEXT,
                    chat_username TEXT,
                    chat_link TEXT,
                    chat_type TEXT,
                    user_id TEXT,
                    sender_name TEXT,
                    timestamp TIMESTAMP,
                    keywords TEXT,
                    media_type TEXT DEFAULT 'text',
                    file_id TEXT,
                    local_file_path TEXT,
                    caption TEXT,
                    reply_to TEXT,
                    opportunity_score INTEGER DEFAULT 0,
                    opportunity_type TEXT,
                    is_important INTEGER DEFAULT 0,
                    my_note TEXT,
                    raw_data TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # ========== جدول leads ==========
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS leads (
                    id TEXT PRIMARY KEY,
                    message_id TEXT,
                    platform TEXT NOT NULL,
                    text TEXT,
                    chat_id TEXT,
                    chat_name TEXT,
                    user_id TEXT,
                    timestamp TIMESTAMP,
                    keywords TEXT,
                    media_type TEXT DEFAULT 'text',
                    file_id TEXT,
                    opportunity_score INTEGER DEFAULT 0,
                    opportunity_type TEXT,
                    is_followed_up INTEGER DEFAULT 0,
                    follow_up_note TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
                )
            """)
            
            # ========== جدول last_poll ==========
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS last_poll (
                    platform TEXT PRIMARY KEY,
                    last_poll TIMESTAMP,
                    last_message_id TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # ========== جدول daily_stats ==========
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE UNIQUE,
                    total_messages INTEGER DEFAULT 0,
                    total_leads INTEGER DEFAULT 0,
                    unique_groups INTEGER DEFAULT 0,
                    unique_keywords INTEGER DEFAULT 0,
                    avg_score REAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # ========== جدول migrations ==========
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version INTEGER UNIQUE,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # ========== ایندکس‌ها (برای سرعت بالا) ==========
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_keywords ON messages(keywords)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_platform ON messages(platform)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_name, timestamp)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(opportunity_score DESC)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_timestamp ON leads(timestamp)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON daily_stats(date)")
            
            await conn.commit()
        
        # اجرای مهاجرت‌های لازم
        await self.migrate()
        print("✅ دیتابیس SQLite راه‌اندازی شد (با ایندکس‌های بهینه)")

    async def migrate(self):
        """مهاجرت خودکار دیتابیس"""
        async with self._get_conn() as conn:
            # بررسی نسخه فعلی
            cursor = await conn.execute("SELECT version FROM migrations ORDER BY version DESC LIMIT 1")
            row = await cursor.fetchone()
            current_version = row[0] if row else 0
            
            if current_version < 1:
                try:
                    await conn.execute("ALTER TABLE messages ADD COLUMN raw_data TEXT")
                    await conn.execute("INSERT OR IGNORE INTO migrations (version) VALUES (1)")
                    print("✅ migration 1: اضافه شدن raw_data")
                except:
                    pass
            
            if current_version < 2:
                try:
                    await conn.execute("ALTER TABLE last_poll ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                    await conn.execute("INSERT OR IGNORE INTO migrations (version) VALUES (2)")
                    print("✅ migration 2: اضافه شدن updated_at به last_poll")
                except:
                    pass

            if current_version < 3:
                # ستون‌های آدرس دقیق گروه: یوزرنیم، لینک و نوع چت
                for col in ("chat_username", "chat_link", "chat_type"):
                    try:
                        await conn.execute(f"ALTER TABLE messages ADD COLUMN {col} TEXT")
                    except Exception:
                        pass
                await conn.execute("INSERT OR IGNORE INTO migrations (version) VALUES (3)")
                print("✅ migration 3: اضافه شدن chat_username/chat_link/chat_type به messages")

            if current_version < 4:
                # مسیر فایل دانلودشده (عکس/فایل ذخیره‌شده روی دیسک)
                try:
                    await conn.execute("ALTER TABLE messages ADD COLUMN local_file_path TEXT")
                except Exception:
                    pass
                await conn.execute("INSERT OR IGNORE INTO migrations (version) VALUES (4)")
                print("✅ migration 4: اضافه شدن local_file_path به messages")

            if current_version < 5:
                # ترجمهٔ فارسی محتوا (متن اصلی در text، ترجمه در text_translated)
                try:
                    await conn.execute("ALTER TABLE messages ADD COLUMN text_translated TEXT")
                except Exception:
                    pass
                await conn.execute("INSERT OR IGNORE INTO migrations (version) VALUES (5)")
                print("✅ migration 5: اضافه شدن text_translated به messages")

            await conn.commit()

    async def save_message(self, message: Dict[str, Any]) -> int:
        async with self._get_conn() as conn:
            cursor = await conn.execute("""
                INSERT OR IGNORE INTO messages (
                    id, platform, platform_message_id, text, text_translated, chat_id, chat_name,
                    chat_username, chat_link, chat_type,
                    user_id, sender_name, timestamp, keywords, media_type,
                    file_id, local_file_path, caption, reply_to, opportunity_score, raw_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                message.get('id'), message.get('platform'), message.get('platform_message_id'),
                message.get('text', '')[:5000], message.get('text_translated'),
                message.get('chat_id'), message.get('chat_name', 'Unknown'),
                message.get('chat_username'), message.get('chat_link'), message.get('chat_type'),
                message.get('user_id'), message.get('sender_name'), message.get('timestamp'),
                message.get('keywords', ''), message.get('media_type', 'text'),
                message.get('file_id'), message.get('local_file_path'),
                message.get('caption'), message.get('reply_to'),
                message.get('opportunity_score', 0),
                json.dumps(message.get('raw_data', {}), ensure_ascii=False)[:10000]
            ))
            await conn.commit()
            return cursor.lastrowid

    async def save_lead(self, lead: Dict[str, Any]) -> int:
        async with self._get_conn() as conn:
            cursor = await conn.execute("""
                INSERT OR IGNORE INTO leads (
                    id, message_id, platform, text, chat_id, chat_name, user_id,
                    timestamp, keywords, media_type, file_id, 
                    opportunity_score, opportunity_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                lead.get('id'), lead.get('message_id'), lead.get('platform'),
                lead.get('text', '')[:5000], lead.get('chat_id'), lead.get('chat_name', 'Unknown'),
                lead.get('user_id'), lead.get('timestamp'), lead.get('keywords', ''),
                lead.get('media_type', 'text'), lead.get('file_id'),
                lead.get('opportunity_score', 0), lead.get('opportunity_type')
            ))
            await conn.commit()
            
            # به‌روزرسانی آمار روزانه (در پس‌زمینه)
            asyncio.create_task(self.update_daily_stats())
            
            return cursor.lastrowid

    async def get_last_poll(self, platform: str) -> Optional[datetime]:
        async with self._get_conn() as conn:
            cursor = await conn.execute("SELECT last_poll FROM last_poll WHERE platform = ?", (platform,))
            row = await cursor.fetchone()
            if row and row[0]:
                return datetime.fromisoformat(row[0])
        return None

    async def update_last_poll(self, platform: str, timestamp: datetime):
        async with self._get_conn() as conn:
            await conn.execute("""
                INSERT OR REPLACE INTO last_poll (platform, last_poll, updated_at) 
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (platform, timestamp.isoformat()))
            await conn.commit()

    async def get_high_score_leads(self, min_score: int = 0, limit: int = 50) -> List[Dict]:
        async with self._get_conn() as conn:
            cursor = await conn.execute("""
                SELECT * FROM leads 
                WHERE opportunity_score >= ? 
                ORDER BY opportunity_score DESC, timestamp DESC 
                LIMIT ?
            """, (min_score, limit))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_leads_by_date_range(self, start_date: str, end_date: str) -> List[Dict]:
        async with self._get_conn() as conn:
            cursor = await conn.execute("""
                SELECT * FROM messages 
                WHERE date(timestamp) BETWEEN ? AND ? AND keywords != ''
                ORDER BY timestamp DESC
            """, (start_date, end_date))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_keyword_stats(self, limit: int = 20) -> List[Tuple[str, int]]:
        async with self._get_conn() as conn:
            # تجزیه کلیدواژه‌ها (ممکن است چندتا با کاما جدا شده باشند)
            cursor = await conn.execute("""
                SELECT keywords FROM messages WHERE keywords != ''
            """)
            rows = await cursor.fetchall()
            
            keyword_count = {}
            for row in rows:
                keywords = row[0].split(',')
                for kw in keywords:
                    kw = kw.strip()
                    if kw:
                        keyword_count[kw] = keyword_count.get(kw, 0) + 1
            
            sorted_keywords = sorted(keyword_count.items(), key=lambda x: x[1], reverse=True)
            return sorted_keywords[:limit]

    async def get_group_stats(self, limit: int = 20) -> List[Tuple[str, int]]:
        async with self._get_conn() as conn:
            cursor = await conn.execute("""
                SELECT chat_name, COUNT(*) as cnt 
                FROM messages 
                WHERE keywords != '' 
                GROUP BY chat_name 
                ORDER BY cnt DESC 
                LIMIT ?
            """, (limit,))
            rows = await cursor.fetchall()
            return [(row[0], row[1]) for row in rows]

    async def get_daily_stats(self, days: int = 7) -> List[Dict]:
        async with self._get_conn() as conn:
            cursor = await conn.execute("""
                SELECT date(timestamp) as date, COUNT(*) as count
                FROM messages 
                WHERE keywords != '' AND date(timestamp) >= date('now', ?)
                GROUP BY date(timestamp)
                ORDER BY date DESC
            """, (f'-{days} days',))
            rows = await cursor.fetchall()
            return [{"date": row[0], "count": row[1]} for row in rows]

    async def search(self, query: str, search_type: str = "all", limit: int = 50) -> List[Dict]:
        async with self._get_conn() as conn:
            if search_type == "keyword":
                cursor = await conn.execute("""
                    SELECT id, platform, chat_name, text, keywords, timestamp 
                    FROM messages 
                    WHERE keywords LIKE ?
                    ORDER BY timestamp DESC 
                    LIMIT ?
                """, (f"%{query}%", limit))
            elif search_type == "text":
                cursor = await conn.execute("""
                    SELECT id, platform, chat_name, text, keywords, timestamp 
                    FROM messages 
                    WHERE text LIKE ?
                    ORDER BY timestamp DESC 
                    LIMIT ?
                """, (f"%{query}%", limit))
            else:
                cursor = await conn.execute("""
                    SELECT id, platform, chat_name, text, keywords, timestamp 
                    FROM messages 
                    WHERE text LIKE ? OR keywords LIKE ?
                    ORDER BY timestamp DESC 
                    LIMIT ?
                """, (f"%{query}%", f"%{query}%", limit))
            
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def mark_important(self, message_id: str, note: str = None):
        async with self._get_conn() as conn:
            if note:
                await conn.execute(
                    "UPDATE messages SET is_important = 1, my_note = ? WHERE id = ?",
                    (note, message_id)
                )
            else:
                await conn.execute(
                    "UPDATE messages SET is_important = 1 WHERE id = ?",
                    (message_id,)
                )
            await conn.commit()

    async def get_important_messages(self, limit: int = 50) -> List[Dict]:
        async with self._get_conn() as conn:
            cursor = await conn.execute("""
                SELECT * FROM messages 
                WHERE is_important = 1 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (limit,))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def update_lead_score(self, lead_id: str, score: int):
        async with self._get_conn() as conn:
            await conn.execute(
                "UPDATE leads SET opportunity_score = ? WHERE id = ?",
                (score, lead_id)
            )
            await conn.commit()

    async def update_daily_stats(self):
        """به‌روزرسانی آمار روزانه"""
        today = datetime.now().strftime("%Y-%m-%d")
        async with self._get_conn() as conn:
            # بررسی وجود دارد یا نه
            cursor = await conn.execute("SELECT id FROM daily_stats WHERE date = ?", (today,))
            if await cursor.fetchone():
                return
            
            # محاسبه آمار
            cursor = await conn.execute("""
                SELECT 
                    COUNT(*) as total_messages,
                    SUM(CASE WHEN keywords != '' THEN 1 ELSE 0 END) as total_leads,
                    COUNT(DISTINCT chat_name) as unique_groups
                FROM messages 
                WHERE date(timestamp) = ?
            """, (today,))
            stats = await cursor.fetchone()
            
            # کلیدواژه‌های منحصر به فرد
            cursor = await conn.execute("""
                SELECT keywords FROM messages 
                WHERE date(timestamp) = ? AND keywords != ''
            """, (today,))
            rows = await cursor.fetchall()
            keywords_set = set()
            for row in rows:
                for kw in row[0].split(','):
                    if kw.strip():
                        keywords_set.add(kw.strip())
            
            await conn.execute("""
                INSERT INTO daily_stats (date, total_messages, total_leads, unique_groups, unique_keywords)
                VALUES (?, ?, ?, ?, ?)
            """, (today, stats[0], stats[1], stats[2], len(keywords_set)))
            await conn.commit()

    async def get_total_count(self) -> Dict[str, int]:
        async with self._get_conn() as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM messages")
            total_messages = (await cursor.fetchone())[0]
            
            cursor = await conn.execute("SELECT COUNT(*) FROM messages WHERE keywords != ''")
            total_leads = (await cursor.fetchone())[0]
            
            cursor = await conn.execute("SELECT COUNT(DISTINCT chat_name) FROM messages")
            total_groups = (await cursor.fetchone())[0]
            
            return {
                "messages": total_messages,
                "leads": total_leads,
                "groups": total_groups
            }

    async def delete_old_messages(self, days: int = 30):
        """حذف پیام‌های قدیمی‌تر از days روز"""
        cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
        async with self._get_conn() as conn:
            await conn.execute("DELETE FROM messages WHERE timestamp < ?", (cutoff_date,))
            await conn.commit()

    async def backup(self, backup_path: str) -> bool:
        """پشتیبان‌گیری از دیتابیس"""
        import shutil
        try:
            shutil.copy2(self.db_path, backup_path)
            return True
        except Exception as e:
            print(f"خطا در پشتیبان‌گیری: {e}")
            return False

    async def execute(self, query: str, params=()):
        async with self._get_conn() as conn:
            cursor = await conn.execute(query, params)
            await conn.commit()
            return cursor

    async def close(self):
        if self._connection:
            await self._connection.close()
            self._connection = None


# ==================================================
# تابع create_database (با قابلیت انتخاب نوع دیتابیس)
# ==================================================

def create_database():
    """ساخت instance دیتابیس بر اساس تنظیمات config.py"""
    
    if DATABASE_TYPE == "postgres":
        print("🐘 استفاده از PostgreSQL")
        from config import POSTGRES_CONFIG
        return PostgresDatabase(
            host=POSTGRES_CONFIG["host"],
            port=POSTGRES_CONFIG["port"],
            user=POSTGRES_CONFIG["user"],
            password=POSTGRES_CONFIG["password"],
            database=POSTGRES_CONFIG["database"]
        )
    else:
        print("📁 استفاده از SQLite (بهینه شده)")
        return SQLiteDatabase(SQLITE_CONFIG["database_path"])


# ==================================================
# کلاس PostgreSQL (برای مهاجرت آینده)
# ==================================================

class PostgresDatabase(BaseDatabase):
    """پیاده‌سازی کامل PostgreSQL (با asyncpg) — هم‌ارز SQLite.

    برای استفاده: در .env بزن DATABASE_TYPE=postgres و اطلاعات DB_HOST/DB_USER/... را پر کن،
    و یک دیتابیس خالی در Postgres بساز. جداول خودکار ساخته می‌شوند.
    """

    def __init__(self, host: str, port: int, user: str, password: str, database: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self._pool = None

    @staticmethod
    def _ts(value):
        """timestamp را به datetime تبدیل می‌کند (Postgres نوع TIMESTAMP می‌خواهد)."""
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except Exception:
                return datetime.now()
        return datetime.now()

    async def init(self):
        import asyncpg
        self._pool = await asyncpg.create_pool(
            host=self.host, port=self.port, user=self.user,
            password=self.password, database=self.database,
            min_size=1, max_size=10,
        )
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    platform_message_id TEXT,
                    text TEXT,
                    text_translated TEXT,
                    chat_id TEXT,
                    chat_name TEXT,
                    chat_username TEXT,
                    chat_link TEXT,
                    chat_type TEXT,
                    user_id TEXT,
                    sender_name TEXT,
                    timestamp TIMESTAMP,
                    keywords TEXT,
                    media_type TEXT DEFAULT 'text',
                    file_id TEXT,
                    local_file_path TEXT,
                    caption TEXT,
                    reply_to TEXT,
                    opportunity_score INTEGER DEFAULT 0,
                    opportunity_type TEXT,
                    is_important INTEGER DEFAULT 0,
                    my_note TEXT,
                    raw_data TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS leads (
                    id TEXT PRIMARY KEY,
                    message_id TEXT,
                    platform TEXT NOT NULL,
                    text TEXT,
                    chat_id TEXT,
                    chat_name TEXT,
                    user_id TEXT,
                    timestamp TIMESTAMP,
                    keywords TEXT,
                    media_type TEXT DEFAULT 'text',
                    file_id TEXT,
                    opportunity_score INTEGER DEFAULT 0,
                    opportunity_type TEXT,
                    is_followed_up INTEGER DEFAULT 0,
                    follow_up_note TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS last_poll (
                    platform TEXT PRIMARY KEY,
                    last_poll TIMESTAMP,
                    last_message_id TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    id SERIAL PRIMARY KEY,
                    date DATE UNIQUE,
                    total_messages INTEGER DEFAULT 0,
                    total_leads INTEGER DEFAULT 0,
                    unique_groups INTEGER DEFAULT 0,
                    unique_keywords INTEGER DEFAULT 0,
                    avg_score REAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for idx in (
                "CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_messages_keywords ON messages(keywords)",
                "CREATE INDEX IF NOT EXISTS idx_messages_platform ON messages(platform)",
                "CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_name, timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(opportunity_score DESC)",
                "CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON daily_stats(date)",
            ):
                await conn.execute(idx)
        print("✅ دیتابیس PostgreSQL راه‌اندازی شد")

    async def migrate(self):
        # init کل اسکیمای فعلی را می‌سازد؛ مهاجرت جداگانه لازم نیست
        return

    async def save_message(self, message: Dict[str, Any]) -> int:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO messages (
                    id, platform, platform_message_id, text, text_translated, chat_id, chat_name,
                    chat_username, chat_link, chat_type,
                    user_id, sender_name, timestamp, keywords, media_type,
                    file_id, local_file_path, caption, reply_to, opportunity_score, raw_data
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)
                ON CONFLICT (id) DO NOTHING
            """,
                message.get('id'), message.get('platform'), message.get('platform_message_id'),
                (message.get('text') or '')[:5000], message.get('text_translated'),
                message.get('chat_id'), message.get('chat_name', 'Unknown'),
                message.get('chat_username'), message.get('chat_link'), message.get('chat_type'),
                message.get('user_id'), message.get('sender_name'), self._ts(message.get('timestamp')),
                message.get('keywords', ''), message.get('media_type', 'text'),
                message.get('file_id'), message.get('local_file_path'),
                message.get('caption'), message.get('reply_to'),
                int(message.get('opportunity_score', 0) or 0),
                json.dumps(message.get('raw_data', {}), ensure_ascii=False)[:10000],
            )
        return 1

    async def save_lead(self, lead: Dict[str, Any]) -> int:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO leads (
                    id, message_id, platform, text, chat_id, chat_name, user_id,
                    timestamp, keywords, media_type, file_id, opportunity_score, opportunity_type
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                ON CONFLICT (id) DO NOTHING
            """,
                lead.get('id'), lead.get('message_id'), lead.get('platform'),
                (lead.get('text') or '')[:5000], lead.get('chat_id'), lead.get('chat_name', 'Unknown'),
                lead.get('user_id'), self._ts(lead.get('timestamp')), lead.get('keywords', ''),
                lead.get('media_type', 'text'), lead.get('file_id'),
                int(lead.get('opportunity_score', 0) or 0), lead.get('opportunity_type'),
            )
        asyncio.create_task(self.update_daily_stats())
        return 1

    async def get_last_poll(self, platform: str) -> Optional[datetime]:
        async with self._pool.acquire() as conn:
            val = await conn.fetchval("SELECT last_poll FROM last_poll WHERE platform=$1", platform)
            return val

    async def update_last_poll(self, platform: str, timestamp: datetime):
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO last_poll (platform, last_poll, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (platform) DO UPDATE SET last_poll=EXCLUDED.last_poll, updated_at=NOW()
            """, platform, self._ts(timestamp))

    async def get_high_score_leads(self, min_score: int = 0, limit: int = 50) -> List[Dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM leads WHERE opportunity_score >= $1
                ORDER BY opportunity_score DESC, timestamp DESC LIMIT $2
            """, min_score, limit)
            return [dict(r) for r in rows]

    async def get_leads_by_date_range(self, start_date: str, end_date: str) -> List[Dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM messages
                WHERE timestamp::date BETWEEN $1::date AND $2::date AND keywords <> ''
                ORDER BY timestamp DESC
            """, start_date, end_date)
            return [dict(r) for r in rows]

    async def get_keyword_stats(self, limit: int = 20) -> List[Tuple[str, int]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT keywords FROM messages WHERE keywords <> ''")
        counts: Dict[str, int] = {}
        for r in rows:
            for kw in (r[0] or '').split(','):
                kw = kw.strip()
                if kw:
                    counts[kw] = counts.get(kw, 0) + 1
        return sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]

    async def get_group_stats(self, limit: int = 20) -> List[Tuple[str, int]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT chat_name, COUNT(*) AS cnt FROM messages WHERE keywords <> ''
                GROUP BY chat_name ORDER BY cnt DESC LIMIT $1
            """, limit)
            return [(r[0], r[1]) for r in rows]

    async def get_daily_stats(self, days: int = 7) -> List[Dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT timestamp::date AS date, COUNT(*) AS count FROM messages
                WHERE keywords <> '' AND timestamp >= NOW() - ($1 || ' days')::interval
                GROUP BY timestamp::date ORDER BY date DESC
            """, str(days))
            return [{"date": str(r[0]), "count": r[1]} for r in rows]

    async def search(self, query: str, search_type: str = "all", limit: int = 50) -> List[Dict]:
        like = f"%{query}%"
        async with self._pool.acquire() as conn:
            if search_type == "keyword":
                rows = await conn.fetch("""
                    SELECT id, platform, chat_name, text, keywords, timestamp FROM messages
                    WHERE keywords ILIKE $1 ORDER BY timestamp DESC LIMIT $2
                """, like, limit)
            elif search_type == "text":
                rows = await conn.fetch("""
                    SELECT id, platform, chat_name, text, keywords, timestamp FROM messages
                    WHERE text ILIKE $1 ORDER BY timestamp DESC LIMIT $2
                """, like, limit)
            else:
                rows = await conn.fetch("""
                    SELECT id, platform, chat_name, text, keywords, timestamp FROM messages
                    WHERE text ILIKE $1 OR keywords ILIKE $1 ORDER BY timestamp DESC LIMIT $2
                """, like, limit)
            return [dict(r) for r in rows]

    async def mark_important(self, message_id: str, note: str = None):
        async with self._pool.acquire() as conn:
            if note:
                await conn.execute("UPDATE messages SET is_important=1, my_note=$1 WHERE id=$2", note, message_id)
            else:
                await conn.execute("UPDATE messages SET is_important=1 WHERE id=$1", message_id)

    async def get_important_messages(self, limit: int = 50) -> List[Dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM messages WHERE is_important=1 ORDER BY timestamp DESC LIMIT $1
            """, limit)
            return [dict(r) for r in rows]

    async def update_lead_score(self, lead_id: str, score: int):
        async with self._pool.acquire() as conn:
            await conn.execute("UPDATE leads SET opportunity_score=$1 WHERE id=$2", score, lead_id)

    async def update_daily_stats(self):
        today = datetime.now().date()
        async with self._pool.acquire() as conn:
            exists = await conn.fetchval("SELECT 1 FROM daily_stats WHERE date=$1", today)
            if exists:
                return
            row = await conn.fetchrow("""
                SELECT COUNT(*) AS total_messages,
                       SUM(CASE WHEN keywords <> '' THEN 1 ELSE 0 END) AS total_leads,
                       COUNT(DISTINCT chat_name) AS unique_groups
                FROM messages WHERE timestamp::date = $1
            """, today)
            krows = await conn.fetch("SELECT keywords FROM messages WHERE timestamp::date=$1 AND keywords <> ''", today)
            kset = set()
            for r in krows:
                for kw in (r[0] or '').split(','):
                    if kw.strip():
                        kset.add(kw.strip())
            await conn.execute("""
                INSERT INTO daily_stats (date, total_messages, total_leads, unique_groups, unique_keywords)
                VALUES ($1,$2,$3,$4,$5) ON CONFLICT (date) DO NOTHING
            """, today, row[0] or 0, row[1] or 0, row[2] or 0, len(kset))

    async def get_total_count(self) -> Dict[str, int]:
        async with self._pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM messages")
            leads = await conn.fetchval("SELECT COUNT(*) FROM messages WHERE keywords <> ''")
            groups = await conn.fetchval("SELECT COUNT(DISTINCT chat_name) FROM messages")
            return {"messages": total or 0, "leads": leads or 0, "groups": groups or 0}

    async def delete_old_messages(self, days: int = 30):
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM messages WHERE timestamp < NOW() - ($1 || ' days')::interval", str(days))

    async def backup(self, backup_path: str) -> bool:
        """پشتیبان‌گیری با pg_dump (در صورت نصب بودن)."""
        import asyncio as _a
        try:
            proc = await _a.create_subprocess_exec(
                "pg_dump", "-h", str(self.host), "-p", str(self.port),
                "-U", self.user, "-d", self.database, "-f", backup_path,
                env={**os.environ, "PGPASSWORD": self.password},
            )
            await proc.wait()
            return proc.returncode == 0
        except Exception as e:
            print(f"پشتیبان‌گیری Postgres نیاز به pg_dump دارد: {e}")
            return False

    async def execute(self, query: str, params=()):
        async with self._pool.acquire() as conn:
            return await conn.execute(query, *params)

    async def close(self):
        if self._pool:
            await self._pool.close()
            self._pool = None