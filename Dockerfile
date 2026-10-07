# ══════════════════════════════════════════════════════════════════
#  Srooyesh Lead-Monitor — Dockerfile
#  Base: Python 3.12 slim (کم‌حجم + سریع)
#  Playwright + Chromium داخل image نصب می‌شوند
# ══════════════════════════════════════════════════════════════════
FROM python:3.12-slim-bookworm

# ─── متغیرهای محیطی ───
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Tehran \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# ─── پکیج‌های سیستمی مورد نیاز Chromium + rubpy ───
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Chromium / Playwright dependencies
    ca-certificates \
    fonts-liberation \
    fonts-noto-color-emoji \
    fonts-noto-cjk \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libx11-6 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    # ابزارهای عمومی
    curl \
    git \
    tini \
    tzdata \
    # build tools (برای cryptography و rubpy)
    build-essential \
    gcc \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# ─── تنظیم timezone ───
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone

# ─── دایرکتوری کار ───
WORKDIR /app

# ─── کپی requirements اول (برای cache بهتر) ───
COPY requirements.txt ./

# ─── نصب پکیج‌های پایتون ───
RUN pip install --upgrade pip setuptools wheel \
    && pip install -r requirements.txt

# ─── نصب Playwright + Chromium ───
# نکته: فقط chromium (نه firefox/webkit) برای حجم کمتر
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache

# ─── کپی کد پروژه ───
COPY . .

# ─── نصب خود پکیج (editable) ───
RUN pip install -e . || true

# ─── پوشه‌های runtime (اگر volume وصل نباشد) ───
RUN mkdir -p \
    /app/storage/database \
    /app/storage/sessions \
    /app/storage/logs \
    /app/storage/media \
    /app/storage/reports \
    /app/storage/backups \
    /app/storage/brain \
    /app/storage/safe_brain \
    /app/storage/offset

# ─── healthcheck ───
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS http://localhost:8787/health || exit 1

# ─── tini برای signal handling تمیز ───
ENTRYPOINT ["/usr/bin/tini", "--"]

# ─── اجرای ربات ───
CMD ["python", "main.py"]