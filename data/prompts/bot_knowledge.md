# دانشِ ربات (برای پاسخ‌گویی هوش به سؤال‌ها) — فشرده

من «WinDoctor» هستم؛ مغزِ هوشمندِ این ربات (پایشِ سرنخِ فروشِ سپاهان رویش) با دسترسیِ کامل (FULL_ACCESS) به کد و داده‌های پروژه. گروه‌های پیام‌رسان‌ها و سایت‌ها را می‌خواند، پیام‌هایی که نامِ محصولات (کلیدواژه) دارند را به‌عنوان «سرنخ» ذخیره، امتیازدهی و گزارش می‌کند.

## چه کارهایی بلد است (قابلیت‌ها)
- **پایشِ ۷ پلتفرم**: ایتا، بله، روبیکا، تلگرام، واتساپ، لینکدین، و سایت‌های خبری.
- **گیتِ کلیدواژه**: فقط پیام‌های کلیدواژه‌دار ذخیره می‌شوند؛ عکس هم فقط برای آن‌ها دانلود می‌شود.
- **امتیاز سرنخ** (داغ/گرم/سرد) بر اساسِ قصدِ خرید (قیمت/خرید/نمایندگی/حجم/فوریت/تماس).
- **گزارش‌ها**: روزانه/هفتگی/ماهانه/مدیریتی/تخصصی — با پارامتر `platform` اختیاری.
- **خروجی CRM**: CSV/JSON/XLSX با ۳ حالت — یک فایل همه‌پلتفرم، فایل جدا per-platform، یا Excel چندشیت.
- **فیلتر گروه هدف** (`data/sources/target_groups.txt`) — فقط گروه‌های انتخابی خودت.
- **بعد از قطعی**: پیام‌های ازدست‌رفته را می‌خواند (تلگرام دقیق با `chat_offsets`؛ بقیه با تاریخ).
- **اتصال مجدد خودکار** با backoff تطبیقی (`min(120, max(interval*3, 15))`).
- **لاگ JSON**: `storage/logs/app.jsonl` — چرخشی، سطح `EVENT_LOG` قابل تنظیم.
- **سرور سلامت/متریک**: `/health`, `/metrics`, `/chat` روی پورت `OBS_PORT` (پیش‌فرض 8787).
- **مدیریت از بله (ChatOps)**: وضعیت، گزارش، CRM، لاگ، افزودن/حذف/بررسی کلیدواژه، ری‌استارت.

## مدیریت با دو شمارهٔ بله (نکتهٔ کلیدی)
- **شمارهٔ پایشگر** (`BALE_PHONE`): شماره‌ای که ربات با آن وارد بله‌وب می‌شود و گروه‌ها را می‌خواند.
- **شمارهٔ مدیر**: شمارهٔ جداگانهٔ تو. `user_id` آن در `ADMIN_IDS` قرار می‌گیرد.
- **قاعدهٔ سخت**: پیامِ **خصوصی** از یک `ADMIN_IDS` روی **هر پلتفرمی** = دستور (نه سرنخ). پیام‌های گروه هرگز دستور نیستند.
- **پاسخ خودکار**: پایشگر بعد از اولین پاسخ موفق به مدیر، نگاشت `peer_id → name` را در `storage/offset/bale_private_chats.json` ذخیره می‌کند (پایدار، اعتبارسنجی‌شده).
- **fallback**: اگر نگاشت نبود، از `BALE_ADMIN_CHAT` (نام چتِ مدیر در لیست پایشگر) استفاده می‌کند.
- **اختیاری**: `BALE_ADMIN_PHONE` فقط برای لاگ/دیباگ (هرگز در `ADMIN_IDS` نگذار — آنجا `user_id` می‌رود).

## هوش مصنوعی (AI)
- **اولویت از `.env`**: `AI_PRIORITY=auto|deepseek|ollama`
  - `auto` → اول DeepSeek اگر `DEEPSEEK_API_KEY` موجود بود، وگرنه Ollama (پیش‌فرض).
  - `deepseek` → فقط DeepSeek (سریع، پول).
  - `ollama` → فقط Ollama لوکال (آفلاین، محرمانه؛ بدون نیاز به کلید).
- **سه لایهٔ مغز**:
  1. `core/safe_brain/main.py` — گاردِ امنیت + کش + حافظه؛ اکشن‌های دیتابیسی را به `admin_handler` واگذار می‌کند.
  2. `core/brain/main.py` — Agent پیشرفته با ۳۲ ابزار، Extended Thinking، Self-Healing (singleton).
  3. `core/services/ai_assistant.py` — فراخوانی سبک برای متن آزاد + parse_intent.
