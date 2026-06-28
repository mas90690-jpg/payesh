# راهنمای استقرار روی سرور (Deployment)

این سیستم به‌عنوان **کاربر** پیام‌رسان‌ها را می‌خواند. سرور باید **داخل ایران** باشد
(بله/روبیکا/ایتا با IP خارج معمولاً بلاک می‌شوند). واتساپ ممکن است VPN بخواهد.

---

## 🔑 مرحلهٔ صفر و حیاتی: ورود (یک‌بار)

ورود هر پیام‌رسان **تعاملی** است (کد پیامک یا QR) و باید **یک‌بار با دست** انجام شود.
بهترین راه: روی یک کامپیوتر با صفحه‌نمایش این را اجرا کن:

```bash
python login.py
#  [1] روبیکا  → کد پیامک
#  [2] بله     → کد پیامک
#  [3] ایتا    → مرورگر باز می‌شود، با شماره+کد وارد شو
#  [4] تلگرام  → کد پیامک
#  [5] واتساپ  → مرورگر باز می‌شود، QR را با گوشی اسکن کن
```

سپس **کل پوشهٔ `storage/sessions/` را روی سرور کپی کن**. بعد از آن سرور بی‌صدا و بدون
صفحه‌نمایش (headless) کار می‌کند.

> نشست‌های روبیکا/بله/تلگرام (فایل‌های session) کاملاً قابل‌انتقال‌اند.
> نشست‌های مرورگری (ایتا/واتساپ = پروفایل مرورگر) را هم کپی کن؛ اگر کار نکرد، یک‌بار
> روی خود سرور (با VNC/صفحه) لاگین کن.

---

## گزینه ۱ — لینوکس + Docker (پیشنهادی)

```bash
# ۱) نصب docker و docker compose
# ۲) فایل‌ها را روی سرور بگذار، .env را پر کن، storage/sessions را کپی کن
docker compose up -d --build      # ساخت و اجرا
docker compose logs -f            # دیدن لاگ زنده
docker compose down               # توقف
```
ری‌استارت خودکار (`restart: unless-stopped`) فعال است — با ری‌بوت سرور هم بالا می‌آید.

> 🇮🇷 نکتهٔ ایران: کشیدن ایمیج و `pip install` ممکن است نیاز به **میرور ایرانی** داشته باشد
> (مثلاً تنظیم registry mirror داکر و pip index ایرانی). اگر دانلود گیر کرد، میرور بگذار.

---

## گزینه ۲ — لینوکس بدون Docker (systemd)

```bash
sudo mkdir -p /opt/srooyesh && sudo cp -r . /opt/srooyesh && cd /opt/srooyesh
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python -m playwright install chromium
./venv/bin/python -m playwright install-deps    # وابستگی‌های سیستمی مرورگر

sudo cp deploy/srooyesh.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now srooyesh
journalctl -u srooyesh -f          # لاگ زنده
```

---

## گزینه ۳ — ویندوز (VPS یا PC همیشه‌روشن)

```bat
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python -m playwright install chromium

run_server.bat        :: اجرای دائم با ری‌استارت خودکار
```
برای اجرای خودکار هنگام روشن‌شدن ویندوز: `run_server.bat` را در Task Scheduler با
تریگر «At startup» بگذار.

---

## ⚙️ تنظیمات سرور (در `.env`)
- پیام‌رسان‌های لازم را `..._ENABLED=true` کن.
- `MONITOR_INTERVAL_SECONDS` و آستانه‌ها برای کم‌کردن نویز تنظیم شده‌اند.
- نگه‌داری فایل/گزارش با `KEEP_MEDIA_DAYS` و `KEEP_REPORTS_DAYS`.
- برای دیتابیس بزرگ: `DATABASE_TYPE=postgres` + پر کردن `DB_*`.

## 📂 داده‌های ماندگار
همه‌چیز در `storage/` است (دیتابیس، نشست‌ها، گزارش‌ها، مدیا، لاگ، بکاپ).
از این پوشه به‌صورت دوره‌ای backup بگیر (سرویس بکاپ خودکار هم فعال است).

## ⚠️ محدودیت‌های صادقانه
- **ایتا/واتساپ مرورگری‌اند**: روی سرور headless کار می‌کنند ولی ممکن است گاهی
  انتخابگرها یا تشخیص ربات نیاز به تنظیم داشته باشند؛ سبک‌تر از پروتکل نیستند.
- **واتساپ در ایران**: نیاز به VPN/پراکسی پایدار دارد.
- **یک نمونه در هر زمان**: هر نشست را فقط روی یک دستگاه اجرا کن (ورود هم‌زمان دو جا
  باعث قطع نشست می‌شود).
