"""
CRUD روی keywords_custom.json — atomic، فرمت-آگنوستیک.

نسخهٔ ۲ (این نسخه):
  - 🎯 استفاده از config برای مسیر پیش‌فرض (اگر path ندهند).
  - 🎯 استفاده از data.paths برای مسیر keywords_custom.json.
  - 🎯 ثابت `_VALID_TIERS` (به‌جای hardcode در چند جا).
  - 🎯 `_FILE_VERSION` ثابت (به‌جای hardcode "2.0.0").
  - 🐛 FIX: `write()` حالا `os.fsync` می‌زند (durability).
  - 🐛 FIX: `write()` حالا اگر `os.path.dirname(p)` خالی باشد
    (مثلاً path = "keywords.json")، خطا نمی‌دهد.
  - 🎯 `add()` و `remove()` حالا tier را حفظ می‌کنند
    (اگر kw از قبل با tier متفاوت بود).
  - 🎯 `add()` حالا می‌تواند tier بگیرد (پیش‌فرض T1).
  - 🎯 `rename()` متد جدید برای تغییر نام (اختیاری).
  - 🎯 `change_tier()` متد جدید برای تغییر tier.
  - 🎯 `list_all()` متد جدید برای لیست.
  - 🎯 لاگ‌های کامل‌تر با prefixهای [KW:...].
  - ✅ حفظ همهٔ رفتار نسخهٔ ۱ (atomic write، فرمت-آگنوستیک).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from core.kernel.logger import log_error, log_info, log_success, log_warning

# 🎯 P0: مسیر پیش‌فرض از data.paths
from data import paths as data_paths


# ═══════════════════════════════════════════════════════════
# ثابت‌ها
# ═══════════════════════════════════════════════════════════
_VALID_TIERS: tuple[str, ...] = ("T1", "T2", "T3")
_DEFAULT_TIER: str = "T1"
_FILE_VERSION: str = "2.0.0"


def _default_path() -> str:
    """مسیر پیش‌فرض — از data.paths."""
    return str(data_paths.KEYWORDS_CUSTOM)


class KeywordAdmin:
    """مدیریت کلیدواژه‌های دستی — هم فرمت قدیمی (list) هم جدید (dict)."""

    def __init__(self, path: str | None = None):
        # 🎯 FIX: اگر path ندهند، از data.paths
        self.path = path or _default_path()

    # ═══════════════════════════════════════════
    # read
    # ═══════════════════════════════════════════
    def read(self, path: str | None = None) -> dict[str, str]:
        """خواندن فایل — برمی‌گرداند {name: tier}.

        فرمت‌های پشتیبانی‌شده:
          - لیست خالص: ["کود", "سم"]
          - dict با keywords: {"keywords": {"کود": {"tier": "T1"}}}
          - dict با keywords لیست: {"keywords": ["کود", "سم"]}

        Args:
            path: مسیر دلخواه (اختیاری). اگر ندهند از self.path.

        Returns:
            dict[str, str]: {نام: tier}. اگر فایل نبود یا خراب بود، {}.
        """
        p = path or self.path
        if not os.path.exists(p):
            return {}

        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            log_error(
                f"❌ [KW:read] JSON نامعتبر | "
                f"line={e.lineno} col={e.colno} | {e.msg} | "
                f"path={p}"
            )
            return {}
        except Exception as e:
            log_error(
                f"❌ [KW:read] {type(e).__name__}: {e} | path={p}"
            )
            return {}

        # ─── فرمت لیست ───
        if isinstance(data, list):
            out = {
                str(x).strip(): _DEFAULT_TIER
                for x in data
                if isinstance(x, str) and x.strip()
            }
            log_info(
                f"📚 [KW:read] فرمت=list | count={len(out)} | "
                f"path={os.path.basename(p)}"
            )
            return out

        # ─── فرمت dict ───
        if isinstance(data, dict):
            kws = data.get("keywords")
            if kws is None:
                log_error(
                    f"❌ [KW:read] dict بدون کلید «keywords» | "
                    f"کلیدها: {list(data.keys())[:5]} | path={p}"
                )
                return {}
            if isinstance(kws, dict):
                out = self._normalize(kws)
                log_info(
                    f"📚 [KW:read] فرمت=dict_meta | count={len(out)} | "
                    f"path={os.path.basename(p)}"
                )
                return out
            if isinstance(kws, list):
                out = {
                    str(x).strip(): _DEFAULT_TIER
                    for x in kws
                    if isinstance(x, str) and x.strip()
                }
                log_info(
                    f"📚 [KW:read] فرمت=dict_list | count={len(out)} | "
                    f"path={os.path.basename(p)}"
                )
                return out

        log_warning(
            f"⚠️ [KW:read] فرمت ناشناخته | "
            f"type={type(data).__name__} | path={p}"
        )
        return {}

    @staticmethod
    def _normalize(kws: dict[str, Any]) -> dict[str, str]:
        """نرمال‌سازی dict با tier."""
        out: dict[str, str] = {}
        for k, meta in kws.items():
            name = str(k).strip()
            if not name:
                continue
            if isinstance(meta, dict):
                tier = str(meta.get("tier", _DEFAULT_TIER)).upper()
            elif isinstance(meta, str):
                tier = meta.upper()
            else:
                tier = _DEFAULT_TIER
            if tier not in _VALID_TIERS:
                tier = _DEFAULT_TIER
            out[name] = tier
        return out

    # ═══════════════════════════════════════════
    # write
    # ═══════════════════════════════════════════
    def write(
        self,
        kw_tier: dict[str, str],
        path: str | None = None,
    ) -> bool:
        """نوشتن dict با tier — atomic (tmp + replace + fsync).

        Args:
            kw_tier: {نام: tier}.
            path: مسیر دلخواه (اختیاری).

        Returns:
            True اگر موفق.
        """
        p = path or self.path

        try:
            data = {
                "version": _FILE_VERSION,
                "updated_at": datetime.now().isoformat(),
                "keywords": {
                    k.strip(): {
                        "tier": (
                            t.upper()
                            if t and t.upper() in _VALID_TIERS
                            else _DEFAULT_TIER
                        )
                    }
                    for k, t in kw_tier.items()
                    if k and k.strip()
                },
            }

            # 🎯 FIX: dirname ممکن است خالی باشد
            parent = os.path.dirname(p)
            if parent:
                os.makedirs(parent, exist_ok=True)

            tmp = p + ".tmp"

            # 🎯 FIX: fsync برای durability
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except (AttributeError, OSError):
                    pass

            os.replace(tmp, p)

            # 🎯 fsync دایرکتوری (POSIX)
            if parent:
                try:
                    dir_fd = os.open(parent, os.O_DIRECTORY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except (AttributeError, OSError):
                    pass

            log_info(
                f"💾 [KW:write] count={len(data['keywords'])} | "
                f"path={os.path.basename(p)}"
            )
            return True

        except Exception as e:
            log_error(
                f"❌ [KW:write] {type(e).__name__}: {e} | path={p}"
            )
            # cleanup tmp
            try:
                tmp = p + ".tmp"
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            return False

    # ═══════════════════════════════════════════
    # add
    # ═══════════════════════════════════════════
    def add(
        self,
        kw: str,
        path: str | None = None,
        tier: str = _DEFAULT_TIER,
    ) -> str:
        """افزودن کلیدواژه.

        Args:
            kw: نام کلیدواژه.
            path: مسیر فایل (اختیاری).
            tier: سطح (T1/T2/T3). پیش‌فرض T1.

        Returns:
            پیام نتیجه (فارسی).
        """
        if not kw or not str(kw).strip():
            return (
                "کلیدواژه را داخل « » بنویس، مثلاً: "
                "کلیدواژه «کود مایع» را اضافه کن"
            )

        kw_clean = str(kw).strip()

        # اعتبارسنجی tier
        tier_clean = (tier or _DEFAULT_TIER).upper()
        if tier_clean not in _VALID_TIERS:
            tier_clean = _DEFAULT_TIER

        current = self.read(path)
        if kw_clean in current:
            existing_tier = current[kw_clean]
            if existing_tier == tier_clean:
                log_info(
                    f"ℹ️ [KW:add] «{kw_clean}» از قبل هست "
                    f"(tier={existing_tier})"
                )
                return f"«{kw_clean}» از قبل هست (سطح {existing_tier})."
            # tier متفاوت → به‌روزرسانی
            log_info(
                f"ℹ️ [KW:add] «{kw_clean}» از قبل هست | "
                f"tier: {existing_tier} → {tier_clean}"
            )
            current[kw_clean] = tier_clean
            if self.write(current, path):
                return (
                    f"✅ «{kw_clean}» از قبل بود — سطحش از "
                    f"{existing_tier} به {tier_clean} تغییر کرد."
                )
            return "❌ ذخیره ناموفق."

        current[kw_clean] = tier_clean

        if self.write(current, path):
            log_success(
                f"✅ [KW:add] «{kw_clean}» | tier={tier_clean} | "
                f"total={len(current)}"
            )
            return (
                f"✅ «{kw_clean}» با سطح {tier_clean} اضافه شد "
                f"(تا چند ثانیهٔ دیگر خودکار فعال می‌شود، "
                f"بدون ری‌استارت)."
            )
        return "❌ ذخیره ناموفق."

    # ═══════════════════════════════════════════
    # remove
    # ═══════════════════════════════════════════
    def remove(self, kw: str, path: str | None = None) -> str:
        """حذف کلیدواژه.

        Args:
            kw: نام کلیدواژه.
            path: مسیر فایل (اختیاری).

        Returns:
            پیام نتیجه (فارسی).
        """
        if not kw or not str(kw).strip():
            return "کدام کلیدواژه را حذف کنم؟ داخل « » بنویس."

        kw_clean = str(kw).strip()
        current = self.read(path)

        if kw_clean not in current:
            log_info(f"ℹ️ [KW:remove] «{kw_clean}» نبود")
            return (
                f"«{kw_clean}» در کلیدواژه‌های افزودنی نیست. "
                f"(فقط کلیدواژه‌هایی که از همین‌جا اضافه شده‌اند "
                f"قابلِ حذف‌اند، نه محصولاتِ اصلی.)"
            )

        old_tier = current.pop(kw_clean, _DEFAULT_TIER)

        if self.write(current, path):
            log_success(
                f"🗑️ [KW:remove] «{kw_clean}» حذف شد | "
                f"tier_was={old_tier} | remaining={len(current)}"
            )
            return (
                f"🗑️ «{kw_clean}» حذف شد "
                f"(چند ثانیهٔ دیگر خودکار اعمال می‌شود)."
            )
        return "❌ ذخیره ناموفق."

    # ═══════════════════════════════════════════
    # 🎯 NEW: rename
    # ═══════════════════════════════════════════
    def rename(
        self,
        old: str,
        new: str,
        path: str | None = None,
    ) -> str:
        """تغییر نام کلیدواژه (با حفظ tier).

        Args:
            old: نام فعلی.
            new: نام جدید.
            path: مسیر فایل (اختیاری).

        Returns:
            پیام نتیجه (فارسی).
        """
        old_clean = str(old or "").strip()
        new_clean = str(new or "").strip()

        if not old_clean or not new_clean:
            return "نام قدیمی و جدید لازم است."

        if old_clean == new_clean:
            return "⚠️ نام قدیم و جدید یکیه."

        current = self.read(path)

        if old_clean not in current:
            return f"❌ «{old_clean}» در کلیدواژه‌های سفارشی نیست."

        if new_clean in current:
            return f"⚠️ «{new_clean}» از قبل در لیست هست."

        tier = current.pop(old_clean, _DEFAULT_TIER)
        current[new_clean] = tier

        if self.write(current, path):
            log_success(
                f"✏️ [KW:rename] «{old_clean}» → «{new_clean}» | "
                f"tier={tier}"
            )
            return (
                f"✏️ «{old_clean}» → «{new_clean}»\n"
                f"⏱️ تا چند ثانیهٔ دیگر خودکار اعمال می‌شود."
            )
        return "❌ ذخیره ناموفق."

    # ═══════════════════════════════════════════
    # 🎯 NEW: change_tier
    # ═══════════════════════════════════════════
    def change_tier(
        self,
        kw: str,
        new_tier: str,
        path: str | None = None,
    ) -> str:
        """تغییر tier یک کلیدواژه.

        Args:
            kw: نام کلیدواژه.
            new_tier: سطح جدید (T1/T2/T3).
            path: مسیر فایل (اختیاری).

        Returns:
            پیام نتیجه (فارسی).
        """
        kw_clean = str(kw or "").strip()
        tier_clean = str(new_tier or "").upper()

        if not kw_clean:
            return "نام کلیدواژه لازم است."

        if tier_clean not in _VALID_TIERS:
            return (
                f"⛔ سطح نامعتبر. باید یکی از این‌ها باشد: "
                f"{', '.join(_VALID_TIERS)}"
            )

        current = self.read(path)

        if kw_clean not in current:
            return f"❌ «{kw_clean}» در کلیدواژه‌های سفارشی نیست."

        old_tier = current[kw_clean]
        if old_tier == tier_clean:
            return f"⚠️ «{kw_clean}» از قبل سطح {tier_clean} هست."

        current[kw_clean] = tier_clean

        if self.write(current, path):
            log_success(
                f"✅ [KW:change_tier] «{kw_clean}» | "
                f"{old_tier} → {tier_clean}"
            )
            return (
                f"✅ «{kw_clean}» از {old_tier} به {tier_clean} "
                f"تغییر کرد."
            )
        return "❌ ذخیره ناموفق."

    # ═══════════════════════════════════════════
    # 🎯 NEW: list_all
    # ═══════════════════════════════════════════
    def list_all(
        self,
        path: str | None = None,
        with_tier: bool = False,
    ) -> list[tuple[str, str]] | list[str]:
        """لیست همهٔ کلیدواژه‌ها.

        Args:
            path: مسیر فایل (اختیاری).
            with_tier: اگر True، لیست (name, tier) برگردان.

        Returns:
            لیست نام‌ها (یا لیست (name, tier)).
        """
        data = self.read(path)
        if with_tier:
            return sorted(data.items())
        return sorted(data.keys())

    # ═══════════════════════════════════════════
    # 🎯 NEW: get_tier
    # ═══════════════════════════════════════════
    def get_tier(
        self, kw: str, path: str | None = None
    ) -> str | None:
        """گرفتن tier یک کلیدواژه — یا None اگر نبود."""
        kw_clean = str(kw or "").strip()
        if not kw_clean:
            return None
        return self.read(path).get(kw_clean)

    # ═══════════════════════════════════════════
    # 🎯 NEW: exists
    # ═══════════════════════════════════════════
    def exists(self, kw: str, path: str | None = None) -> bool:
        """آیا کلیدواژه وجود دارد؟"""
        kw_clean = str(kw or "").strip()
        if not kw_clean:
            return False
        return kw_clean in self.read(path)

    # ═══════════════════════════════════════════
    # 🎯 NEW: count
    # ═══════════════════════════════════════════
    def count(self, path: str | None = None) -> int:
        """تعداد کلیدواژه‌های سفارشی."""
        return len(self.read(path))