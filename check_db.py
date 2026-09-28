import sqlite3
conn = sqlite3.connect('storage/database/messages.db')
cur = conn.cursor()

# نسخه schema
try:
    pragma = cur.execute("PRAGMA user_version").fetchone()[0]
    migrations = cur.execute("SELECT MAX(version) FROM migrations").fetchone()[0]
    print(f"user_version: v{pragma}")
    print(f"migrations: v{migrations}")
except Exception as e:
    print(f"خطا: {e}")

conn.close()