import sqlite3

conn = sqlite3.connect("storage/database/messages.db")
cur = conn.cursor()

# تعداد پیام‌های روبیکا بعد از 11:50
cur.execute(
    "SELECT COUNT(*) FROM messages "
    "WHERE platform='rubika' AND timestamp > '2026-09-27 11:50'"
)
count = cur.fetchone()[0]
print("تعداد پیام‌های روبیکا بعد از 11:50:", count)
print("=" * 60)

# آخرین ۱۰ پیام
cur.execute(
    "SELECT chat_name, text, timestamp FROM messages "
    "WHERE platform='rubika' AND timestamp > '2026-09-27 11:50' "
    "ORDER BY timestamp DESC LIMIT 10"
)
for r in cur.fetchall():
    print(f"[{r[2]}] {r[0]}: {(r[1] or '')[:70]}")

conn.close()