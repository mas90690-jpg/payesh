# analyzers/target_filter.py
"""
فیلترِ «گروه‌های هدف» — تمرکزِ عقاب فقط روی گروه‌هایی که خودت انتخاب کرده‌ای.

مشکل: بعضی پلتفرم‌ها گروه‌های تبلیغاتی را بدونِ اجازه عضوت می‌کنند یا نشانت
می‌دهند. این فیلتر باعث می‌شود ربات فقط گروه‌هایی را چک کند که تو در
`data/sources/target_groups.txt` نوشته‌ای (نام یا یوزرنیمِ گروه — تطبیقِ
بخشی و بی‌توجه به بزرگ/کوچک).

قالبِ فایل (حرفه‌ای و ساده — هر دو حالت):
  نهاده اصفهان            ← برای همهٔ پلتفرم‌ها
  eitaa: بازار کود        ← فقط ایتا (وقتی نامِ گروه در یک پلتفرم فرق دارد)
  bale: @keshavarz_group  ← فقط بله

منطق (بدونِ سورپرایز):
  - فایل خالی → همه‌جا همه‌چیز چک می‌شود.
  - یک پلتفرم که هیچ قانونی (نه سراسری، نه مخصوصِ خودش) ندارد → برای آن
    پلتفرم فیلتر خاموش است (همه‌چیزش چک می‌شود). یعنی اگر فقط برای ایتا هدف
    نوشتی، بله دست‌نخورده می‌ماند.
  - hot-reload: تغییرِ فایل بدونِ ری‌استارت اعمال می‌شود.

نسخهٔ ۳ (این نسخه):
  - 🎯 `_RELOAD_SECONDS` حالا از `config.KEYWORDS_CONFIG["reload_seconds"]`
    می‌آید (نه hardcode).
  - 🎯 `_PLATFORMS` حالا از `config.PLATFORMS_ORDER` می‌آید (شامل
    instagram).
  - 🐛 FIX: race در `_mtime` اولیه — حالا `_mtime` قبل از `_load` مقدار
    می‌گیرد.
  - 🐛 FIX: `_load()` حالا `log_warning` می‌دهد اگر فایل نبود (قبلاً
    بی‌صدا بود).
  - 🎯 `reload()` متد عمومی برای force-reload.
  - 🎯 `get_stats()` برای debug.
  - 🎯 `reset_target_groups()` برای تست.
  - 🎯 `_normalize()` helper برای نرمال‌سازی.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۲.
"""
from __future__ import annotations

import os
import time
from typing import Any

from config import KEYWORDS_CONFIG, PLATFORMS_ORDER
from core.kernel.logger import log_info, log_success, log_warning

# 🎯 P0: مسیرها از data.paths
from data import paths as data_paths


# ═══════════════════════════════════════════════════════════
# ثابت‌ها
# ═══════════════════════════════════════════════════════════

# 🎯 FIX: از config
_RELOAD_SECONDS: float = float(
    KEYWORDS_CONFIG.get("reload_seconds", 120.0)
)

# 🎯 FIX: از config.PLATFORMS_ORDER (شامل instagram)
_PLATFORMS: tuple[str, ...] = tuple(PLATFORMS_ORDER)

# آستانهٔ هشدار برای کندی load
_SLOW_LOAD_MS = 100


