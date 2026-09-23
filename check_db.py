import json

with open('storage/logs/app.jsonl', encoding='utf-8') as f:
    lines = f.readlines()

# فقط ۵۰ خط آخر
for line in lines[-50:]:
    try:
        o = json.loads(line)
        msg = o.get('msg', '') or ''
        ts = o.get('ts', '')[:19]
        lvl = o.get('level', '')

        # فقط پیام‌های روبیکا و STORAGE
        if ('STORAGE' in msg or 'روبیکا' in msg or 'rubika' in msg.lower()
            or 'AdminHandler' in msg or 'handle' in msg.lower()):
            # حذف کاراکترهای غیرقابل‌چاپ
            msg_clean = ''.join(c for c in msg if c.isprintable() or c == ' ')
            print(f"{ts} | {lvl:8} | {msg_clean[:180]}")
    except Exception as e:
        pass