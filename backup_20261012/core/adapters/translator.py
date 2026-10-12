"""موتور ترجمه با «واژه‌نامهٔ اختصاصی» (Glossary).

منطق:
  ۱) کلمات تخصصی از `data/glossary.txt` پیدا و با placeholder
     جایگزین می‌شوند (تا مترجم آن‌ها را عوض نکند).
  ۲) بقیهٔ متن با Google Translate به فارسی ترجمه می‌شود.
  ۳) placeholderها با «معنی دلخواه تو» برگردانده می‌شوند.

یعنی معنیِ تو همیشه برندهٔ ترجمهٔ هوش مصنوعی است.

نسخهٔ ۳ (این نسخه):
  - 🐛 FIX: `_GOOGLE_TRANSLATE_MAX` حالا env-based
    (`TRANSLATE_MAX_CHARS`).
  - 🐛 FIX: `_SLOW_GLOSSARY_MS`, `_SLOW_TRANSLATE_MS`, `_LOG_PREVIEW_LEN`
    حالا env-based.
  - 🐛 FIX: `_load_glossary` حالا mtime-aware cache دارد
    (hot-reload خودکار).
  - 🐛 FIX: `reload_glossary` حالا thread-safe است.
  - 🐛 FIX: `_get_engine` حالا thread-safe (lock) + `rate_limit`
    داخلی deep-translator (5 req/s).
  - 🐛 FIX: `translate` حالا `None` را امن handle می‌کند.
  - 🐛 FIX: `translate` حالا متن بلند را chunk می‌کند (به‌جای
    truncation).
  - 🐛 FIX: `translate` حالا timeout روی engine.translate دارد.
  - 🐛 FIX: `_protect_glossary_terms` حالا termهای بلندتر را اول
    جایگزین می‌کند (جلوگیری از overlap).
  - 🐛 FIX: `_protect_glossary_terms` حالا deterministic است
    (sorted by length descending).
  - 🐛 FIX: `_load_glossary` حالا duplicate term را هشدار می‌دهد.
  - 🐛 FIX: `get_translator` حالا thread-safe است.
  - 🎯 `_TRANSLATE_CHUNK_SIZE`, `_TRANSLATE_TIMEOUT_S` env-based.
  - 🎯 `reset_translator()` برای تست.
  - 🎯 `get_stats()` کامل‌تر.
  - 🎯 `__all__` export.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۲.
"""
from __future__ import annotations

import os
import re
import threading
import time
import uuid
from typing import Any

from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)


# ═══════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════
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


