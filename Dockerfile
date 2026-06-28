# ======================================================================
#  Dockerfile — سیستم پایش هوشمند سرنخ
#  ایمیج پایه‌ی Playwright مرورگر chromium را از قبل دارد (برای ایتا/واتساپ).
# ======================================================================
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

# نصب وابستگی‌ها (اول فقط requirements تا کش داکر بهتر کار کند)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

# کد پروژه
COPY . .

# همهٔ فایل‌های تولیدی اینجا می‌مانند (با volume بیرون نگه‌دار)
VOLUME ["/app/storage"]

# اجرای بی‌وقفه؛ خروج با سیگنال
ENV PYTHONUNBUFFERED=1
CMD ["python", "main.py"]
