# analyzers/keyword_finder.py
"""
یابندهٔ کلیدواژه — منبع یکتای کلیدواژه‌ها برای کل سیستم.

منابع کلیدواژه:
  - data/products.py       → ALL_PRODUCTS (لیست تخت محصولات فارسی)
  - data/products_en.py    → ALL_PRODUCTS_EN (لیست تخت محصولات انگلیسی)
  - data/runtime/keywords_custom.json → کلیدهای داخل "keywords" (دستی، با tier)

⚠️ برندها کلیدواژه نیستن — فقط از طریق محصولات پیدا می‌شن.

کارایی:
  - فایل‌ها فقط وقتی دوباره خوانده می‌شوند که mtime عوض شده باشه.
  - بررسی mtime حداکثر هر KEYWORDS_RELOAD_SECONDS.
  - الگوی regex با مرزِ کلمه ساخته می‌شه تا «یاراترا» داخل «یاراترایی» match نشه.

نسخهٔ ۱۰ (این نسخه):
  - 🎯 `_BRAND_TOKEN_ENABLED` و `_BRAND_TOKEN_MIN_LEN` حالا از
    `config.py` (top-level) می‌آیند — به‌جای PLUGINS_CONFIG.
  - 🎯 `_RELOAD_CHECK_SECONDS` حالا از `config.KEYWORDS_CONFIG` می‌آید
    (نه `os.getenv` مستقیم).
  - 🎯 `_SLOW_LOAD_MS` و `_SLOW_PATTERN_MS` حالا از env قابل تنظیم.
  - 🐛 FIX: `_NON_KEYWORD_JSON` حذف شد (بی‌استفاده بود — منطق
    `_KEYWORD_JSON_PREFIX` و `_excluded_json` جایگزینش شده).
  - 🎯 `data_dir` پیش‌فرض حالا از `data.paths.DATA_DIR` می‌آید
    (نه رشتهٔ "data").
  - 🎯 `get_stats()` حالا شامل اطلاعات env-based thresholds هم هست.
  - 🎯 `_MIN_KEYWORD_LEN`, `_MAX_KEYWORD_LEN` حالا env-based.
  - 🎯 لاگ init کوتاه‌تر + کامل‌تر.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۹.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import time

# 🎯 P0: استفاده از config برای top-level params
from config import (
    BRAND_TOKEN_ENABLED,
    BRAND_TOKEN_MIN_LEN,
    KEYWORDS_CONFIG,
)
from core.contracts.base_analyzer import BaseAnalyzer
from core.kernel.logger import log_error, log_info, log_success, log_warning
from core.kernel.models import AnalyzedMessage

# 🎯 P0: مسیرها از data.paths
from data import paths as data_paths


# ═══════════════════════════════════════════════════════════
# helpers (env-based)
# ═══════════════════════════════════════════════════════════
def _env_int(
    name: str, default: int,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    """خواندن int از env — با sanitize."""
    try:
        v = int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


# ═══════════════════════════════════════════════════════════
# ثابت‌ها
# ═══════════════════════════════════════════════════════════

# 🎯 FIX: از config.KEYWORDS_CONFIG (نه os.getenv مستقیم)
_RELOAD_CHECK_SECONDS: float = float(
    KEYWORDS_CONFIG.get("reload_seconds", 120.0)
)

# 🎯 FIX: env-based
_SLOW_LOAD_MS = _env_int(
    "KEYWORDS_SLOW_LOAD_MS", 300, min_val=50, max_val=60_000
)
_SLOW_PATTERN_MS = _env_int(
    "KEYWORDS_SLOW_PATTERN_MS", 200, min_val=50, max_val=60_000
)

# کاراکتر کلمه (برای مرز)
_WB = r"\w"

# 🎯 FIX #13: فقط فایل‌های با این پیشوند به‌عنوان منبع کلیدواژه قبول می‌شن
_KEYWORD_JSON_PREFIX = "keywords_"

# 🎯 FIX: env-based
_MIN_KEYWORD_LEN = _env_int(
    "KEYWORDS_MIN_LEN", 2, min_val=1, max_val=100
)
_MAX_KEYWORD_LEN = _env_int(
    "KEYWORDS_MAX_LEN", 120, min_val=10, max_val=10_000
)


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════
def _get_excluded_json_files() -> set[str]:
    """فایل‌های JSON که نباید به‌عنوان منبع کلیدواژهٔ عمومی خونده بشن.

    keywords_custom.json قبلاً دوبار خونده می‌شد:
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


