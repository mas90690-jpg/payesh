# analyzers/keyword_finder.py
"""
یابندهٔ کلیدواژه — منبع یکتای کلیدواژه‌ها برای کل سیستم.

منابع کلیدواژه:
  - data/products.py       → ALL_PRODUCTS (لیست تخت محصولات فارسی)
  - data/products_en.py    → ALL_PRODUCTS_EN (لیست تخت محصولات انگلیسی)
  - data/keywords_custom.json → کلیدهای داخل "keywords" (دستی، با tier)

⚠️ برندها کلیدواژه نیستن — فقط از طریق محصولات پیدا می‌شن.

کارایی:
  - فایل‌ها فقط وقتی دوباره خوانده می‌شوند که mtime عوض شده باشه.
  - بررسی mtime حداکثر هر KEYWORDS_RELOAD_SECONDS (پیش‌فرض ۵ث).
  - الگوی regex با مرزِ کلمه ساخته می‌شه تا «یاراترا» داخل «یاراترایی» match نشه.

نسخهٔ ۹ (این نسخه):
  - 🐛 FIX #13: فقط فایل‌های با پیشوند «keywords_» در data/ به‌عنوان
    منبع کلیدواژه قبول می‌شوند (جلوگیری از config.json → کلیدواژه).
  - 🐛 FIX #13: رشته‌های عددی/تاریخ/hex در _extract_strings_from_json
    نادیده گرفته می‌شوند.
  - ✅ نسخهٔ ۸ هم حفظ شده.

نسخهٔ ۸:
  - 🐛 FIX بحرانی: keywords_custom.json حالا از _json_files حذف می‌شود.
  - 🐛 FIX: _extract_strings_from_json حالا dict تودرتو را هم می‌گردد.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import time

from core.contracts.base_analyzer import BaseAnalyzer
from core.kernel.logger import log_error, log_info, log_success, log_warning
from core.kernel.models import AnalyzedMessage

# هر چند ثانیه حداکثر بررسی شود که آیا فایل‌های data عوض شده‌اند
_RELOAD_CHECK_SECONDS = float(os.getenv("KEYWORDS_RELOAD_SECONDS", "5"))

# آستانهٔ هشدار برای کندی‌های غیرعادی (میلی‌ثانیه)
_SLOW_LOAD_MS = 300
_SLOW_PATTERN_MS = 200

# ─── تنظیمات توکن برند (از config، یک‌بار در import) ───
try:
    from config import PLUGINS_CONFIG

    _kf_cfg = PLUGINS_CONFIG.get("keywordsfinder", {})
    _BRAND_TOKEN_ENABLED = bool(_kf_cfg.get("brand_token_enabled", False))
    _BRAND_TOKEN_MIN_LEN = int(_kf_cfg.get("brand_token_min_len", 4))
except Exception as e:
    log_warning(
        f"⚠️ [KF:init] PLUGINS_CONFIG نبود | "
        f"brand_token=False | {type(e).__name__}: {e}"
    )
    _BRAND_TOKEN_ENABLED = False
    _BRAND_TOKEN_MIN_LEN = 4

from data import paths as data_paths

# کاراکتر کلمه (برای مرز)
_WB = r"\w"

# ─── نام فایل‌های JSON که نباید به‌عنوان منبع کلیدواژه خونده بشن ───
# (چون جداگانه خونده می‌شن یا ساختار خاصی دارن)
_NON_KEYWORD_JSON: set[str] = set()

# 🎯 FIX #13: فقط فایل‌های با این پیشوند به‌عنوان منبع کلیدواژه قبول می‌شن
# — جلوگیری از تبدیل config.json / settings.json به کلیدواژه
_KEYWORD_JSON_PREFIX = "keywords_"

# 🎯 FIX #13: رشته‌های زیر این طول نادیده گرفته می‌شن (اعداد، کلیدهای کوتاه)
_MIN_KEYWORD_LEN = 2
# 🎯 FIX #13: رشته‌های بالای این طول نادیده گرفته می‌شن (توضیحات، متن‌های بلند)
_MAX_KEYWORD_LEN = 120


def _get_excluded_json_files() -> set[str]:
    """فایل‌های JSON که نباید به‌عنوان منبع کلیدواژهٔ عمومی خونده بشن.

    🐛 FIX: keywords_custom.json قبلاً دوبار خونده می‌شد:
      ۱. در _read_custom_keywords (درست)
      ۲. در _extract_strings_from_json (اشتباه — version/updated_at
         به‌عنوان کلیدواژه اضافه می‌شدن)
    """
    excluded: set[str] = set()
    try:
        excluded.add(os.path.basename(str(data_paths.KEYWORDS_CUSTOM)))
    except Exception as e:
        log_warning(
            f"⚠️ [KF:excluded] keywords_custom | "
            f"{type(e).__name__}: {e}"
        )
    return excluded


class KeywordFinder(BaseAnalyzer):
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.keywords: list[str] = []
        self.pattern: re.Pattern | None = None
        self._signature: tuple | None = None
        self._last_check: float = 0.0
        # 🐛 FIX: cache برای فایل‌های exclude
        self._excluded_json: set[str] = _get_excluded_json_files()

        log_info(
            f"🔍 [KF:init] شروع | data_dir={data_dir} | "
            f"brand_token={_BRAND_TOKEN_ENABLED} "
            f"(min_len={_BRAND_TOKEN_MIN_LEN}) | "
            f"reload_check={_RELOAD_CHECK_SECONDS}s | "
            f"excluded={sorted(self._excluded_json)}"
        )
        self._load(initial=True)

    # ==================================================
    # منابع و امضای تغییر
    # ==================================================
    def _json_files(self) -> list[str]:
        """
        فقط فایل‌های JSON که «منبع کلیدواژهٔ عمومی» هستند.

        🐛 FIX: keywords_custom.json رو exclude می‌کنه (چون جداگانه
        در _read_custom_keywords خونده میشه).
        """
        out: list[str] = []
        if not os.path.isdir(self.data_dir):
            log_warning(
                f"⚠️ [KF:_json_files] پوشهٔ data وجود ندارد: {self.data_dir}"
            )
            return out

        for fn in sorted(os.listdir(self.data_dir)):
            if not fn.endswith(".json"):
                continue
            if fn in _NON_KEYWORD_JSON:
                continue
            # 🐛 FIX: فایل‌های exclude (keywords_custom) رو رد کن
            if fn in self._excluded_json:
                log_info(
                    f"⏭️ [KF:_json_files] {fn} رد شد "
                    f"(جداگانه خونده میشه)"
                )
                continue
            # 🎯 FIX #13: فقط فایل‌های با پیشوند keywords_ قبول می‌شن
            if not fn.startswith(_KEYWORD_JSON_PREFIX):
                log_info(
                    f"⏭️ [KF:_json_files] {fn} رد شد "
                    f"(پیشوند keywords_ ندارد)"
                )
                continue
            out.append(os.path.join(self.data_dir, fn))
        return out

    def _source_files(self) -> list[str]:
        """همهٔ فایل‌هایی که mtimeشان باید چک بشه (شامل keywords_custom.json)."""
        files = [
            str(data_paths.PRODUCTS_PY),
            str(data_paths.PRODUCTS_EN_PY),
            str(data_paths.KEYWORDS_CUSTOM),
        ]
        files.extend(self._json_files())
        return files

    def _signature_now(self) -> tuple:
        sig = []
        for p in self._source_files():
            try:
                sig.append((p, os.path.getmtime(p)))
            except OSError:
                sig.append((p, None))
        return tuple(sig)

    # ==================================================
    # بارگذاری کلیدواژه‌ها
    # ==================================================
    def _collect_keywords(self) -> list[str]:
        """جمع‌آوری همهٔ کلیدواژه‌ها از همهٔ منابع — با لاگ تفصیلی."""
        t0 = time.monotonic()
        words: list[str] = []

        # ۱) محصولات فارسی
        n_fa = 0
        try:
            fa_words = self._import_list("data.products", "ALL_PRODUCTS")
            n_fa = len(fa_words)
            words += fa_words
        except Exception as e:
            log_error(
                f"❌ [KF:_collect] products.py | {type(e).__name__}: {e}"
            )

        # ۲) محصولات انگلیسی
        n_en = 0
        try:
            en_words = self._import_list("data.products_en", "ALL_PRODUCTS_EN")
            n_en = len(en_words)
            words += en_words
        except Exception as e:
            log_error(
                f"❌ [KF:_collect] products_en.py | {type(e).__name__}: {e}"
            )

        # ۳) keywords_custom.json — اختصاصی (فقط اینجا)
        custom = self._read_custom_keywords()
        n_custom = len(custom)
        words += custom

        # ۴) سایر فایل‌های JSON مجاز (بدون keywords_custom)
        n_extra = 0
        extra_files = self._json_files()
        for path in extra_files:
            got = self._extract_strings_from_json(path)
            n_extra += len(got)
            words += got

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_info(
            f"📚 [KF:_collect] {len(words)} کلمه از منابع | "
            f"fa={n_fa} en={n_en} custom={n_custom} "
            f"extra={n_extra} ({len(extra_files)} فایل) | "
            f"{elapsed_ms:.0f}ms"
        )

        if elapsed_ms > _SLOW_LOAD_MS:
            log_warning(
                f"🐌 [KF:_collect] کند | {elapsed_ms:.0f}ms "
                f"(آستانه {_SLOW_LOAD_MS}ms)"
            )
        return words

    def _read_custom_keywords(self) -> list[str]:
        """کلیدواژه‌های دستی از keywords_custom.json.

        پشتیبانی از دو فرمت:
          - لیست خالص (قدیمی): ["کود", "سم"]
          - دیکشنری با کلید "keywords": {"keywords": {"کود": {"tier": "T1"}}}

        ⚠️ اگر dict بود ولی کلید "keywords" نداشت → هیچ کلیدواژه‌ای برنمی‌گرداند
           (تا version/updated_at اشتباهاً کلیدواژه نشوند).
        """
        path = str(data_paths.KEYWORDS_CUSTOM)

        if not os.path.exists(path):
            log_info(f"ℹ️ [KF:custom] فایل وجود ندارد: {path}")
            return []

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            log_error(
                f"❌ [KF:custom] JSON نامعتبر | {path} | "
                f"line={e.lineno} col={e.colno} | {e.msg}"
            )
            return []
        except Exception as e:
            log_error(
                f"❌ [KF:custom] خواندن ناموفق | {path} | "
                f"{type(e).__name__}: {e}"
            )
            return []

        out: list[str] = []
        fmt = "unknown"

        # فرمت قدیمی: لیست
        if isinstance(data, list):
            fmt = "list"
            for x in data:
                if isinstance(x, str) and x.strip():
                    out.append(x.strip())

        # فرمت جدید: dict با کلید "keywords"
        elif isinstance(data, dict):
            kws = data.get("keywords")
            if isinstance(kws, dict):
                fmt = "dict_with_meta"
                for k in kws.keys():
                    if isinstance(k, str) and k.strip():
                        out.append(k.strip())
            elif isinstance(kws, list):
                fmt = "dict_with_list"
                for x in kws:
                    if isinstance(x, str) and x.strip():
                        out.append(x.strip())
            else:
                fmt = "dict_no_keywords"
                log_warning(
                    f"⚠️ [KF:custom] dict بدون کلید «keywords» | {path} | "
                    f"کلیدهای موجود: {list(data.keys())[:5]}"
                )

        log_info(
            f"📂 [KF:custom] {len(out)} کلیدواژه از فرمت «{fmt}» | "
            f"{os.path.basename(path)}"
        )
        return out

    @staticmethod
    def _extract_strings_from_json(path: str) -> list[str]:
        """
        استخراج رشته‌ها از یک فایل JSON عمومی.

        🐛 FIX: حالا dict تودرتو (nested) رو هم می‌گرده.
        مثلاً: {"group1": ["a", "b"], "group2": {"x": "c"}}
        """
        out: list[str] = []
        fn = os.path.basename(path)

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            log_warning(
                f"⚠️ [KF:extra] خواندن {fn} ناموفق — نادیده گرفته شد | "
                f"{type(e).__name__}: {e}"
            )
            return out

        def _is_keyword_like(s: str) -> bool:
            """🎯 FIX #13: آیا این رشته شبیه کلیدواژه است (نه متادیتا)؟"""
            if not s or len(s) < _MIN_KEYWORD_LEN:
                return False
            if len(s) > _MAX_KEYWORD_LEN:
                return False
            # اعداد خالص (1.0، 123، 3.14)
            if re.fullmatch(r"[\d.,:/\-\s]+", s):
                return False
            # تاریخ ISO (2026-01-01 / 2026-01-01T00:00:00Z)
            if re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}", s):
                return False
            # hex/uuid
            if re.fullmatch(
                r"[0-9a-fA-F]{8}(-?[0-9a-fA-F]{4}){3}-?[0-9a-fA-F]{12}",
                s,
            ):
                return False
            return True

        def _walk(obj, depth: int = 0) -> None:
            """بازگشتی: هر رشتهٔ معناداری در هر عمقی رو پیدا کن."""
            if depth > 5:  # جلوگیری از بازگشت بی‌نهایت
                return
            if isinstance(obj, str):
                s = obj.strip()
                # 🎯 FIX #13: فقط رشته‌های شبیه کلیدواژه
                if _is_keyword_like(s):
                    out.append(s)
            elif isinstance(obj, (list, tuple, set)):
                for x in obj:
                    _walk(x, depth + 1)
            elif isinstance(obj, dict):
                for v in obj.values():
                    _walk(v, depth + 1)

        _walk(data)

        if out:
            log_info(f"📂 [KF:extra] {len(out)} رشته از {fn}")
        return out

    @staticmethod
    def _import_list(module_path: str, attr: str) -> list[str]:
        """import ماژول و برگرداندن یک لیست رشته‌ای."""
        try:
            mod = importlib.import_module(module_path)
            importlib.reload(mod)
            val = getattr(mod, attr, None)
            if isinstance(val, (list, tuple, set)):
                return [
                    str(x).strip()
                    for x in val
                    if isinstance(x, str) and x.strip()
                ]
            log_warning(
                f"⚠️ [KF:import] {module_path}.{attr} یک لیست نیست | "
                f"type={type(val).__name__}"
            )
        except ImportError as e:
            log_error(
                f"❌ [KF:import] ماژول پیدا نشد: {module_path} | {e}"
            )
        except Exception as e:
            log_error(
                f"❌ [KF:import] {module_path}.{attr} | "
                f"{type(e).__name__}: {e}"
            )
        return []

    # ==================================================
    # ساخت الگو (با مرزِ کلمه)
    # ==================================================
    @staticmethod
    def _build_pattern(keywords: list[str]) -> re.Pattern | None:
        if not keywords:
            return None

        cleaned = [k for k in keywords if k]
        if not cleaned:
            return None

        t0 = time.monotonic()
        try:
            parts = [
                rf"(?<!{_WB}){re.escape(k)}(?!{_WB})"
                for k in cleaned
            ]
            pat = re.compile("|".join(parts), re.IGNORECASE)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > _SLOW_PATTERN_MS:
                log_warning(
                    f"🐌 [KF:pattern] ساخت الگو کند | {len(cleaned)} کلمه | "
                    f"{elapsed_ms:.0f}ms (آستانه {_SLOW_PATTERN_MS}ms)"
                )
            return pat
        except re.error as e:
            log_warning(
                f"⚠️ [KF:pattern] ساخت با مرز ناموفق — تلاش بدون مرز | "
                f"{type(e).__name__}: {e}"
            )
            try:
                return re.compile(
                    "|".join(re.escape(k) for k in cleaned),
                    re.IGNORECASE,
                )
            except Exception as e2:
                log_error(
                    f"❌ [KF:pattern] fallback هم ناموفق | "
                    f"{type(e2).__name__}: {e2}"
                )
                return None

    # ==================================================
    # _load
    # ==================================================
    def _load(self, initial: bool = False):
        """بارگذاری کامل — از منابع تا ساخت الگو."""
        t0 = time.monotonic()
        prev_count = len(self.keywords)

        raw = self._collect_keywords()

        # یکتا + strip
        cleaned = set()
        for k in raw:
            if isinstance(k, str):
                s = k.strip()
                if s:
                    cleaned.add(s)

        # افزودن توکنِ برند (اختیاری)
        n_tokens = 0
        if _BRAND_TOKEN_ENABLED:
            tokens = set()
            for name in cleaned:
                parts = name.split()
                if parts and len(parts[0]) >= _BRAND_TOKEN_MIN_LEN:
                    tokens.add(parts[0])
            n_tokens = len(tokens - cleaned)
            cleaned |= tokens
            if n_tokens:
                log_info(
                    f"🏷️ [KF:brand] {n_tokens} توکن برند اضافه شد "
                    f"(min_len={_BRAND_TOKEN_MIN_LEN})"
                )

        # مرتب: بلندترین اول (برای regex alternation اهمیت داره)
        self.keywords = sorted(cleaned, key=lambda s: (-len(s), s))
        self.pattern = self._build_pattern(self.keywords)

        # امضای جدید
        self._signature = self._signature_now()
        self._last_check = time.monotonic()

        elapsed_ms = (time.monotonic() - t0) * 1000
        pattern_status = "✅" if self.pattern else "❌"

        if initial:
            log_success(
                f"✅ [KF:load] init | {len(self.keywords)} کلیدواژه | "
                f"pattern={pattern_status} | brand_tokens=+{n_tokens} | "
                f"{elapsed_ms:.0f}ms"
            )
        else:
            delta = len(self.keywords) - prev_count
            sign = "+" if delta >= 0 else ""
            log_success(
                f"♻️ [KF:reload] {prev_count} → {len(self.keywords)} "
                f"({sign}{delta}) | pattern={pattern_status} | "
                f"brand_tokens=+{n_tokens} | {elapsed_ms:.0f}ms"
            )

        if elapsed_ms > _SLOW_LOAD_MS:
            log_warning(
                f"🐌 [KF:load] کند | {elapsed_ms:.0f}ms "
                f"(آستانه {_SLOW_LOAD_MS}ms)"
            )

    def _maybe_reload(self):
        """چک mtime — اگه تغییری بوده reload کن."""
        now = time.monotonic()
        if now - self._last_check < _RELOAD_CHECK_SECONDS:
            return
        self._last_check = now

        new_sig = self._signature_now()
        if new_sig == self._signature:
            return

        changed = self._diff_signatures(self._signature, new_sig)
        log_info(
            f"🔄 [KF:reload] تغییر شناسایی شد | "
            f"{len(changed)} فایل: {', '.join(changed[:5])}"
            + (
                f" و {len(changed) - 5} دیگر"
                if len(changed) > 5 else ""
            )
        )
        self._load(initial=False)

    @staticmethod
    def _diff_signatures(old_sig, new_sig) -> list[str]:
        """کدام فایل‌ها عوض شده‌اند (نام فقط)."""
        if not old_sig or not new_sig:
            return []
        old_map = dict(old_sig)
        changed: list[str] = []
        for path, mtime in new_sig:
            if old_map.get(path) != mtime:
                changed.append(os.path.basename(path))
        return changed

    # ==================================================
    # تطبیق
    # ==================================================
    def match(self, text: str | None) -> list[str]:
        """فهرست کلیدواژه‌های یافت‌شده (مرتب و یکتا)."""
        self._maybe_reload()
        if not self.pattern or not text:
            return []
        found = {m for m in self.pattern.findall(text) if m}
        return sorted(found)

    def has_match(self, text: str | None) -> bool:
        """آیا متن حداقل یک کلیدواژه دارد؟ (سریع)"""
        self._maybe_reload()
        if not self.pattern or not text:
            return False
        return self.pattern.search(text) is not None

    # ==================================================
    # رابط آنالیزور
    # ==================================================
    async def analyze(self, message: AnalyzedMessage) -> AnalyzedMessage:
        """تحلیل پیام — لاگ debug در صورت یافتن کلیدواژه."""
        message.keywords = self.match(message.text or "")
        return message

    # ==================================================
    # ابزار عیب‌یابی (اختیاری — برای debug)
    # ==================================================
    def get_stats(self) -> dict:
        """خلاصهٔ وضعیت فعلی — برای /debug یا admin handler."""
        return {
            "keywords": len(self.keywords),
            "pattern_ok": self.pattern is not None,
            "brand_token": _BRAND_TOKEN_ENABLED,
            "data_dir": self.data_dir,
            "source_files": len(self._source_files()),
            "excluded_json": sorted(self._excluded_json),
            "last_check_ago_s": round(
                time.monotonic() - self._last_check, 1
            ),
        }


# ==================================================
# instance مشترک سراسری
# ==================================================
_shared: KeywordFinder | None = None


def get_keyword_finder(data_dir: str = "data") -> KeywordFinder:
    global _shared
    if _shared is None:
        _shared = KeywordFinder(data_dir)
    return _shared