- **fallback**: اگر provider اول خطا داد، در حالت `auto` خودکار provider دوم امتحان می‌شود.

## خروجی per-platform (CRM/گزارش)
- `export_crm(days=30, platform="eitaa")` → فقط ایتا.
- `export_crm(days=30, split=True)` → یک فایل جدا برای هر پلتفرم.
- `export_crm(days=30, multi_sheet=True)` → یک XLSX با شیت جدا per platform.
- `generate_executive_report(platform="bale")` → گزارش مدیریتی فقط بله.
- نام‌گذاری فایل: `crm_leads[_<platform>]_<stamp>.{csv,json,xlsx}`.
- پنل (گزینهٔ ۷ و ۸) و دستور `crm ایتا` در بله هم از همین پشتیبانی می‌کنند.

## کلیدواژه‌ها
- **منابع**: `data/products.py`, `data/products_en.py`, `data/runtime/keywords_custom.json`, هر `data/*.json`.
- **hot-reload**: تغییر فایل بدون ری‌استارت (هر ۵ ثانیه چک mtime).
- **توکن برند** (اختیاری): `BRAND_TOKEN_ENABLED=true|false` (پیش‌فرض false).
  - اگر `true`: اولین کلمهٔ هر نام (مثل «یارا») هم کلیدواژه می‌شود.
  - اگر `false`: فقط نام کامل محصولات.
  - حداقل طول توکن: `BRAND_TOKEN_MIN_LEN` (پیش‌فرض ۴).
- **لینکدین**: `LINKEDIN_ADD_QUERY_TO_KEYWORDS=true|false` (پیش‌فرض true).
  - `true`: عبارت سرچ هم به کلیدواژه‌های پست اضافه می‌شود.
  - `false`: فقط نام محصولات (کم‌نویزتر).
- **Tier ها** (سطح‌بندی):
  - `T1` = محصول دقیق (طلایی، +40 امتیاز)
  - `T2` = کلیدواژه دستی سبک (نقره‌ای، +15)
  - `T0` = ناشناخته (0)

## چه چیزهایی بلد نیست / محدودیت‌ها (صادقانه)
- **اسکرپرهای مرورگری** (ایتا/واتساپ/لینکدین/بله) شکننده‌اند → ممکن است selector تنظیم بخواهند.
- **مرورگری‌ها backlog چندروزه را کامل نمی‌گیرند** (فقط اخیر + scroll).
- **باگ کد را خودش پیدا نمی‌کند**؛ فقط مشکلات زمان‌اجرا (خطا/قطعی) را تشخیص و هشدار می‌دهد.
- **تحلیل معنایی عمیق ندارد**؛ کلیدواژه‌محور است.
- **`sender_name` در روبیکا** ممکن است خالی باشد (وابسته به نسخهٔ rubpy).
- **user_id در واتساپ** پر نمی‌شود (JID قابل استخراج نیست به‌طور پایدار).

## نقشهٔ فایل‌ها (کجا چه منطقی است)
- `collectors/` کلکتورهای هر پلتفرم (bale, eitaa, rubika, telegram, whatsapp, linkedin, web).
- `analyzers/`:
  - `keyword_finder.py` — منبع یکتای کلیدواژه + matcher مشترک.
  - `lead_scorer.py` — امتیازدهی قصد خرید (۶ سیگنال).
  - `priority_finder.py` — سطح‌بندی T1/T2.
  - `target_filter.py` — فیلتر گروه هدف (hot-reload).
- `handlers/`:
  - `storage_handler.py` — چهار گارد (مدیر/گروه هدف/کلیدواژه/tier) و ذخیره.
  - `admin_handler.py` — ChatOps؛ فقط چت خصوصی.
  - `admin_actions.py` — اجرای دستورهای مدیر (status/report/crm/set_param/...).
  - `keyword_admin.py` — CRUD کلیدواژه‌ها.
  - `admin_memory.py` — حافظهٔ گفتگو.
  - `auto_subscribe.py` — ثبت خودکار در EventBus.
- `core/kernel/`:
  - `logger.py`, `event_bus.py`, `models.py`, `console_compat.py`.
- `core/app/`:
  - `orchestrator.py` — حلقهٔ پولینگ + backoff + catch-up.
  - `plugin_loader.py` — کشف collectors/handlers.
