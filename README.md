# Lead Monitor — Srooyesh

> ربات پایش هوشمند سرنخ فروش **سپاهان رویش** — ۸ پلتفرم، تحلیل کلیدواژه،
> امتیازدهی قصد خرید، هشدار زنده، و دستیار AI داخلی.

<p align="center">
  <b>🎯 هدف:</b> پیدا کردن خودکار مشتریان بالقوه از پیام‌های عمومی گروه‌ها،
  به‌جای اینکه کارشناس فروش دستی همه‌جا را بگردد.
</p>

---

## 📋 فهرست

- [ویژگی‌ها](#-ویژگیها)
- [پلتفرم‌های پشتیبانی‌شده](#-پلتفرمهای-پشتیبانیشده)
- [پیش‌نیازها](#-پیشنیازها)
- [نصب گام‌به‌گام](#-نصب-گامبهگام)
- [اجرا](#-اجرا)
- [کنترل پنل (panel)](#-کنترل-پنل-panel)
- [ChatOps (مدیریت از بله)](#-chatops-مدیریت-از-بله)
- [ساختار پروژه](#-ساختار-پروژه)
- [پیکربندی](#-پیکربندی)
- [دستیار AI](#-دستیار-ai)
- [Docker](#-docker)
- [عیب‌یابی](#-عیبیابی)
- [توسعه](#-توسعه)
- [امنیت](#-امنیت)
- [لایسنس](#-لایسنس)

---

## 🚀 ویژگی‌ها

- **۸ پلتفرم**: بله، ایتا، روبیکا، تلگرام، واتساپ، لینکدین، اینستاگرام، وب
- **تشخیص کلیدواژه** با hot-reload (بدون ری‌استارت)
- **سطح‌بندی هوشمند**: T1 (محصول دقیق) + T2 (دستی)
- **امتیازدهی قصد خرید** (0..100) بر اساس ۶ گروه سیگنال (B2B intent scoring)
- **هشدار زنده** به مدیر در بله (با فیلتر tier و score)
- **گزارش خودکار** روزانه/هفتگی/ماهانه (TXT, HTML, CSV, JSON)
- **خروجی CRM** (CSV, JSON, XLSX — با قابلیت split per-platform)
- **دستیار AI داخلی** (Ollama لوکال یا DeepSeek آنلاین)
- **ChatOps**: مدیریت ربات از چت خصوصی بله با دستورات متنی
- **Self-Monitor**: تشخیص خودکار مشکلات و هشدار به مدیر
- **Observability**: سرور `/health`, `/metrics` (Prometheus), `/chat`
- **پشتیبانی PostgreSQL** برای مقیاس بزرگ

---

## 📱 پلتفرم‌های پشتیبانی‌شده

| پلتفرم | روش | احتیاج به |
|--------|-----|----------|
| 📨 بله | Playwright (web.bale.ai) | شماره تلفن + کد پیامکی |
| 📱 ایتا | Playwright (web.eitaa.com) | شماره تلفن + کد پیامکی |
| 🟣 روبیکا | rubpy (رسمی) | شماره تلفن + کد پیامکی |
| ✈️ تلگرام | Playwright (web.telegram.org) | شماره + کد (VPN) |
| 🟢 واتساپ | Playwright (web.whatsapp.com) | اسکن QR با گوشی |
| 💼 لینکدین | Playwright | ایمیل + رمز (⚠️ خطر بن) |
| 📷 اینستاگرام | Playwright (instagram.com) | نام کاربری + رمز (⚠️ خطر بن) |
| 🌐 وب | aiohttp + BeautifulSoup | فقط URL |

---

## 🛠 پیش‌نیازها

### سیستم
- **Python 3.11 یا 3.12** ([دانلود](https://www.python.org/downloads/))
- **Windows 10/11**، **Linux**، یا **macOS**
- **Chrome** یا **Edge** نصب باشد (برای Playwright) — یا `playwright install chromium` اجرا شود

### منابع حداقلی
- CPU: ۲ هسته
- RAM: ۴GB (۸GB اگر Ollama هم اجرا می‌شود)
- Disk: ۵GB (برای session + media + reports + Ollama model)

### اختیاری
- **Ollama** ([دانلود](https://ollama.com/download)) — برای دستیار AI لوکال
- **Docker** ([دانلود](https://docs.docker.com/get-docker/)) — برای استقرار روی سرور

---

## 📦 نصب گام‌به‌گام

### ۱) کلون پروژه

```bash
git clone <repo-url> lead-monitor
cd lead-monitor
۲) ساخت محیط مجازی
bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux / macOS
python3 -m venv venv
source venv/bin/activate
۳) نصب پکیج
bash
pip install --upgrade pip
pip install -e .
گروه‌های اختیاری (فقط در صورت نیاز):

bash
pip install -e ".[telegram]"    # telethon
pip install -e ".[rubika]"      # rubpy
pip install -e ".[postgres]"    # asyncpg
pip install -e ".[vector]"      # chromadb + sentence-transformers
pip install -e ".[dev]"         # pytest + lint + type
pip install -e ".[all]"         # همه‌چیز
۴) نصب مرورگر Playwright
bash
playwright install chromium
۵) کپی و پیکربندی .env
bash
# Windows
copy .env.example .env

# Linux / macOS
cp .env.example .env
حالا .env را باز کن و پر کن:

🔴 ADMIN_IDS — user_id عددی مدیر (پایین توضیح داده شده)

🟠 پلتفرم(ها) — BALE_PHONE, EITAA_PHONE, ...

🟢 AI_PRIORITY — ollama (لوکال) یا deepseek (آنلاین) یا auto

۶) ورود به پلتفرم‌ها (یک‌بار)
bash
python login.py
منو نمایش داده می‌شود:

text
[1] روبیکا (Rubika)
[2] بله (Bale - مرورگر)
[3] ایتا (Eitaa - مرورگر)
[4] تلگرام (Telegram)
[5] واتساپ (WhatsApp - مرورگر/QR)
[6] لینکدین (LinkedIn - مرورگر)
[7] اینستاگرام (Instagram - مرورگر)
[9] ورود همه (به ترتیب)
[0] خروج
انتخاب کن و مراحل را در مرورگر انجام بده. نشست ذخیره می‌شود.

⚠️ قبل از لاگین مرورگری، ربات را Stop کن (گزینهٔ ۳ در panel).
اگر ربات روشن باشد، پروفایل Playwright قفل است و خطای
Target page, context or browser has been closed می‌گیری.

۷) پیدا کردن ADMIN_IDS
بعد از اولین اجرای ربات، به بله پیام بده (چت خصوصی). ربات user_id تو را در لاگ می‌نویسد:

text
👋 آیدیِ تو: 123456789
آن را در .env بگذار: ADMIN_IDS=123456789
بعد ربات را restart کن.

▶️ اجرا
روش ۱: مستقیم
bash
python main.py
برای توقف: Ctrl+C

روش ۲: از کنترل پنل (توصیه‌شده)
bash
python panel.py
# یا در Windows: دابل‌کلیک panel.bat
روش ۳: Docker (سرور)
bash
docker compose up -d --build
docker compose logs -f
توجه: ورود اولیه به پلتفرم‌ها باید خارج از Docker انجام شود، سپس پوشهٔ storage/sessions/ را کپی کن.

🎛 کنترل پنل (panel)
#	کار	توضیح
۱	Start	ربات را در پس‌زمینه/پنجرهٔ جدید بالا می‌آورد
۲	Restart	Stop + Start
۳	Stop	همهٔ children (مرورگرها) را هم می‌کشد
۴	Status	PID + تعداد سرنخ به تفکیک پلتفرم
۵	Login	ورود تعاملی به پلتفرم‌ها
۶	Backup	پشتیبان امن از DB با sqlite3.backup()
۷	Reports	تولید گزارش مدیریتی/تخصصی با فیلتر پلتفرم
۸	CRM	CSV/JSON/XLSX با حالت‌های split/multi_sheet
۹	Tests	اجرای pytest
۱۰	Cleanup	پاک‌سازی فایل‌های قدیمی
۱۱	GitHub Push	با چک خودکار .env tracked
۱۲	Health	وضعیت سلامت + لینک‌های /health, /metrics
۱۳	AI	وضعیت Ollama + لیست adminها
۱۴	Requests	درخواست‌های قابلیت + خطاهای اخیر
۱۵	Chat	باز کردن چت AI لوکال در مرورگر
۱۶	Recent	آخرین گزارش‌ها و خروجی‌های CRM
۱۷	GitHub Pull	دریافت تغییرات با حفظ فایل‌های پاک‌شده
۱۸	Git Status	وضعیت شاخه، remote، و تغییرات
💬 ChatOps (مدیریت از بله)
بعد از راه‌اندازی، در چت خصوصی بله با ربات، این دستورات کار می‌کنند:

اطلاعات
دستور	نتیجه
وضعیت	آمار کلی
گزارش	گزارش مدیریتی
CRM	خروجی اکسل همهٔ سرنخ‌ها
CRM ایتا	فقط ایتا
لاگ	آخرین لاگ‌ها
سلامت	بررسی سلامت کامل
آخرین سرنخ	جزئیات آخرین سرنخ
مدیریت کلیدواژه
دستور	نتیجه
کلیدواژه «کود مایع» را اضافه کن	افزودن کلیدواژه
کلیدواژه «کود» را حذف کن	حذف
کلیدواژه‌ها	لیست
یادگیری (هوش خودکار)
دستور	نتیجه
یاد بگیر «قیمت کود چنده؟» = «قیمت هر تن X تومان»	قانون دستی
یاد بگیر «X» = SQL: SELECT COUNT(*) FROM ...	قانون SQL
لیست candidate ها	قانون‌های یادگرفته‌شده
تأیید <نام>	فعال‌سازی
تنظیمات
دستور	نتیجه
set BALE_POLL_SECONDS=300	تغییر env (بعد از ری‌استارت)
get BALE_POLL_SECONDS	خواندن مقدار
ری‌استارت	restart ربات
فرمول‌ها
دستور	نتیجه
ذخیره کن «گزارش هفتگی» = SELECT * FROM messages WHERE...	ذخیرهٔ SQL
اجرا کن «گزارش هفتگی»	اجرا (SELECT)
تأیید «گزارش هفتگی»	تأیید نوشتن
لیست فرمول‌ها	همهٔ فرمول‌ها
📂 ساختار پروژه
text
lead-monitor/
├── main.py                  ← نقطهٔ ورود اصلی
├── panel.py / panel.bat     ← کنترل پنل
├── login.py                 ← ورود تعاملی به پلتفرم‌ها
├── config.py                ← همهٔ تنظیمات از .env
├── .env.example             ← نمونهٔ تنظیمات
├── pyproject.toml           ← تعریف پکیج (منبع یکتای وابستگی‌ها)
├── requirements.txt         ← لایهٔ سازگاری (pip)
├── pytest.ini               ← تنظیم pytest
├── Dockerfile               ← image build
├── docker-compose.yml       ← استقرار Docker
├── README.md                ← همین فایل
├── LICENSE                  ← اختصاصی
│
├── core/                    ← هستهٔ سیستم
│   ├── __init__.py
│   ├── kernel/              ← زیرساخت
│   │   ├── logger.py        ← لاگ JSONL ساخت‌یافته
│   │   ├── event_bus.py     ← pub/sub داخلی
│   │   ├── models.py        ← Message/AnalyzedMessage/Lead
│   │   └── console_compat.py ← fix کنسول Windows
│   │
│   ├── contracts/           ← کلاس‌های پایه
│   │   ├── base_collector.py
│   │   ├── base_handler.py
│   │   └── base_analyzer.py
│   │
│   ├── database/            ← SQLite/Postgres
│   │   ├── base.py
│   │   ├── sqlite.py
│   │   ├── postgres.py
│   │   └── factory.py
│   │
│   ├── adapters/            ← آداپترها
│   │   ├── access_manager.py
│   │   ├── command_parser.py
│   │   ├── formula_manager.py
│   │   ├── messenger_registry.py
│   │   ├── translator.py
│   │   └── web_media.py
│   │
│   ├── app/                 ← لایهٔ application
│   │   ├── orchestrator.py  ← پولینگ + reconnect
│   │   └── plugin_loader.py ← کشف collectors/handlers
│   │
│   ├── manual/              ← هوش rule-based
│   │   ├── brain.py         ← ManualBrain
│   │   ├── data_api.py      ← دسترسی به DB
│   │   └── rules.py         ← لودر قوانین
│   │
│   ├── brain/               ← دستیار AI پیشرفته
│   │   ├── main.py          ← Brain (۴۱ ابزار)
│   │   ├── constants.py
│   │   ├── memory.py
│   │   ├── learning.py
│   │   ├── tools_meta.py
│   │   ├── utils.py
│   │   └── mixin_*.py       ← files/system/info/reports/agent
│   │
│   ├── safe_brain/          ← گارد امن + کش
│   │   ├── main.py          ← SafeBrain
│   │   ├── constants.py
│   │   └── storage.py
│   │
│   ├── services/            ← سرویس‌های کاربردی
│   │   ├── ai_assistant.py  ← لایهٔ AI (Ollama/DeepSeek)
│   │   ├── daily_reporter.py ← گزارش‌ساز
│   │   ├── crm_export.py    ← خروجی CRM
│   │   ├── backup_service.py ← پشتیبان‌گیری خودکار
│   │   └── cleanup_service.py ← پاک‌سازی قدیمی‌ها
│   │
│   └── ops/                 ← عملیات
│       ├── health_check.py  ← چک سلامت DB/collectors
│       ├── monitor.py       ← SystemMonitor + Performance
│       ├── obs_server.py    ← سرور /health, /metrics, /chat
│       └── self_monitor.py  ← پایشگر خودکار
│
├── analyzers/               ← تحلیل‌گرها
│   ├── keyword_finder.py    ← تشخیص کلیدواژه (hot-reload)
│   ├── priority_finder.py   ← سطح‌بندی T1/T2
│   ├── lead_scorer.py       ← امتیاز قصد خرید (B2B intent)
│   └── target_filter.py     ← فیلتر گروه‌های هدف
│
├── collectors/              ← کلکتورها (۸ پلتفرم)
│   ├── bale.py              ← Playwright
│   ├── eitaa.py             ← Playwright
│   ├── rubika.py            ← rubpy
│   ├── telegram.py          ← Playwright (web)
│   ├── whatsapp.py          ← Playwright
│   ├── linkedin.py          ← Playwright
│   ├── instagram.py         ← Playwright (فقط فالوورها)
│   └── web.py               ← aiohttp + Playwright رندر
│
├── handlers/                ← هندلرها
│   ├── storage_handler.py   ← ذخیرهٔ سرنخ
│   ├── admin_handler.py     ← ChatOps coordinator
│   ├── admin_actions.py     ← اجرای دستورات
│   ├── admin_memory.py      ← حافظهٔ گفتگو
│   ├── keyword_admin.py     ← CRUD کلیدواژه
│   └── auto_subscribe.py    ← ثبت خودکار در EventBus
│
├── data/                    ← داده‌ها (منبع حقیقت)
│   ├── products.py          ← محصولات فارسی (dict by brand)
│   ├── products_en.py       ← محصولات انگلیسی
│   ├── paths.py             ← همهٔ مسیرها (منبع یکتا)
│   ├── configs/
│   │   ├── access_control.json  ← دسترسی‌های AI
│   │   ├── rules.json           ← قانون‌های هوش دستی
│   │   ├── queries.json         ← کوئری‌های نام‌دار
│   │   ├── settings.json        ← تنظیمات fuzzy/learning
│   │   ├── synonyms.json        ← مترادف‌های پلتفرم
│   │   ├── templates.json       ← قالب پاسخ‌ها
│   │   └── schemas/             ← JSON schemas
│   ├── prompts/
│   │   ├── persona.md       ← شخصیت AI
│   │   ├── system.md        ← system prompt
│   │   └── bot_knowledge.md ← دانش ربات
│   ├── sources/             ← ⭐ اینجا را ویرایش کن
│   │   ├── glossary.txt     ← واژه‌نامهٔ تخصصی
│   │   ├── linkedin_searches.txt
│   │   ├── web_sources.txt
│   │   └── target_groups.txt ← گروه‌های هدف
│   └── runtime/             ← ⭐ ربات خودش می‌نویسد
│       ├── keywords_custom.json
│       ├── learned_rules.json
│       ├── candidate_rules.json
│       ├── rule_stats.json
│       ├── unknown_questions.jsonl
│       └── my_formulas.json
│
└── storage/                 ← runtime (در .gitignore)
    ├── database/messages.db
    ├── sessions/            ← نشست‌های Playwright/rubpy
    ├── media/               ← عکس‌ها و فایل‌های پیام‌ها
    ├── reports/             ← گزارش‌های تولیدشده
    ├── logs/app.jsonl       ← لاگ ساخت‌یافته
    ├── backups/             ← بکاپ‌های خودکار
    ├── brain/               ← حافظه و کش AI
    ├── safe_brain/          ← حافظه و کش SafeBrain
    ├── offset/              ← seen_ids، chat_offsets
    └── requests.jsonl       ← درخواست‌های قابلیت
⚙️ پیکربندی
همهٔ تنظیمات از .env خوانده می‌شوند. به فایل .env.example نگاه کن — هر کلید با نشانه:

🔴 اجباری — بدون این کار نمی‌کند

🟠 اجباری برای پلتفرم فعال — اگر X_ENABLED=true

🟢 اختیاری — پیش‌فرض معقول

متغیرهای کلیدی
متغیر	توضیح
ADMIN_IDS	user_id عددی مدیر(ان) — با کاما جدا شوند
ADMIN_PLATFORM	bale (پیش‌فرض) — پلتفرم ChatOps
BALE_ENABLED	فعال‌سازی کلکتور بله
AI_PRIORITY	ollama / deepseek / auto
DEEPSEEK_API_KEY	کلید API (فقط برای DeepSeek)
OLLAMA_URL	آدرس Ollama لوکال
LOG_LEVEL	DEBUG / INFO / SUCCESS / WARNING / ERROR
OBS_ENABLED	فعال‌سازی سرور سلامت
فایل‌های قابل ویرایش (داده)
فایل	برای چه
data/sources/glossary.txt	واژه‌نامهٔ تخصصی (کلمه = ترجمه)
data/sources/target_groups.txt	گروه‌های هدف (فیلتر)
data/sources/linkedin_searches.txt	عبارت‌های سرچ لینکدین
data/sources/web_sources.txt	URLهای پایش وب
data/runtime/keywords_custom.json	کلیدواژه‌های دستی (خودکار)
مثال: اضافه کردن کلیدواژهٔ دستی
json
{
  "version": "2.0.0",
  "keywords": {
    "کود مایع": {"tier": "T1"},
    "بذر گوجه": {"tier": "T2"}
  }
}
یا از بله: کلیدواژه «کود مایع» را اضافه کن

🤖 دستیار AI
ربات سه لایهٔ هوش دارد:

۱. ManualBrain (rule-based — همیشه فعال)
پاسخ به سؤال‌های رایج

مدیریت کلیدواژه

تحلیل خطاها

بدون نیاز به AI، سریع، رایگان

۲. SafeBrain (گارد امن)
چک امنیت (بلاک rm -rf, drop table, ...)

Cache + حافظهٔ گفتگو

Rate limit (۲۰ API call/ساعت — قابل تنظیم)

واگذاری به AdminHandler برای کارهای پیچیده

cache available() ۳۰ ثانیه — جلوگیری از ping مکرر

۳. Brain (AI چندلایه)
Ollama لوکال (آفلاین، محرمانه) — پیشنهاد برای داده حساس

DeepSeek آنلاین (سریع‌تر، دقیق‌تر) — نیاز به internet

۴۱ ابزار (read/write files, git, test, grep, ...)

تنظیم AI
در .env:

env
AI_PRIORITY=ollama          # یا deepseek یا auto
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:3b     # یا gemma3:1b, gemma3:4b
برای DeepSeek:

env
AI_PRIORITY=deepseek
DEEPSEEK_API_KEY=sk-...
⚠️ مدل Ollama باید قبلاً نصب شده باشد: ollama pull qwen2.5:3b
برای دیدن مدل‌های نصب‌شده: ollama list

🐳 Docker
راه‌اندازی سریع
bash
docker compose up -d --build
docker compose logs -f
نکات مهم
ورود اولیه باید خارج از Docker باشد (کد پیامکی/QR):

python login.py روی سیستم لوکال
storage/sessions/ را به سرور کپی کن
docker compose up -d
پوشه‌های persistent:

./storage → DB, sessions, media, reports, logs

./data → محصولات، کلیدواژه، rules

پروکسی (اگر ایران):

yaml
environment:
  - HTTPS_PROXY=http://host.docker.internal:PORT
shm_size: در docker-compose.yml روی 1gb تنظیم شده (مرورگر chromium به آن نیاز دارد).

🔧 عیب‌یابی
ربات بالا نمی‌آید
bash
# لاگ را ببین
tail -f storage/logs/app.jsonl

# یا از panel
python panel.py
# → 4) Status & stats
مرورگر بالا نمی‌آید / خطای exitCode=21
علت رایج: ربات روشن است و پروفایل Playwright قفل است.

راه‌حل: اول ربات را Stop کن (panel گزینهٔ ۳)، بعد login.py را اجرا کن.

اگر باز هم نشد:

bash
# Chrome/Edge نصب است؟ یا Chromium دانلود کن:
playwright install chromium

# یا در .env مسیر بده:
CHROME_PATH=C:/Program Files/Google/Chrome/Application/chrome.exe
نشست بله/ایتا منقضی شده
bash
python login.py
# انتخاب 2 (بله) یا 3 (ایتا)
Ollama جواب نمی‌دهد / خطای model not found
bash
# Ollama running?
curl http://localhost:11434/api/tags

# مدل را ببین و نصب کن
ollama list
ollama pull qwen2.5:3b
یا از panel: python panel.py → 13) AI / Admin status

سرنخ‌ها ذخیره نمی‌شوند
چک کن:

STORAGE_REQUIRE_T1=true باشد → فقط T1 ذخیره می‌شود

گروه در target_groups.txt باشد (اگر فایل پر است)

STORAGE_ALLOWED_TIERS=T1 باشد

Health endpoint جواب نمی‌دهد
bash
curl http://localhost:8787/health
اگر جواب نداد:

OBS_ENABLED=true در .env؟

پورت 8787 آزاد است؟

ربات روشن است؟

Cleanup خیلی چیزها را پاک کرده
env
KEEP_MESSAGES_DAYS=0     # 0 = هرگز حذف نکن
KEEP_MEDIA_DAYS=30
KEEP_REPORTS_DAYS=90
EITAA_MESSAGES_PER_CHAT یا EITAA_MSGS_PER_CHAT؟
نام جدید: EITAA_MESSAGES_PER_CHAT (هماهنگ با دیگر پلتفرم‌ها).
نام قدیمی: EITAA_MSGS_PER_CHAT (سازگاری عقب‌رو).

در .env.example هر دو نوشته شده — هرکدام را پر کنی کار می‌کند.

👨‍💻 توسعه
افزودن پلتفرم جدید
collectors/newplatform.py بساز — از BaseCollector ارث‌بری کن

connect(), fetch_messages(), disconnect() را پیاده کن

platform_name property بده

در config.py (داخل _build_plugins_config) بلوک newplatformcollector اضافه کن

در .env.example متغیرهایش را بنویس

plugin_loader.py خودکار کشف می‌کند — نیازی به ثبت دستی نیست.

افزودن analyzer جدید
analyzers/new_analyzer.py با ارث‌بری از BaseAnalyzer

analyze(message) را پیاده کن

در storage_handler._analyze صدا بزن

افزودن هندلر
handlers/my_handler.py از BaseHandler

handler_name, can_handle, handle را پیاده کن

auto_subscribe.py خودکار ثبت می‌کند

تست
bash
pytest -q
pytest -v tests/test_specific.py
Lint و format
bash
ruff check .
black .
mypy core/
اجرای CLI ابزارها
bash
python -m core.services.crm_export 30           # CRM ۳۰ روز
python -m core.services.crm_export 7 eitaa      # فقط ایتا
python -m core.services.crm_export 30 --split   # split per platform
python -m core.services.crm_export 30 --multi-sheet  # یک XLSX چندشیت
🔒 امنیت
⚠️ این‌ها را هرگز push نکن
.env (شامل کلیدها و رمزها)

storage/sessions/ (نشست‌های احراز هویت)

storage/database/messages.db (داده‌های مشتری)

storage/media/ (عکس‌های خصوصی)

در .gitignore هست
text
.env
storage/
venv/
__pycache__/
*.pyc
backups/
panel.py → گزینهٔ ۱۱ (Push to GitHub)
این گزینه خودکار چک می‌کند که .env tracked نباشد و اگر باشد، abort می‌کند.

Rate limit
AI: حداکثر ۲۰ API call/ساعت (قابل تنظیم: SAFE_BRAIN_MAX_API_HOUR)

OBS: ۳۰ درخواست/دقیقه per-IP

Command whitelist
دستورات AI در data/configs/access_control.json تعریف شده‌اند:

read: **/* (بجز .env, storage/sessions/**)

write: data/**, storage/reports/**

execute: git status, git diff

Backup امنیتی
backup_service.py فایل‌های حساس را رد می‌کند:

.env, .env.local, .env.production, .env.staging

*.key, *.pem, *.crt, *.p12, *.pfx, *.jks

secrets.json, credentials.json, auth.json

.git-credentials, .netrc

پوشه‌های venv/, .venv/, env/, node_modules/

📄 لایسنس
این پروژه اختصاصی است و برای سپاهان رویش توسعه یافته.
استفادهٔ تجاری بدون اجازه مجاز نیست.

📞 پشتیبانی
📝 مشکل داری؟ در panel.py → گزینهٔ ۱۴ (Feature requests & errors)

🐛 باگ پیدا کردی؟ issue بساز

💡 ایده داری؟ به مدیر بگو — از بله: می‌خوام بتونی ...

نسخه: 3.1.0
سازگار با: Python 3.11 / 3.12