"""CRUD روی keywords_custom.json — atomic، فرمت-آگنوستیک."""
from __future__ import annotations

import json
import os
from datetime import datetime

from core.kernel.logger import log_error, log_info, log_success


class KeywordAdmin:
    """مدیریت کلیدواژههای دستی — هم فرمت قدیمی (list) هم جدید (dict)."""

    def __init__(self, path: str):
        self.path = path

    def read(self, path: str | None = None) -> dict[str, str]:
        """برمیگرداند {name: tier}."""
        p = path or self.path
        if not os.path.exists(p):
            return {}
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            log_error(
                f"❌ [KW:read] JSON نامعتبر | line={e.lineno} | {e.msg}"
            )
            return {}
        except Exception as e:
            log_error(f"❌ [KW:read] {type(e).__name__}: {e}")
            return {}

        if isinstance(data, list):
            return {
                str(x).strip(): "T1"
                for x in data
                if isinstance(x, str) and x.strip()
            }
        if isinstance(data, dict):
            kws = data.get("keywords")
            if kws is None:
                log_error(
                    f"❌ [KW:read] dict بدون کلید «keywords» | "
                    f"کلیدها: {list(data.keys())[:5]}"
                )
                return {}
            if isinstance(kws, dict):
                return self._normalize(kws)
            if isinstance(kws, list):
                return {
                    str(x).strip(): "T1"
                    for x in kws
                    if isinstance(x, str) and x.strip()
                }
        return {}

    @staticmethod
    def _normalize(kws: dict) -> dict[str, str]:
        out: dict[str, str] = {}
        for k, meta in kws.items():
            name = str(k).strip()
            if not name:
                continue
            if isinstance(meta, dict):
                tier = str(meta.get("tier", "T1")).upper()
            elif isinstance(meta, str):
                tier = meta.upper()
            else:
                tier = "T1"
            if tier not in ("T1", "T2", "T3"):
                tier = "T1"
            out[name] = tier
        return out

    def write(self, kw_tier: dict[str, str],
              path: str | None = None) -> bool:
        """نوشتن dict با tier — atomic (tmp + replace)."""
        p = path or self.path
        try:
            data = {
                "version": "2.0.0",
                "updated_at": datetime.now().isoformat(),
                "keywords": {
                    k.strip(): {
                        "tier": (
                            t.upper()
                            if t and t.upper() in ("T1", "T2", "T3")
                            else "T1"
                        )
                    }
                    for k, t in kw_tier.items()
                    if k and k.strip()
                },
            }
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
            log_info(f"💾 [KW:write] count={len(data['keywords'])}")
            return True
        except Exception as e:
            log_error(f"❌ [KW:write] {type(e).__name__}: {e}")
            return False

    def add(self, kw: str, path: str | None = None) -> str:
        if not kw:
            return (
                "کلیدواژه را داخل « » بنویس، مثلاً: "
                "کلیدواژه «کود مایع» را اضافه کن"
            )
        kw = str(kw).strip()
        current = self.read(path)
        if kw in current:
            log_info(f"ℹ️ [KW:add] «{kw}» از قبل هست")
            return f"«{kw}» از قبل هست."
        current[kw] = "T1"
        if self.write(current, path):
            log_success(f"✅ [KW:add] «{kw}»")
            return (
                f"✅ «{kw}» اضافه شد "
                f"(تا چند ثانیهٔ دیگر خودکار فعال میشود، بدون ریاستارت)."
            )
        return "❌ ذخیره ناموفق."

    def remove(self, kw: str, path: str | None = None) -> str:
        if not kw:
            return "کدام کلیدواژه را حذف کنم؟ داخل « » بنویس."
        kw = str(kw).strip()
        current = self.read(path)
        if kw not in current:
            log_info(f"ℹ️ [KW:remove] «{kw}» نبود")
            return (
                f"«{kw}» در کلیدواژههای افزودنی نیست. "
                f"(فقط کلیدواژههایی که از همینجا اضافه شدهاند "
                f"قابلِ حذفاند، نه محصولاتِ اصلی.)"
            )
        current.pop(kw, None)
        if self.write(current, path):
            log_success(f"✅ [KW:remove] «{kw}»")
            return f"🗑️ «{kw}» حذف شد (چند ثانیهٔ دیگر خودکار اعمال میشود)."
        return "❌ ذخیره ناموفق."
