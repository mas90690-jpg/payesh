"""
CRUD روی keywords_custom.json — داده‌محور.

نسخهٔ ۳ (این نسخه — داده‌محور):
  - 🎯 همهٔ ثابت‌ها از data/ می‌آید:
      * data/configs/tiers.json     → valid / default / version
      * data/configs/messages.json  → پیام‌های خطا
  - 🎯 `_valid_tiers`, `_default_tier`, `_file_version` حالا attribute هستند.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۲ (atomic write، فرمت-آگنوستیک).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from core.kernel.logger import log_error, log_info, log_success, log_warning

# 🎯 داده‌محور
from data import paths as data_paths
from data.loader import load_json


# ═══════════════════════════════════════════════════════════
# helpers — داده‌محور
# ═══════════════════════════════════════════════════════════
def _get_tiers_data() -> dict:
    """بارگذاری tiers.json."""
    return load_json("tiers")


def _get_error_msg(key: str, default: str = "") -> str:
    """گرفتن پیام خطا از errors.json — منبع یکتا."""
    data = load_json("errors")
    errors = data.get("errors", {})
    return errors.get(key, default)


def _default_path() -> str:
    """مسیر پیش‌فرض — از data.paths."""
    return str(data_paths.KEYWORDS_CUSTOM)


class KeywordAdmin:
    """مدیریت کلیدواژه‌های دستی — هم فرمت قدیمی (list) هم جدید (dict)."""

    def __init__(self, path: str | None = None):
        self.path = path or _default_path()

        # 🎯 داده‌محور — بارگذاری از tiers.json
        tiers = _get_tiers_data()
        self._valid_tiers: tuple[str, ...] = tuple(
            tiers.get("valid", ["T1", "T2", "T3"])
        )
        self._default_tier: str = tiers.get("default", "T1")
        self._file_version: str = tiers.get("version", "2.0.0")

        log_info(
            f"📚 [KW:init] آماده | valid_tiers={self._valid_tiers} | "
            f"default={self._default_tier}"
        )

    # ═══════════════════════════════════════════
    # read
    # ═══════════════════════════════════════════
    def read(self, path: str | None = None) -> dict[str, str]:
        """خواندن فایل — برمی‌گرداند {name: tier}."""
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
                str(x).strip(): self._default_tier
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
                    str(x).strip(): self._default_tier
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

    def _normalize(self, kws: dict[str, Any]) -> dict[str, str]:
        """نرمال‌سازی dict با tier — با self._valid_tiers."""
        out: dict[str, str] = {}
        for k, meta in kws.items():
            name = str(k).strip()
            if not name:
                continue
            if isinstance(meta, dict):
                tier = str(
                    meta.get("tier", self._default_tier)
                ).upper()
            elif isinstance(meta, str):
                tier = meta.upper()
            else:
                tier = self._default_tier
            if tier not in self._valid_tiers:
                tier = self._default_tier
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
        """نوشتن dict با tier — atomic (tmp + replace + fsync)."""
        p = path or self.path

        try:
            data = {
                "version": self._file_version,
                "updated_at": datetime.now().isoformat(),
                "keywords": {
                    k.strip(): {
                        "tier": (
                            t.upper()
                            if t and t.upper() in self._valid_tiers
                            else self._default_tier
                        )
                    }
                    for k, t in kw_tier.items()
                    if k and k.strip()
                },
            }

            parent = os.path.dirname(p)
            if parent:
                os.makedirs(parent, exist_ok=True)

            tmp = p + ".tmp"

            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except (AttributeError, OSError):
                    pass

            os.replace(tmp, p)

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
        tier: str | None = None,
    ) -> str:
        """افزودن کلیدواژه."""
        if not kw or not str(kw).strip():
            return _get_error_msg(
                "add_keyword_hint",
                "کلیدواژه را داخل « » بنویس.",
            )

        kw_clean = str(kw).strip()

        # ─── tier پیش‌فرض ───
        if tier is None:
            tier = self._default_tier
        tier_clean = str(tier).upper()
        if tier_clean not in self._valid_tiers:
            tier_clean = self._default_tier

        current = self.read(path)
        if kw_clean in current:
            existing_tier = current[kw_clean]
            if existing_tier == tier_clean:
                log_info(
                    f"ℹ️ [KW:add] «{kw_clean}» از قبل هست "
                    f"(tier={existing_tier})"
                )
                return f"«{kw_clean}» از قبل هست (سطح {existing_tier})."
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
                f"(تا چند ثانیهٔ دیگر خودکار فعال می‌شود)."
            )
        return "❌ ذخیره ناموفق."

    # ═══════════════════════════════════════════
    # remove
    # ═══════════════════════════════════════════
    def remove(self, kw: str, path: str | None = None) -> str:
        """حذف کلیدواژه."""
        if not kw or not str(kw).strip():
            return _get_error_msg(
                "remove_keyword_hint",
                "کدام کلیدواژه را حذف کنم؟ داخل « » بنویس.",
            )

        kw_clean = str(kw).strip()
        current = self.read(path)

        if kw_clean not in current:
            log_info(f"ℹ️ [KW:remove] «{kw_clean}» نبود")
            return f"«{kw_clean}» در کلیدواژه‌های افزودنی نیست."

        old_tier = current.pop(kw_clean, self._default_tier)

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
    # rename
    # ═══════════════════════════════════════════
    def rename(
        self,
        old: str,
        new: str,
        path: str | None = None,
    ) -> str:
        """تغییر نام کلیدواژه (با حفظ tier)."""
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

        tier = current.pop(old_clean, self._default_tier)
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
    # change_tier
    # ═══════════════════════════════════════════
    def change_tier(
        self,
        kw: str,
        new_tier: str,
        path: str | None = None,
    ) -> str:
        """تغییر tier یک کلیدواژه."""
        kw_clean = str(kw or "").strip()
        tier_clean = str(new_tier or "").upper()

        if not kw_clean:
            return "نام کلیدواژه لازم است."

        if tier_clean not in self._valid_tiers:
            return (
                f"⛔ سطح نامعتبر. باید یکی از این‌ها باشد: "
                f"{', '.join(self._valid_tiers)}"
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
    # list_all / get_tier / exists / count
    # ═══════════════════════════════════════════
    def list_all(
        self,
        path: str | None = None,
        with_tier: bool = False,
    ) -> list[tuple[str, str]] | list[str]:
        """لیست همهٔ کلیدواژه‌ها."""
        data = self.read(path)
        if with_tier:
            return sorted(data.items())
        return sorted(data.keys())

    def get_tier(
        self, kw: str, path: str | None = None
    ) -> str | None:
        """گرفتن tier یک کلیدواژه — یا None اگر نبود."""
        kw_clean = str(kw or "").strip()
        if not kw_clean:
            return None
        return self.read(path).get(kw_clean)

    def exists(self, kw: str, path: str | None = None) -> bool:
        """آیا کلیدواژه وجود دارد؟"""
        kw_clean = str(kw or "").strip()
        if not kw_clean:
            return False
        return kw_clean in self.read(path)

    def count(self, path: str | None = None) -> int:
        """تعداد کلیدواژه‌های سفارشی."""
        return len(self.read(path))