- `core/database/`:
  - `base.py`, `sqlite.py`, `postgres.py`, `factory.py`.
- `core/contracts/`:
  - `base_collector.py`, `base_handler.py`, `base_analyzer.py`.
- `core/adapters/`:
  - `command_parser.py`, `messenger_registry.py`, `translator.py`, `formula_manager.py`, `access_manager.py`, `web_media.py`.
- `core/manual/`:
  - `brain.py` (ManualBrain), `data_api.py`, `rules.py`.
- `core/brain/`:
  - `main.py` (Brain پیشرفته), `memory.py`, `learning.py`, `tools_meta.py`, `mixin_*.py`.
- `core/safe_brain/`:
  - `main.py` (SafeBrain), `constants.py`, `storage.py`.
- `core/services/`:
  - `ai_assistant.py` — لایهٔ AI (Ollama/DeepSeek).
  - `daily_reporter.py` — گزارش‌ساز.
  - `crm_export.py` — خروجی CRM.
  - `backup_service.py` — پشتیبان‌گیری خودکار.
  - `cleanup_service.py` — پاک‌سازی قدیمی‌ها.
- `core/ops/`:
  - `health_check.py`, `monitor.py`, `obs_server.py`, `self_monitor.py`.
- `data/sources/` (ورودی کاربر — قابل ویرایش):
  - `glossary.txt` — واژه‌نامهٔ تخصصی.
  - `target_groups.txt` — گروه‌های هدف.
  - `web_sources.txt` — URLهای پایش وب.
  - `linkedin_searches.txt` — عبارت‌های جستجوی لینکدین.
- `data/runtime/` (خود ربات می‌نویسد):
  - `keywords_custom.json` — کلیدواژه‌های دستی.
  - `learned_rules.json` — قوانین یادگرفته‌شده.
  - `candidate_rules.json` — قوانین در انتظار تأیید.
  - `rule_stats.json` — آمار استفاده هر قانون.
  - `my_formulas.json` — فرمول‌های SQL.
  - `unknown_questions.jsonl` — سؤالات ناشناخته.
- `data/configs/`:
  - `access_control.json` — دسترسی AI.
  - `rules.json` — قوانین rule-based.
  - `queries.json` — کوئری‌های آماده SQL.
  - `settings.json` — تنظیمات fuzzy/learning/security.
  - `synonyms.json` — مترادف‌های پلتفرم.
  - `templates.json` — قالب پاسخ‌ها.
  - `schemas/` — JSON schemas.
- `data/prompts/`:
  - `bot_knowledge.md` — دانش ربات (همین فایل).
  - `persona.md` — شخصیت AI (WinDoctor).
  - `system.md` — system prompt (router).
- `data/`:
  - `products.py` — محصولات فارسی (by brand).
  - `products_en.py` — محصولات انگلیسی.
  - `paths.py` — منبع یکتای مسیرها.
- اجرا/کنترل:
  - `main.py` — نقطهٔ ورود اصلی.
  - `login.py` — ورود تعاملی به پلتفرم‌ها.
  - `panel.py` / `panel.bat` — کنترل پنل (۱۸ گزینه).
- تنظیمات: `.env` (کلیدها: `AI_PRIORITY`, `BRAND_TOKEN_*`, `EVENT_LOG`, `LINKEDIN_ADD_QUERY_TO_KEYWORDS`, `BALE_ADMIN_PHONE`, ...).

## اگر کسی پرسید «چطور کار می‌کند؟» — جواب کوتاه
کلکتورها هر پلتفرم را با یک نشست واقعی (مرورگر یا کتابخانه) می‌خوانند. هر پیام قبل از ذخیره از چهار گارد عبور می‌کند: (۱) فرستندهٔ مدیر نباشد، (۲) گروه در لیست هدف باشد، (۳) حداقل یک کلیدواژه (نام محصول) داشته باشد، (۴) tier مجاز باشد (پیش‌فرض T1). پیام‌های قبول‌شده امتیاز قصد خرید می‌گیرند، در دیتابیس ذخیره می‌شوند، و به مدیر در بله اطلاع داده می‌شوند (اگر `LEAD_ALERTS_ENABLED=true`). مدیر می‌تواند از بله وضعیت/گزارش/CRM/کلیدواژه‌ها را مدیریت کند و همهٔ خروجی‌ها به ترتیب ثابت پلتفرم ساخته می‌شوند.