# ═══════════════════════════════════════════════════════════
# TargetGroups
# ═══════════════════════════════════════════════════════════
class TargetGroups:
    """فیلتر گروه‌های هدف — با hot-reload و platform-specific rules."""

    def __init__(self, path: str | None = None):
        # 🎯 FIX: مسیر پیش‌فرض از data.paths
        self.path: str = path or str(data_paths.TARGET_GROUPS)

        # لیست‌ها
        self._all: list[str] = []
        self._by_platform: dict[str, list[str]] = {}

        # state
        self._mtime: float | None = None
        self._last_check: float = 0.0
        self._loaded_at: float = time.monotonic()

        self._load(initial=True)

    # ═══════════════════════════════════════════
    # _load
    # ═══════════════════════════════════════════
    def _load(self, initial: bool = False) -> None:
        """بارگذاری فایل — با لاگ دقیق.

        فایل: هر خط یک گروه
          - سراسری: `نهاده اصفهان`
          - platform-specific: `eitaa: بازار کود`
          - @ حذف می‌شود (تا با یوزرنیم تطبیق بخورد)
          - خطوط با # نادیده
        """
        t0 = time.monotonic()
        all_t: list[str] = []
        by: dict[str, list[str]] = {}
        line_count = 0

        # 🎯 FIX: `_mtime` را اول ست کن (race fix)
        try:
            self._mtime = os.path.getmtime(self.path)
        except OSError:
            self._mtime = None

        if not os.path.exists(self.path):
            log_warning(
                f"⚠️ [TARGET:load] فایل نیست: {self.path} — "
                f"فیلتر خاموش (همه قبولند)"
            )
            self._all = []
            self._by_platform = {}
            self._loaded_at = time.monotonic()
            return

        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line_count += 1
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue

                    # ─── تشخیص platform-prefix ───
                    plat: str | None = None
                    if ":" in s:
                        head, rest = s.split(":", 1)
                        head_clean = head.strip().lower()
                        if head_clean in _PLATFORMS:
                            plat = head_clean
                            s = rest.strip()

                    # ─── normalize ───
                    s = self._normalize(s)
                    if not s:
                        continue

                    if plat:
                        by.setdefault(plat, []).append(s)
                    else:
                        all_t.append(s)

        except OSError as e:
            log_warning(
                f"⚠️ [TARGET:load] خواندن | "
                f"{type(e).__name__}: {e} | path={self.path}"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [TARGET:load] {type(e).__name__}: {e}"
            )

        # ─── ذخیره ───
        self._all = all_t
        self._by_platform = by
        self._loaded_at = time.monotonic()

        # ─── لاگ ───
        elapsed_ms = (time.monotonic() - t0) * 1000
        total = len(all_t) + sum(len(v) for v in by.values())
        by_str = ", ".join(
            f"{p}={len(v)}" for p, v in sorted(by.items())
        ) or "—"

        if initial:
            log_success(
                f"✅ [TARGET:load] init | "
                f"سراسری={len(all_t)} | per-platform={by_str} | "
                f"total={total} | {elapsed_ms:.0f}ms"
            )
        else:
            log_info(
                f"🔄 [TARGET:reload] سراسری={len(all_t)} | "
                f"per-platform={by_str} | total={total} | "
                f"{elapsed_ms:.0f}ms"
            )

        if elapsed_ms > _SLOW_LOAD_MS:
            log_warning(
                f"🐌 [TARGET:load] کند | {elapsed_ms:.0f}ms"
            )

    @staticmethod
    def _normalize(s: str) -> str:
        """🎯 FIX: نرمال‌سازی خط — حذف @ و lowercase."""
        if not s:
            return ""
        return s.strip().lstrip("@").lower()

    # ═══════════════════════════════════════════
    # hot-reload
    # ═══════════════════════════════════════════
    def _maybe_reload(self) -> None:
        """چک mtime — اگه تغییر کرده reload کن."""
        now = time.monotonic()
        if now - self._last_check < _RELOAD_SECONDS:
            return
        self._last_check = now

        try:
            current_mtime = os.path.getmtime(self.path)
        except OSError:
            current_mtime = None

        if current_mtime != self._mtime:
            log_info("🔄 [TARGET:reload] تغییر mtime — بارگذاری مجدد")
            self._load(initial=False)

    def reload(self) -> None:
        """🎯 force reload — برای تست یا admin."""
        log_info("🔄 [TARGET:reload] دستی")
        self._load(initial=False)

    # ═══════════════════════════════════════════
    # API اصلی
    # ═══════════════════════════════════════════
    def _targets_for(self, platform: str | None) -> list[str]:
        """همهٔ targetهای معتبر برای یک پلتفرم."""
        if platform:
            return self._all + self._by_platform.get(platform, [])
        return list(self._all)

    def enabled(self, platform: str | None = None) -> bool:
        """آیا برای این پلتفرم هدفی تعریف شده؟

        Args:
            platform: نام پلتفرم (اختیاری).

        Returns:
            - اگر platform داده شد: True اگر rule دارد (سراسری یا خاص).
            - اگر None: True اگر اصلاً هدفی هست.
        """
        self._maybe_reload()
        if platform:
            return bool(self._targets_for(platform))
        return bool(self._all or self._by_platform)

    def is_target(
        self,
        chat_name: str | None,
        chat_username: str | None = None,
        platform: str | None = None,
    ) -> bool:
        """آیا این گروه هدف است؟

        اگر برای این پلتفرم قانونی نباشد → True (فیلتر خاموش).

        Args:
            chat_name: نام چت.
            chat_username: یوزرنیم چت (اختیاری).
            platform: نام پلتفرم (اختیاری).

        Returns:
            True اگر گروه هدف باشد یا فیلتر خاموش باشد.
        """
        self._maybe_reload()
        targets = self._targets_for(platform)

        # فیلتر خاموش
        if not targets:
            return True

        # تطبیق بخشی
        hay = (
            (chat_name or "") + " " + (chat_username or "")
        ).lower()

        for t in targets:
            if t in hay:
                return True
        return False

    # ═══════════════════════════════════════════
    # ابزار عیب‌یابی
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت — برای debug/admin."""
        return {
            "path": self.path,
            "all_targets": len(self._all),
            "by_platform": {
                p: len(v) for p, v in self._by_platform.items()
            },
            "total_targets": (
                len(self._all)
                + sum(len(v) for v in self._by_platform.values())
            ),
            "platforms_with_rules": list(self._by_platform.keys()),
            "reload_seconds": _RELOAD_SECONDS,
            "last_check_ago_s": round(
                time.monotonic() - self._last_check, 1
            ),
            "loaded_at_ago_s": round(
                time.monotonic() - self._loaded_at, 1
            ),
        }

    def get_targets(
        self, platform: str | None = None
    ) -> list[str]:
        """🎯 لیست همهٔ targetها (برای نمایش در admin)."""
        return list(self._targets_for(platform))


# ═══════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════
_shared: TargetGroups | None = None


def get_target_groups(path: str | None = None) -> TargetGroups:
    """Singleton — instance مشترک سراسری."""
    global _shared
    if _shared is None:
        _shared = TargetGroups(path)
    return _shared


def reset_target_groups() -> None:
    """🎯 پاک‌کردن singleton — فقط برای تست."""
    global _shared
    _shared = None
    log_info("🗑️ [TARGET:singleton] reset شد")