def _default_data_dir() -> str:
    """🎯 FIX: مسیر پیش‌فرض data — از data.paths."""
    try:
        return str(data_paths.DATA_DIR)
    except Exception:
        return "data"


# ═══════════════════════════════════════════════════════════
# KeywordFinder
# ═══════════════════════════════════════════════════════════
class KeywordFinder(BaseAnalyzer):
    """یابندهٔ کلیدواژه — منبع یکتای کلیدواژه‌ها."""

    def __init__(self, data_dir: str | None = None):
        # 🎯 FIX: data_dir از data.paths اگر ندهند
        self.data_dir = data_dir or _default_data_dir()
        self.keywords: list[str] = []
        self.pattern: re.Pattern | None = None
        self._signature: tuple | None = None
        self._last_check: float = 0.0
        # cache برای فایل‌های exclude
        self._excluded_json: set[str] = _get_excluded_json_files()

        log_info(
            f"🔍 [KF:init] شروع | data_dir={self.data_dir} | "
            f"brand_token={BRAND_TOKEN_ENABLED} "
            f"(min_len={BRAND_TOKEN_MIN_LEN}) | "
            f"reload_check={_RELOAD_CHECK_SECONDS:.1f}s | "
            f"excluded={sorted(self._excluded_json)}"
        )
        self._load(initial=True)

    # ═══════════════════════════════════════════
    # منابع و امضای تغییر
    # ═══════════════════════════════════════════
    def _json_files(self) -> list[str]:
        """فقط فایل‌های JSON که «منبع کلیدواژهٔ عمومی» هستند.

        keywords_custom.json رو exclude می‌کنه (چون جداگانه خونده میشه).
        فقط فایل‌های با پیشوند `keywords_` قبول می‌شن.
        """
        out: list[str] = []
        if not os.path.isdir(self.data_dir):
            log_warning(
                f"⚠️ [KF:_json_files] پوشه وجود ندارد: {self.data_dir}"
            )
            return out

        for fn in sorted(os.listdir(self.data_dir)):
            if not fn.endswith(".json"):
                continue
            # فایل‌های exclude (keywords_custom) رو رد کن
            if fn in self._excluded_json:
                log_info(
                    f"⏭️ [KF:_json_files] {fn} رد شد "
                    f"(جداگانه خونده میشه)"
                )
                continue
            # فقط فایل‌های با پیشوند keywords_ قبول می‌شن
            if not fn.startswith(_KEYWORD_JSON_PREFIX):
                log_info(
                    f"⏭️ [KF:_json_files] {fn} رد شد "
                    f"(پیشوند keywords_ ندارد)"
                )
                continue
            out.append(os.path.join(self.data_dir, fn))
        return out

    def _source_files(self) -> list[str]:
        """همهٔ فایل‌هایی که mtimeشان باید چک بشه."""
        files = [
            str(data_paths.PRODUCTS_PY),
            str(data_paths.PRODUCTS_EN_PY),
            str(data_paths.KEYWORDS_CUSTOM),
        ]
        files.extend(self._json_files())
        return files

    def _signature_now(self) -> tuple:
        """امضای mtime همهٔ فایل‌های منبع."""
        sig = []
        for p in self._source_files():
            try:
                sig.append((p, os.path.getmtime(p)))
            except OSError:
                sig.append((p, None))
        return tuple(sig)

    # ═══════════════════════════════════════════
    # جمع‌آوری کلیدواژه‌ها
    # ═══════════════════════════════════════════
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
            en_words = self._import_list(
                "data.products_en", "ALL_PRODUCTS_EN"
            )
            n_en = len(en_words)
            words += en_words
        except Exception as e:
            log_error(
                f"❌ [KF:_collect] products_en.py | "
                f"{type(e).__name__}: {e}"
            )

        # ۳) keywords_custom.json — اختصاصی
        custom = self._read_custom_keywords()
        n_custom = len(custom)
        words += custom

        # ۴) سایر فایل‌های JSON مجاز (keywords_*.json)
        n_extra = 0
        extra_files = self._json_files()
        for path in extra_files:
            got = self._extract_strings_from_json(path)
            n_extra += len(got)
            words += got

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_info(
            f"📚 [KF:_collect] {len(words)} کلمه | "
            f"fa={n_fa} en={n_en} custom={n_custom} "
            f"extra={n_extra} ({len(extra_files)} فایل) | "
            f"{elapsed_ms:.0f}ms"
        )

        if elapsed_ms > _SLOW_LOAD_MS:
            log_warning(
                f"🐌 [KF:_collect] کند | {elapsed_ms:.0f}ms"
            )
        return words

    def _read_custom_keywords(self) -> list[str]:
        """کلیدواژه‌های دستی از keywords_custom.json.

        پشتیبانی از دو فرمت:
          - لیست خالص (قدیمی): ["کود", "سم"]
          - دیکشنری با کلید "keywords": {"keywords": {"کود": {"tier": "T1"}}}

        ⚠️ اگر dict بود ولی کلید "keywords" نداشت → هیچ کلیدواژه‌ای برنمی‌گرداند
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
                    f"⚠️ [KF:custom] dict بدون کلید «keywords» | "
                    f"{path} | "
                    f"کلیدهای موجود: {list(data.keys())[:5]}"
                )

        log_info(
            f"📂 [KF:custom] {len(out)} کلیدواژه از فرمت «{fmt}» | "
            f"{os.path.basename(path)}"
        )
        return out

    @staticmethod
    def _is_keyword_like(s: str) -> bool:
        """🎯 FIX #13: آیا این رشته شبیه کلیدواژه است (نه متادیتا)؟"""
        if not s or len(s) < _MIN_KEYWORD_LEN:
            return False
        if len(s) > _MAX_KEYWORD_LEN:
            return False
        # اعداد خالص
        if re.fullmatch(r"[\d.,:/\-\s]+", s):
            return False
        # تاریخ ISO
        if re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}", s):
            return False
        # hex/uuid
        if re.fullmatch(
            r"[0-9a-fA-F]{8}(-?[0-9a-fA-F]{4}){3}-?[0-9a-fA-F]{12}",
            s,
        ):
            return False
        return True

    @staticmethod
    def _extract_strings_from_json(path: str) -> list[str]:
        """استخراج رشته‌های شبیه کلیدواژه از یک فایل JSON عمومی.

        dict تودرتو (nested) رو هم می‌گرده.
        """
        out: list[str] = []
        fn = os.path.basename(path)

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            log_warning(
                f"⚠️ [KF:extra] خواندن {fn} ناموفق — "
                f"نادیده گرفته شد | {type(e).__name__}: {e}"
            )
            return out

        def _walk(obj, depth: int = 0) -> None:
            if depth > 5:
                return
            if isinstance(obj, str):
                s = obj.strip()
                if KeywordFinder._is_keyword_like(s):
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
                f"⚠️ [KF:import] {module_path}.{attr} لیست نیست | "
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

    # ═══════════════════════════════════════════
    # ساخت الگو (با مرزِ کلمه)
    # ═══════════════════════════════════════════
    @staticmethod
    def _build_pattern(keywords: list[str]) -> re.Pattern | None:
        """ساخت regex با مرزِ کلمه — با fallback بدون مرز."""
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
                    f"🐌 [KF:pattern] ساخت الگو کند | "
                    f"{len(cleaned)} کلمه | {elapsed_ms:.0f}ms"
                )
            return pat
        except re.error as e:
            log_warning(
                f"⚠️ [KF:pattern] مرز ناموفق — تلاش بدون مرز | "
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

    # ═══════════════════════════════════════════
    # _load
    # ═══════════════════════════════════════════
    def _load(self, initial: bool = False):
        """بارگذاری کامل — از منابع تا ساخت الگو."""
        t0 = time.monotonic()
        prev_count = len(self.keywords)

        raw = self._collect_keywords()

        # یکتا + strip
        cleaned: set[str] = set()
        for k in raw:
            if isinstance(k, str):
                s = k.strip()
                if s:
                    cleaned.add(s)

        # افزودن توکنِ برند (اختیاری)
        n_tokens = 0
        if BRAND_TOKEN_ENABLED:
            tokens = set()
            for name in cleaned:
                parts = name.split()
                if (
                    parts
                    and len(parts[0]) >= BRAND_TOKEN_MIN_LEN
                ):
                    tokens.add(parts[0])
            n_tokens = len(tokens - cleaned)
            cleaned |= tokens
            if n_tokens:
                log_info(
                    f"🏷️ [KF:brand] {n_tokens} توکن برند اضافه شد "
                    f"(min_len={BRAND_TOKEN_MIN_LEN})"
                )

        # مرتب: بلندترین اول
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
                f"pattern={pattern_status} | "
                f"brand_tokens=+{n_tokens} | "
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
                f"🐌 [KF:load] کند | {elapsed_ms:.0f}ms"
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
            f"🔄 [KF:reload] تغییر | "
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

    # ═══════════════════════════════════════════
    # تطبیق
    # ═══════════════════════════════════════════
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

    # ═══════════════════════════════════════════
    # رابط آنالیزور
    # ═══════════════════════════════════════════
    async def analyze(self, message: AnalyzedMessage) -> AnalyzedMessage:
        """تحلیل پیام — یافتن کلیدواژه‌ها."""
        message.keywords = self.match(message.text or "")
        return message

    # ═══════════════════════════════════════════
    # ابزار عیب‌یابی
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict:
        """🎯 خلاصهٔ وضعیت فعلی — برای /debug یا admin handler."""
        return {
            "keywords": len(self.keywords),
            "pattern_ok": self.pattern is not None,
            "brand_token": BRAND_TOKEN_ENABLED,
            "brand_token_min_len": BRAND_TOKEN_MIN_LEN,
            "data_dir": self.data_dir,
            "source_files": len(self._source_files()),
            "excluded_json": sorted(self._excluded_json),
            "last_check_ago_s": round(
                time.monotonic() - self._last_check, 1
            ),
            # 🎯 NEW: env-based thresholds
            "reload_check_seconds": _RELOAD_CHECK_SECONDS,
            "slow_load_ms": _SLOW_LOAD_MS,
            "slow_pattern_ms": _SLOW_PATTERN_MS,
            "min_keyword_len": _MIN_KEYWORD_LEN,
            "max_keyword_len": _MAX_KEYWORD_LEN,
        }


# ═══════════════════════════════════════════════════════════
# instance مشترک سراسری
# ═══════════════════════════════════════════════════════════
_shared: KeywordFinder | None = None


def get_keyword_finder(
    data_dir: str | None = None,
) -> KeywordFinder:
    """Singleton — instance مشترک سراسری.

    Args:
        data_dir: مسیر پوشهٔ data (اختیاری — از data.paths می‌آید).
    """
    global _shared
    if _shared is None:
        _shared = KeywordFinder(data_dir)
    return _shared


def reset_keyword_finder() -> None:
    """🎯 پاک‌کردن singleton — فقط برای تست.

    ⚠️ در runtime صدا نزن — ممکنه referenceهای موجود بی‌اعتبار بشن.
    """
    global _shared
    _shared = None
    log_info("🗑️ [KF:singleton] reset شد")