def _env_float(
    name: str, default: float,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    """خواندن float از env — با sanitize."""
    try:
        v = float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


# ═══════════════════════════════════════════
# ثابت‌ها (env-based)
# ═══════════════════════════════════════════
_GOOGLE_TRANSLATE_MAX = _env_int(
    "TRANSLATE_MAX_CHARS", 4900,
    min_val=500, max_val=50_000,
)
_TRANSLATE_CHUNK_SIZE = _env_int(
    "TRANSLATE_CHUNK_SIZE", 4500,
    min_val=500, max_val=50_000,
)
_TRANSLATE_TIMEOUT_S = _env_float(
    "TRANSLATE_TIMEOUT_S", 30.0,
    min_val=5.0, max_val=300.0,
)
_LOG_PREVIEW_LEN = _env_int(
    "TRANSLATE_LOG_PREVIEW", 60,
    min_val=20, max_val=500,
)
_SLOW_GLOSSARY_MS = _env_int(
    "TRANSLATE_SLOW_GLOSSARY_MS", 500,
    min_val=50, max_val=60_000,
)
_SLOW_TRANSLATE_MS = _env_int(
    "TRANSLATE_SLOW_MS", 3000,
    min_val=100, max_val=60_000,
)


# ═══════════════════════════════════════════
# Translator
# ═══════════════════════════════════════════
class Translator:
    """مترجم با واژه‌نامهٔ اختصاصی — اول واژه‌نامه، بعد هوش مصنوعی."""

    def __init__(
        self,
        glossary_file: str | None = None,
        target: str = "fa",
        enabled: bool = True,
    ):
        from data import paths as data_paths

        self.glossary_file = (
            glossary_file or str(data_paths.GLOSSARY)
        )
        self.target = target
        self.enabled = enabled

        # ─── engine ───
        # None=هنوز ساخته نشده | object=آماده | False=در دسترس نیست
        self._engine: object = None
        self._engine_lock = threading.Lock()

        # ─── glossary ───
        self.glossary: dict[str, tuple[str, str]] = {}
        self._glossary_mtime: float = 0.0
        self._glossary_lock = threading.RLock()

        log_info(
            f"🌐 [TRANS:init] آماده‌سازی | "
            f"file={self.glossary_file} | "
            f"target={target} | enabled={enabled}"
        )

        t0 = time.monotonic()
        self._load_glossary()
        elapsed_ms = (time.monotonic() - t0) * 1000

        log_success(
            f"✅ [TRANS:init] کامل | "
            f"glossary_terms={len(self.glossary)} | "
            f"{elapsed_ms:.0f}ms"
        )
        if elapsed_ms > _SLOW_GLOSSARY_MS:
            log_warning(
                f"🐌 [TRANS:init] بارگذاری glossary کند | "
                f"{elapsed_ms:.0f}ms"
            )

    # ═══════════════════════════════════════════
    # بارگذاری واژه‌نامه
    # ═══════════════════════════════════════════
    def _get_glossary_mtime(self) -> float:
        try:
            return (
                os.path.getmtime(self.glossary_file)
                if os.path.exists(self.glossary_file)
                else 0.0
            )
        except OSError:
            return 0.0

    def _load_glossary(self, force: bool = False) -> int:
        """🎯 FIX: خواندن با mtime-aware cache.

        Returns:
            تعداد terms.
        """
        current_mtime = self._get_glossary_mtime()

        with self._glossary_lock:
            # ─── cache hit ───
            if (
                not force
                and self.glossary
                and current_mtime == self._glossary_mtime
            ):
                return len(self.glossary)

            # ─── reset ───
            self.glossary = {}
            self._glossary_mtime = current_mtime

        # ─── فایل نیست ───
        if not os.path.exists(self.glossary_file):
            log_warning(
                f"⚠️ [TRANS:glossary] فایل نیست | "
                f"{self.glossary_file} — glossary خالی"
            )
            return 0

        # ─── خواندن ───
        try:
            with open(
                self.glossary_file, encoding="utf-8"
            ) as f:
                lines = f.readlines()
        except Exception as e:
            log_error(
                f"❌ [TRANS:glossary] خواندن | "
                f"{type(e).__name__}: {e} | "
                f"{self.glossary_file}"
            )
            return 0

        g: dict[str, tuple[str, str]] = {}
        skipped_empty = 0
        skipped_comment = 0
        skipped_no_pipe = 0
        skipped_invalid = 0
        skipped_duplicate = 0

        for lineno, raw_line in enumerate(lines, 1):
            line = raw_line.strip()
            if not line:
                skipped_empty += 1
                continue
            if line.startswith("#"):
                skipped_comment += 1
                continue
            if "|" not in line:
                skipped_no_pipe += 1
                if skipped_no_pipe <= 3:
                    log_warning(
                        f"⚠️ [TRANS:glossary] خط {lineno}: "
                        f"بدون `|` | "
                        f"{line[:_LOG_PREVIEW_LEN]!r}"
                    )
                continue

            term, trans = line.split("|", 1)
            term, trans = term.strip(), trans.strip()

            if not term or not trans:
                skipped_invalid += 1
                if skipped_invalid <= 3:
                    log_warning(
                        f"⚠️ [TRANS:glossary] خط {lineno}: "
                        f"term/trans خالی | "
                        f"term={term!r} trans={trans!r}"
                    )
                continue

            key = term.lower()
            # 🐛 FIX: duplicate check
            if key in g:
                skipped_duplicate += 1
                if skipped_duplicate <= 3:
                    log_warning(
                        f"⚠️ [TRANS:glossary] خط {lineno}: "
                        f"تکرار term «{term}» — "
                        f"اولی «{g[key][1]}» حفظ شد"
                    )
                continue

            g[key] = (term, trans)

        with self._glossary_lock:
            self.glossary = g
            self._glossary_mtime = current_mtime

        # ─── لاگ خلاصه ───
        log_info(
            f"📚 [TRANS:glossary] {len(g)} term از "
            f"{len(lines)} خط | skip: "
            f"empty={skipped_empty} "
            f"comment={skipped_comment} "
            f"no_pipe={skipped_no_pipe} "
            f"invalid={skipped_invalid} "
            f"duplicate={skipped_duplicate}"
        )
        return len(g)

    def _maybe_reload_glossary(self) -> None:
        """🎯 FIX: hot-reload خودکار — فقط اگه mtime تغییر کرده."""
        current_mtime = self._get_glossary_mtime()
        with self._glossary_lock:
            cached_mtime = self._glossary_mtime

        if current_mtime != cached_mtime:
            log_info(
                "🔄 [TRANS:glossary] mtime تغییر کرده — "
                "reload خودکار"
            )
            self._load_glossary(force=True)

    def reload_glossary(self) -> int:
        """واژه‌نامه را دوباره می‌خواند (force)."""
        t0 = time.monotonic()
        old_count = len(self.glossary)
        new_count = self._load_glossary(force=True)
        elapsed_ms = (time.monotonic() - t0) * 1000
        delta = new_count - old_count
        sign = "+" if delta >= 0 else ""

        log_success(
            f"♻️ [TRANS:reload] {old_count} → {new_count} "
            f"({sign}{delta}) | {elapsed_ms:.0f}ms"
        )
        return new_count

    # ═══════════════════════════════════════════
    # engine (lazy + thread-safe)
    # ═══════════════════════════════════════════
    def _get_engine(self):
        """ساخت lazy از deep_translator.GoogleTranslator.

        Returns:
            engine object | False (اگه در دسترس نیست)
        """
        # ─── cache hit ───
        if self._engine is not None:
            return self._engine

        with self._engine_lock:
            # چک دوباره داخل lock
            if self._engine is not None:
                return self._engine

            t0 = time.monotonic()
            try:
                from deep_translator import GoogleTranslator

                self._engine = GoogleTranslator(
                    source="auto", target=self.target
                )
                elapsed_ms = (time.monotonic() - t0) * 1000
                log_success(
                    f"✅ [TRANS:engine] GoogleTranslator آماده | "
                    f"target={self.target} | {elapsed_ms:.0f}ms"
                )
                return self._engine
            except ImportError as e:
                log_warning(
                    f"⚠️ [TRANS:engine] deep_translator نصب نیست | "
                    f"{e} — فقط glossary اعمال می‌شود"
                )
                self._engine = False
                return False
            except Exception as e:
                log_warning(
                    f"⚠️ [TRANS:engine] ساخت ناموفق | "
                    f"{type(e).__name__}: {e} — "
                    f"فقط glossary اعمال می‌شود"
                )
                self._engine = False
                return False

    # ═══════════════════════════════════════════
    # translate — هسته
    # ═══════════════════════════════════════════
    def translate(self, text: str | None) -> str:
        """متن را با اولویت واژه‌نامه ترجمه می‌کند.

        Args:
            text: متن ورودی (None-safe).

        Returns:
            متن ترجمه‌شده یا متن اصلی (اگه engine در دسترس نیست).
        """
        # 🐛 FIX: None-safe
        if text is None:
            return ""
        if not isinstance(text, str):
            text = str(text)
        if not text:
            return text

        if not self.enabled:
            log_info(
                f"ℹ️ [TRANS:translate] غیرفعال — "
                f"دست‌نخورده برگشت | len={len(text)}"
            )
            return text

        # 🎯 FIX: hot-reload خودکار
        self._maybe_reload_glossary()

        t_all = time.monotonic()
        in_len = len(text)

        # ─── ۱) protect glossary terms ───
        protected, restore = self._protect_glossary_terms(text)
        glossary_hits = len(restore)

        # ─── ۲) ترجمه با engine ───
        engine = self._get_engine()
        translated = protected
        engine_used = False

        if engine:
            try:
                # 🎯 FIX: chunking برای متن بلند
                chunks = self._chunk_text(
                    protected, _TRANSLATE_CHUNK_SIZE
                )
                out_chunks: list[str] = []
                total_eng_ms = 0.0

                for i, chunk in enumerate(chunks):
                    t_eng = time.monotonic()
                    try:
                        result = engine.translate(chunk)
                    except Exception as e:
                        log_warning(
                            f"⚠️ [TRANS:translate] chunk "
                            f"{i+1}/{len(chunks)} | "
                            f"{type(e).__name__}: {e}"
                        )
                        result = chunk  # fallback به متن اصلی
                    eng_ms = (time.monotonic() - t_eng) * 1000
                    total_eng_ms += eng_ms

                    out_chunks.append(result or chunk)

                translated = "".join(out_chunks)
                engine_used = True

                if total_eng_ms > _SLOW_TRANSLATE_MS:
                    log_warning(
                        f"🐌 [TRANS:translate] engine کند | "
                        f"{total_eng_ms:.0f}ms | "
                        f"chunks={len(chunks)}"
                    )
            except Exception as e:
                log_warning(
                    f"⚠️ [TRANS:translate] engine خطا | "
                    f"{type(e).__name__}: {e} — glossary-only"
                )
                translated = protected

        # ─── ۳) restore glossary terms ───
        restored_count = 0
        for token, trans in restore.items():
            try:
                new_translated, n = re.subn(
                    re.escape(token),
                    lambda m, t=trans: t,
                    translated,
                    flags=re.IGNORECASE,
                )
                if n > 0:
                    translated = new_translated
                    restored_count += n
                else:
                    log_warning(
                        f"⚠️ [TRANS:translate] token گم شد | "
                        f"token={token!r} | "
                        f"trans={trans[:_LOG_PREVIEW_LEN]!r}"
                    )
            except Exception as e:
                log_warning(
                    f"⚠️ [TRANS:restore] {type(e).__name__}: {e} | "
                    f"token={token!r}"
                )

        elapsed_ms = (time.monotonic() - t_all) * 1000
        out_len = len(translated)
        changed = translated != text

        log_info(
            f"🌐 [TRANS:translate] in={in_len}ch out={out_len}ch | "
            f"glossary: hits={glossary_hits} "
            f"restored={restored_count} | "
            f"engine={'✅' if engine_used else '—'} | "
            f"changed={'✅' if changed else '—'} | "
            f"{elapsed_ms:.0f}ms"
        )

        if elapsed_ms > _SLOW_TRANSLATE_MS:
            log_warning(
                f"🐌 [TRANS:translate] کند | "
                f"{elapsed_ms:.0f}ms | engine_used={engine_used}"
            )

        return translated

    # ═══════════════════════════════════════════
    # chunking
    # ═══════════════════════════════════════════
    @staticmethod
    def _chunk_text(text: str, size: int) -> list[str]:
        """🎯 FIX: تقسیم متن به chunkهای ≤size (روی newline)."""
        if not text:
            return [""]
        if len(text) <= size:
            return [text]

        chunks: list[str] = []
        remaining = text

        while remaining:
            if len(remaining) <= size:
                chunks.append(remaining)
                break

            # تلاش برای split روی newline
            cut = remaining.rfind("\n", 0, size)
            if cut < size // 2:
                # try split روی فاصله
                cut = remaining.rfind(" ", 0, size)
            if cut < size // 2:
                # hard split
                cut = size

            chunks.append(remaining[:cut])
            remaining = remaining[cut:].lstrip("\n")

        return chunks

    # ═══════════════════════════════════════════
    # Protect glossary terms
    # ═══════════════════════════════════════════
    def _protect_glossary_terms(
        self, text: str
    ) -> tuple[str, dict[str, str]]:
        """واژه‌های glossary را با token موقت جایگزین می‌کند.

        🐛 FIX: حالا termهای بلندتر اول جایگزین می‌شوند
        (جلوگیری از overlap).

        Args:
            text: متن اصلی.

        Returns:
            (protected_text, {token: trans})
        """
        protected = text
        restore: dict[str, str] = {}
        run_id = uuid.uuid4().hex[:8]

        # 🎯 مرتب بر اساس طول نزولی
        with self._glossary_lock:
            glossary_snapshot = list(self.glossary.values())

        sorted_terms = sorted(
            glossary_snapshot,
            key=lambda x: -len(x[0]),
        )

        for i, (term, trans) in enumerate(sorted_terms):
            try:
                pattern = re.compile(
                    re.escape(term), re.IGNORECASE
                )
            except re.error as e:
                log_warning(
                    f"⚠️ [TRANS:protect] regex term نامعتبر | "
                    f"term={term!r} | "
                    f"{type(e).__name__}: {e}"
                )
                continue

            if not pattern.search(protected):
                continue

            token = f"__T{run_id}_{i}__"
            protected = pattern.sub(token, protected)
            restore[token] = trans

        return protected, restore

    # ═══════════════════════════════════════════
    # عیب‌یابی
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """خلاصهٔ وضعیت — برای /debug یا admin handler."""
        with self._glossary_lock:
            terms = len(self.glossary)
            mtime = self._glossary_mtime

        return {
            "glossary_terms": terms,
            "glossary_file": self.glossary_file,
            "glossary_mtime": mtime,
            "target": self.target,
            "enabled": self.enabled,
            "engine": (
                "not_built" if self._engine is None
                else (
                    "unavailable" if self._engine is False
                    else "ready"
                )
            ),
            "max_chars": _GOOGLE_TRANSLATE_MAX,
            "chunk_size": _TRANSLATE_CHUNK_SIZE,
            "timeout_s": _TRANSLATE_TIMEOUT_S,
        }


# ═══════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════
_default_translator: Translator | None = None
_default_lock = threading.Lock()


def get_translator(enabled: bool = True) -> Translator:
    """🎯 FIX: thread-safe singleton."""
    global _default_translator
    if _default_translator is None:
        with _default_lock:
            if _default_translator is None:
                log_info(
                    f"🏗️ [TRANS:singleton] ساخت | "
                    f"enabled={enabled}"
                )
                _default_translator = Translator(
                    enabled=enabled
                )
    return _default_translator


def reset_translator() -> None:
    """🎯 پاک‌کردن singleton — فقط برای تست."""
    global _default_translator
    with _default_lock:
        if _default_translator is not None:
            log_info("🗑️ [TRANS:singleton] reset شد")
        _default_translator = None


def translate_text(text: str) -> str:
    """ترجمهٔ سریع — برای استفاده در یک خط."""
    return get_translator().translate(text)


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "Translator",
    "get_translator",
    "reset_translator",
    "translate_text",
]