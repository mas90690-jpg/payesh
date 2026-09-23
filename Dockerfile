# ======================================================================
#  Dockerfile — اجرای ربات روی سرور
#  ساخت:    docker build -t srooyesh-monitor .
#  اجرا:    docker compose up -d
# ======================================================================
FROM python:3.11-slim-bookworm

# ─── متغیرهای محیطی پایه ───
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8 \
    TZ=Asia/Tehran \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ─── وابستگی‌های سیستمی ───
# chromium برای playwright، fonts برای فارسی، tzdata برای زمان
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        wget \
        gnupg \
        tzdata \
        fonts-dejavu-core \
        fonts-liberation \
        libnss3 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libgtk-3-0 \
        libnspr4 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        libxshmfence1 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2 \
        xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# ─── پوشهٔ کار ───
WORKDIR /app

# ─── لایهٔ اول: pyproject (کش pip) ───
COPY pyproject.toml README.md ./
COPY requirements.txt ./

# ─── لایهٔ دوم: نصب پکیج (اجباری) ───
RUN pip install --upgrade pip setuptools wheel \
    && pip install -e .

# ─── نصب گروه‌های اختیاری (telegram, rubika, postgres, vector) ───
# اگه نمی‌خواهی، خط زیر را کامنت کن یا از ARG استفاده کن
ARG EXTRAS="telegram,rubika,postgres"
RUN if [ -n "$EXTRAS" ]; then \
        pip install -e ".[${EXTRAS}]"; \
    fi

# ─── نصب Chromium (playwright) ───
RUN playwright install chromium \
    && playwright install-deps chromium

# ─── لایهٔ سوم: کد اصلی ───
COPY . .

# ─── اطمینان از ساختار پوشه‌ها ───
RUN mkdir -p \
        storage/database \
        storage/sessions \
        storage/logs \
        storage/media \
        storage/reports \
        storage/backups \
        storage/offset \
        storage/brain \
        storage/safe_brain \
        data/runtime \
        data/configs \
        data/prompts \
        data/sources

# ─── غیر-root user (امنیت) ───
# UID/GID 1000 معمولاً اولین user غیر-root در Linux است
RUN useradd -m -u 1000 -s /bin/bash monitor \
    && chown -R monitor:monitor /app
USER monitor

# ─── Healthcheck (اختیاری — اگه OBS_ENABLED=true) ───
# چون OBS_PORT از env می‌آید، اینجا hardcode می‌کنیم
# اگه OBS_ENABLED=false، healthcheck fail می‌شود (طبیعی)
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8787/health || exit 1

# ─── اجرای ربات ───
CMD ["python", "main.py"]