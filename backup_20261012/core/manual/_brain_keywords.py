# core/manual/_brain_keywords.py
"""
مدیریت کلیدواژه‌های سفارشی + تحلیل خطاها.

این ماژول:
  - load/save custom keywords (با tier)
  - add/remove/edit/change_tier keyword
  - handle_keyword_command (parse دستورات فارسی)
  - handle_kw_list
  - validate_kw
  - read/explain errors از لاگ
  - handle_error_command

نسخهٔ ۱۰ (این نسخه):
  - 🎯 FIX: `_handle_keyword_command` از CC=35 به CC≈6 کاهش یافت.
    با dispatcher و ۵ handler مستقل:
      _kw_branch_list, _kw_branch_change_tier,
      _kw_branch_edit, _kw_branch_remove, _kw_branch_add,
      _kw_branch_exists
  - ✅ حفظ همهٔ رفتار نسخهٔ ۹.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from core.kernel.logger import log_info, log_success, log_warning

from core.manual._brain_core import (
    CUSTOM_KW_FILE,
    LOG_FILE,
    LOG_TAIL_BYTES,
    MAX_KW_LEN,
    MIN_KW_LEN,
    VALID_TIERS,
    tail_lines,
)


# ═══════════════════════════════════════════════════════════
# BrainKeywordsMixin
# ═══════════════════════════════════════════════════════════
class BrainKeywordsMixin:
    """Mixin مدیریت کلیدواژه + تحلیل خطا."""

    # ═══════════════════════════════════════════
    # Load/Save custom keywords
    # ═══════════════════════════════════════════
    def _load_custom_kw(self) -> list[str]:
        """خواندن کلیدواژه‌های سفارشی — فقط اسم‌ها."""
        return list(self._load_custom_kw_with_tier().keys())

    def _load_custom_kw_with_tier(self) -> dict[str, str]:
        """خواندن کلیدواژه‌های سفارشی به صورت {name: tier}."""
        if not CUSTOM_KW_FILE.exists():
            return {}
        try:
            with open(str(CUSTOM_KW_FILE), encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            log_warning(
                f"⚠️ [MBRAIN:kw:load] JSON نامعتبر | "
                f"line={e.lineno} | {e.msg}"
            )
            return {}
        except Exception as e:
            log_warning(
                f"⚠️ [MBRAIN:kw:load] {type(e).__name__}: {e}"
            )
            return {}

        if isinstance(data, list):
            return {
                str(k).strip(): "T1"
                for k in data
                if str(k).strip()
            }

        if isinstance(data, dict):
            kws = data.get("keywords")
            if kws is None:
                log_warning(
                    f"⚠️ [MBRAIN:kw:load] dict بدون «keywords» | "
                    f"کلیدها: {list(data.keys())[:5]}"
                )
                return {}
            if isinstance(kws, dict):
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
                    if tier not in VALID_TIERS:
                        tier = "T1"
                    out[name] = tier
                return out
        return {}

    def _save_custom_kw(
        self, keywords_with_tier: dict[str, str] | None
    ) -> bool:
        """ذخیره کلیدواژه‌ها با tier — atomic."""
        if not keywords_with_tier:
            log_warning(
                "⚠️ [MBRAIN:kw:save] ورودی خالی/None — رد شد"
            )
            return False

        clean: dict[str, dict[str, str]] = {}
        for k, t in keywords_with_tier.items():
            name = (k or "").strip()
            if not name:
                continue
            tier = str(t or "T1").upper()
            if tier not in VALID_TIERS:
                log_warning(
                    f"⚠️ [MBRAIN:kw:save] tier نامعتبر «{t}» برای "
                    f"«{name}» → T1"
                )
                tier = "T1"
            clean[name] = {"tier": tier}

        data = {
            "version": "2.0.0",
            "updated_at": datetime.now().isoformat(),
            "keywords": clean,
        }
        ok = self._atomic_write_json(CUSTOM_KW_FILE, data)
        if ok:
            log_success(
                f"💾 [MBRAIN:kw:save] {len(clean)} کلیدواژه ذخیره شد"
            )
        return ok

    # ═══════════════════════════════════════════
    # CRUD
    # ═══════════════════════════════════════════
    def _add_keyword(self, kw: str, tier: str = "T1") -> str:
        ok, msg = self._validate_kw(kw)
        if not ok:
            log_warning(f"⚠️ [MBRAIN:kw:add] نامعتبر | {msg}")
            return f"⛔ کلیدواژه نامعتبر: {msg}"

        tier = (tier or "T1").upper()
        if tier not in VALID_TIERS:
            tier = "T1"

        existing = self._load_custom_kw_with_tier()
        if msg in existing:
            log_info(f"ℹ️ [MBRAIN:kw:add] «{msg}» از قبل هست")
            return f"⚠️ «{msg}» از قبل هست."

        try:
            from analyzers.keyword_finder import get_keyword_finder
            if msg in set(get_keyword_finder().keywords):
                log_info(
                    f"ℹ️ [MBRAIN:kw:add] «{msg}» در محصولات اصلی هست"
                )
                return f"⚠️ «{msg}» در محصولات اصلی هست."
        except Exception as e:
            log_warning(
                f"⚠️ [MBRAIN:kw:add] keyword_finder | "
                f"{type(e).__name__}: {e}"
            )

        existing[msg] = tier
        if self._save_custom_kw(existing):
            log_success(
                f"✅ [MBRAIN:kw:add] «{msg}» | tier={tier} | "
                f"total={len(existing)}"
            )
            return (
                f"✅ «{msg}» با سطح {tier} اضافه شد.\n"
                f"🔢 الان {len(existing)} کلیدواژهٔ سفارشی داری.\n"
                f"⏱️ تا ۵ ثانیهٔ دیگه خودکار فعال میشه."
            )
        return "❌ ذخیره ناموفق."

    def _remove_keyword(self, kw: str) -> str:
        kw = (kw or "").strip()
        if not kw:
            return "⛔ اسم کلیدواژه خالیه."
        existing = self._load_custom_kw_with_tier()
        if kw not in existing:
            log_info(f"ℹ️ [MBRAIN:kw:rm] «{kw}» نبود")
            return (
                f"❌ «{kw}» در کلیدواژه‌های سفارشی نیست.\n"
                f"(فقط کلیدواژه‌های سفارشی قابل حذفن.)"
            )
        existing.pop(kw, None)
        if self._save_custom_kw(existing):
            log_success(
                f"🗑️ [MBRAIN:kw:rm] «{kw}» حذف شد | "
                f"remaining={len(existing)}"
            )
            return (
                f"🗑️ «{kw}» حذف شد.\n"
                f"🔢 الان {len(existing)} کلیدواژهٔ سفارشی داری."
            )
        return "❌ ذخیره ناموفق."

    def _change_tier(self, kw: str, new_tier: str) -> str:
        kw = (kw or "").strip()
        if not kw:
            return "⛔ اسم کلیدواژه خالیه."
        new_tier = new_tier.upper()
        if new_tier not in VALID_TIERS:
            return "⛔ سطح نامعتبر. باید T1 یا T2 یا T3 باشه."
        existing = self._load_custom_kw_with_tier()
        if kw not in existing:
            return (
                f"❌ «{kw}» در کلیدواژه‌های سفارشی نیست.\n"
                f"(فقط کلیدواژه‌های سفارشی قابل تغییر سطحن.)"
            )
        old_tier = existing.get(kw, "T1")
        if old_tier == new_tier:
            return f"⚠️ «{kw}» از قبل سطح {new_tier} هست."
        existing[kw] = new_tier
        if self._save_custom_kw(existing):
            log_success(
                f"✅ [MBRAIN:kw:tier] «{kw}» | "
                f"{old_tier} → {new_tier}"
            )
            return (
                f"✅ «{kw}» از {old_tier} به {new_tier} تغییر کرد.\n"
                f"⏱️ تا ۵ ثانیهٔ دیگه خودکار فعال میشه."
            )
        return "❌ ذخیره ناموفق."

    def _edit_keyword(self, old: str, new: str) -> str:
        old = (old or "").strip()
        new = (new or "").strip()
        ok, msg = self._validate_kw(new)
        if not ok:
            return f"⛔ کلیدواژهٔ جدید نامعتبر: {msg}"
        if old == new:
            return "⚠️ اسم قدیم و جدید یکیه."
        existing = self._load_custom_kw_with_tier()
        if old not in existing:
            return f"❌ «{old}» در کلیدواژه‌های سفارشی نیست."
        if new in existing:
            return f"⚠️ «{new}» از قبل در لیست هست."
        tier = existing.pop(old, "T1")
        existing[new] = tier
        if self._save_custom_kw(existing):
            log_success(f"✏️ [MBRAIN:kw:edit] «{old}» → «{new}»")
            return (
                f"✏️ «{old}» → «{new}»\n"
                f"⏱️ تا ۵ ثانیهٔ دیگه خودکار فعال میشه."
            )
        return "❌ ذخیره ناموفق."

    @staticmethod
    def _validate_kw(kw: str) -> tuple[bool, str]:
        if not kw or not isinstance(kw, str):
            return False, "خالی"
        k = kw.strip()
        if len(k) < MIN_KW_LEN:
            return False, f"حداقل {MIN_KW_LEN} کاراکتر"
        if len(k) > MAX_KW_LEN:
            return False, f"حداکثر {MAX_KW_LEN} کاراکتر"
        if any(c in k for c in ("\n", "\r", "\t")):
            return False, "بدون newline/tab"
        return True, k

    # ═══════════════════════════════════════════
    # handle_keyword_command — dispatcher
    # ═══════════════════════════════════════════
    def _handle_keyword_command(self, text: str) -> str | None:
        """🎯 FIX: dispatcher — CC≈6 (قبلاً ۳۵).

        ترتیب branchها:
          ۱. has keyword word?
          ۲. list
          ۳. change_tier
          ۴. edit
          ۵. remove
          ۶. add
          ۷. exists
        """
        low = self._normalize(text)

        if not self._has_kw_word(low):
            return None

        quoted = self._extract_quoted(text)

        # ─── ۱) لیست ───
        if self._kw_is_list(low):
            return self._handle_kw_list(text, low, quoted)

        # ─── ۲) تغییر سطح ───
        if self._kw_is_change_tier(low):
            return self._kw_branch_change_tier(text, low, quoted)

        # ─── ۳) ویرایش ───
        if self._kw_is_edit(low):
            return self._kw_branch_edit(text, quoted)

        # ─── ۴) حذف ───
        if self._kw_is_remove(low):
            return self._kw_branch_remove(text, quoted)

        # ─── ۵) افزودن ───
        if self._kw_is_add(low):
            return self._kw_branch_add(text, quoted)

        # ─── ۶) سؤال وجود ───
        if self._kw_is_exists(low):
            return self._kw_branch_exists(text, quoted)

        return None

    # ═══════════════════════════════════════════
    # keyword helpers — تشخیص branch
    # ═══════════════════════════════════════════
    @staticmethod
    def _has_kw_word(low: str) -> bool:
        return (
            "کلیدواژه" in low
            or "کلید واژه" in low
            or "کلیدواژه‌ها" in low
            or "کلید واژه ها" in low
            or "keyword" in low
        )

    @staticmethod
    def _kw_is_list(low: str) -> bool:
        return any(w in low for w in [
            "لیست", "list", "نمایش", "چند",
            "چیا", "چه‌ها", "چیه", "چی‌ها",
        ])

    @staticmethod
    def _kw_is_change_tier(low: str) -> bool:
        return (
            any(w in low for w in [
                "تی", "سطح", "وزن", "tier", "level",
                "t1", "t2", "t3",
            ])
            and any(w in low for w in [
                "کن", "بذار", "عوض", "تغییر"
            ])
        )

    @staticmethod
    def _kw_is_edit(low: str) -> bool:
        return any(w in low for w in [
            "تغییر", "عوض", "اصلاح", "ویرایش",
            "rename", "edit",
        ])

    @staticmethod
    def _kw_is_remove(low: str) -> bool:
        return any(w in low for w in [
            "حذف", "پاک", "بردار", "remove", "delete",
        ])

    @staticmethod
    def _kw_is_add(low: str) -> bool:
        return any(w in low for w in [
            "اضافه", "اضاف", "بذار", "بیفزا",
            "ثبت", "add", "insert",
        ])

    @staticmethod
    def _kw_is_exists(low: str) -> bool:
        return any(w in low for w in [
            "دار", "هست", "داری", "exist", "has"
        ])

    # ═══════════════════════════════════════════
    # keyword branches — هر کدام تابع مستقل
    # ═══════════════════════════════════════════
    def _kw_branch_change_tier(
        self, text: str, low: str, quoted: list[str]
    ) -> str:
        """تغییر سطح کلیدواژه — با parse فارسی/انگلیسی."""
        _low_n = low.translate(
            str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
        )
        _low_n = (
            _low_n
            .replace("یک", "1")
            .replace("دو", "2")
            .replace("سه", "3")
            .replace("one", "1")
            .replace("two", "2")
            .replace("three", "3")
        )

        target_tier: str | None = None
        for pat, tier in (
            (
                r"(^|\s)(1|t1|تی\s*1|سطح\s*1|وزن\s*1|"
                r"level\s*1|tier\s*1)(\s|$)",
                "T1",
            ),
            (
                r"(^|\s)(2|t2|تی\s*2|سطح\s*2|وزن\s*2|"
                r"level\s*2|tier\s*2)(\s|$)",
                "T2",
            ),
            (
                r"(^|\s)(3|t3|تی\s*3|سطح\s*3|وزن\s*3|"
                r"level\s*3|tier\s*3)(\s|$)",
                "T3",
            ),
        ):
            if re.search(pat, _low_n):
                target_tier = tier
                break

        kw_to_change = quoted[0] if quoted else None

        if not kw_to_change and target_tier:
            m = re.search(
                r"(?:کلید\s*واژه|keyword)\s+(.+?)\s+"
                r"(?:را\s+)?(?:تی|سطح|وزن|tier|level)\s*"
                r"(?:1|2|3|۱|۲|۳|یک|دو|سه|one|two|three)",
                text,
                flags=re.IGNORECASE,
            )
            if m:
                kw_to_change = m.group(1).strip()

        if kw_to_change and target_tier:
            return self._change_tier(
                kw_to_change, target_tier
            )

        return (
            "📝 قالب تغییر سطح:\n"
            "• با گیومه: «کلیدواژه «کود» را تی 1 کن»\n"
            "• بدون گیومه: «کلیدواژه کود را تی 2 کن»\n"
            "• یا: «کلیدواژه نهاد کشاورزی را سطح 1 کن»"
        )

    def _kw_branch_edit(
        self, text: str, quoted: list[str]
    ) -> str:
        """ویرایش کلیدواژه — با گیومه یا regex."""
        if len(quoted) >= 2:
            return self._edit_keyword(quoted[0], quoted[1])

        m = re.search(
            r"(?:کلید\s*واژه|keyword)\s+(.+?)\s+"
            r"(?:را\s+)?(?:به|to)\s+(.+?)"
            r"(?:\s+(?:تغییر|عوض|اصلاح|ویرایش|کن|بده|بکن))?\s*$",
            text,
            flags=re.IGNORECASE,
        )
        if m:
            old = m.group(1).strip()
            new = m.group(2).strip()
            return self._edit_keyword(old, new)

        return (
            "📝 قالب ویرایش:\n"
            "• با گیومه: «کلیدواژه «قدیمی» را به «جدید» "
            "تغییر بده»\n"
            "• بدون گیومه: «کلیدواژه قدیمی را به جدید "
            "تغییر بده»"
        )

    def _kw_branch_remove(
        self, text: str, quoted: list[str]
    ) -> str:
        """حذف کلیدواژه."""
        if quoted:
            return self._remove_keyword(quoted[0])

        val = self._extract_value_fallback(
            text,
            ["حذف", "پاک", "بردار", "remove", "delete"],
        )
        if val:
            return self._remove_keyword(val)

        return (
            "📝 قالب حذف:\n"
            "• با گیومه: «کلیدواژه «مورد نظر» را حذف کن»\n"
            "• بدون گیومه: «کلیدواژه نهاد کشاورزی را پاک کن»"
        )

    def _kw_branch_add(
        self, text: str, quoted: list[str]
    ) -> str:
        """افزودن کلیدواژه."""
        if quoted:
            tier = self._extract_tier(text)
            return self._add_keyword(quoted[0], tier=tier)

        val = self._extract_value_fallback(
            text,
            ["اضافه", "اضاف", "بیفزا", "ثبت", "add", "insert"],
        )
        if val:
            tier = self._extract_tier(text)
            return self._add_keyword(val, tier=tier)

        return (
            "📝 قالب افزودن:\n"
            "• پیش‌فرض (T1): «کلیدواژه «کود مایع» را "
            "اضافه کن»\n"
            "• با سطح: «کلیدواژه «کود مایع» را سطح 2 "
            "اضافه کن»\n"
            "• بدون گیومه: «کلیدواژه کود مایع را اضافه کن»"
        )

    def _kw_branch_exists(
        self, text: str, quoted: list[str]
    ) -> str | None:
        """سؤال «داری؟»."""
        if quoted:
            return self._check_keyword_exists(quoted[0])

        val = self._extract_value_fallback(
            text, ["دار", "هست", "داری", "exist", "has"]
        )
        if val:
            return self._check_keyword_exists(val)
        return None

    def _check_keyword_exists(self, kw: str) -> str:
        """چک وجود یک کلیدواژه در سفارشی یا محصولات."""
        kws = self._load_custom_kw()
        if kw in kws:
            return f"✅ «{kw}» در کلیدواژه‌های سفارشی هست."
        try:
            from analyzers.keyword_finder import get_keyword_finder
            if kw in get_keyword_finder().keywords:
                return (
                    f"✅ «{kw}» در محصولات اصلی هست "
                    f"(نه سفارشی)."
                )
        except Exception as e:
            log_warning(
                f"⚠️ [MBRAIN:kw:check] keyword_finder | "
                f"{type(e).__name__}: {e}"
            )
        return f"❌ «{kw}» در هیچ لیستی نیست."

    # ═══════════════════════════════════════════
    # لیست کلیدواژه‌ها
    # ═══════════════════════════════════════════
    def _handle_kw_list(
        self, text: str, low: str, quoted: list[str]
    ) -> str:
        """منطق مشترک لیست کلیدواژه‌ها — اصلی/سفارشی/همه."""
        kws = self._load_custom_kw()

        if "سفارشی" in low or "دستی" in low or "custom" in low:
            if not kws:
                return "📭 هیچ کلیدواژهٔ سفارشی‌ای ثبت نشده."
            lines = [f"🔧 {len(kws)} کلیدواژهٔ سفارشی:"]
            for i, k in enumerate(kws[:60], 1):
                lines.append(f"   {i}. {k}")
            if len(kws) > 60:
                lines.append(
                    f"   ... و {len(kws) - 60} مورد دیگر"
                )
            return "\n".join(lines)

        only_main = (
            "اصلی" in low or "main" in low or "محصول" in low
        )

        try:
            from analyzers.keyword_finder import get_keyword_finder
            all_kws = list(get_keyword_finder().keywords)
        except Exception as e:
            return f"❌ خطا در خوندن کلیدواژه‌ها: {e}"

        if only_main:
            custom_set = set(kws)
            source = [k for k in all_kws if k not in custom_set]
            title_prefix = "کلیدواژهٔ اصلی (از محصولات)"
        else:
            source = all_kws
            title_prefix = "کلیدواژه"

        total = len(source)
        if not total:
            return f"📭 هیچ {title_prefix}ای پیدا نشد."

        n = self._extract_number(text)
        if re.search(r"(^|\s)(همه|تمام|کل)(\s|$)", low):
            n = 0

        if n is None:
            n = 100
        if n <= 0 or n >= total:
            show = source
            header = f"🔑 همهٔ {total} {title_prefix}:"
            shown = total
        else:
            show = source[:n]
            header = (
                f"🔑 {total} {title_prefix} — "
                f"نمایش {len(show)} مورد اول:"
            )
            shown = len(show)

        lines = [header]
        for i, k in enumerate(show, 1):
            lines.append(f"   {i}. {k}")
        if shown < total:
            lines.append(
                f"\n💡 برای دیدن بقیه: «لیست {total} کلیدواژه» "
                f"یا «لیست ۲۰۰ کلیدواژه»"
            )
        if kws:
            lines.append(
                f"\n🔧 {len(kws)} کلیدواژهٔ سفارشی هم داری."
            )
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # خطاها
    # ═══════════════════════════════════════════
    def _read_recent_logs(self, limit: int = 500) -> list[dict]:
        """آخرین خطوط لاگ — با seek از انتها."""
        lines = tail_lines(LOG_FILE, LOG_TAIL_BYTES)
        if not lines:
            return []
        out: list[dict] = []
        invalid = 0
        for ln in reversed(lines):
            if len(out) >= limit:
                break
            try:
                out.append(json.loads(ln))
            except Exception:
                invalid += 1
                continue
        if invalid:
            log_info(
                f"ℹ️ [MBRAIN:logs] {invalid} خط نامعتبر رد شد"
            )
        out.reverse()
        return out

    def _get_recent_errors(
        self, hours: int = 24, limit: int = 20
    ) -> list[dict]:
        """🎯 FIX: timeout-aware date comparison."""
        cutoff = datetime.now() - timedelta(hours=hours)
        logs = self._read_recent_logs(1000)
        errors: list[dict] = []

        for entry in reversed(logs):
            if entry.get("level") not in ("ERROR", "CRITICAL"):
                continue
            try:
                ts = entry.get("ts", "")
                # 🎯 FIX: handle both naive and aware
                when = datetime.fromisoformat(
                    ts.replace("Z", "+00:00")
                )
                if when.tzinfo is not None:
                    when = when.replace(tzinfo=None)
                if when < cutoff:
                    continue
            except Exception:
                pass  # ts نامعتبر → قبول کن

            errors.append(entry)
            if len(errors) >= limit:
                break
        return errors

    def _handle_error_command(self, text: str) -> str | None:
        low = self._normalize(text)
        if not any(w in low for w in [
            "خطا", "error", "خرابی", "مشکل",
            "ایراد", "ارور",
        ]):
            return None

        if any(w in low for w in [
            "توضیح", "بگو", "چیه", "یعنی", "چرا",
            "explain", "فهم", "بخوان", "بخون",
        ]):
            errors = self._get_recent_errors(hours=24, limit=15)
            if not errors:
                return "✅ در ۲۴ ساعت اخیر هیچ خطایی در لاگ نبود."
            return self._explain_errors(errors)

        if any(w in low for w in [
            "چند", "تعداد", "count", "شمردن"
        ]):
            errors = self._get_recent_errors(hours=24, limit=1000)
            if not errors:
                return "✅ در ۲۴ ساعت اخیر خطایی ثبت نشده."
            by_level: dict[str, int] = {}
            for e in errors:
                lv = e.get("level", "?")
                by_level[lv] = by_level.get(lv, 0) + 1
            lines = [f"⚠️ {len(errors)} خطا در ۲۴ ساعت اخیر:"]
            for lv, n in sorted(
                by_level.items(), key=lambda x: -x[1]
            ):
                lines.append(f"   • {lv}: {n}")
            return "\n".join(lines)

        if any(w in low for w in [
            "لاگ", "log", "لیست", "نمایش",
            "اخیر", "آخرین",
        ]):
            errors = self._get_recent_errors(hours=24, limit=10)
            if not errors:
                return "✅ در ۲۴ ساعت اخیر هیچ خطایی نبود."
            lines = [f"🗒️ آخرین {len(errors)} خطا:"]
            for e in errors:
                ts = str(e.get("ts", ""))[:19].replace("T", " ")
                msg = str(e.get("msg", ""))[:100]
                lines.append(
                    f"   [{ts}] {e.get('level','?')}: {msg}"
                )
            return "\n".join(lines)

        return None

    def _explain_errors(self, errors: list[dict]) -> str:
        categories = {
            "شبکه/اتصال": [
                "ConnectionError", "timed out", "TimeoutError",
                "Connection refused", "502", "Bad Gateway",
                "failed to connect",
            ],
            "محدودیت AI": [
                "402", "Insufficient Balance", "rate limit",
                "429", "موجودی",
            ],
            "نشست/ورود": [
                "session", "login", "expired", "unauthorized",
                "401", "403",
            ],
            "دیتابیس": [
                "database", "sqlite", "no such table",
                "locked", "sql",
            ],
            "سلکتور/DOM": [
                "selector", "not found", "element", "timeout",
                "waiting for",
            ],
            "مرورگر": [
                "chromium", "chrome", "browser", "playwright",
                "executable",
            ],
            "کلیدواژه": ["keyword", "کلیدواژه"],
            "عمومی": [],
        }
        classified: dict[str, list[dict]] = {
            k: [] for k in categories
        }
        for e in errors:
            msg = (
                str(e.get("msg", "")) + " "
                + str(e.get("context", ""))
            )
            placed = False
            for cat, hints in categories.items():
                if cat == "عمومی":
                    continue
                for hint in hints:
                    if hint.lower() in msg.lower():
                        classified[cat].append(e)
                        placed = True
                        break
                if placed:
                    break
            if not placed:
                classified["عمومی"].append(e)

        lines = [f"🧠 توضیح {len(errors)} خطای اخیر:"]
        for cat, items in classified.items():
            if not items:
                continue
            lines.append(f"\n📌 {cat} ({len(items)} مورد)")
            for e in items[:3]:
                msg = str(e.get("msg", ""))[:120]
                lines.append(f"   • {msg}")
            if len(items) > 3:
                lines.append(
                    f"   ... و {len(items) - 3} مورد دیگر"
                )

        lines.append("\n💡 پیشنهاد:")
        suggestions_added = False
        if classified["محدودیت AI"]:
            lines.append(
                "   • شارژ DeepSeek تموم شده → شارژ کن "
                "یا AI_PRIORITY=ollama بذار"
            )
            suggestions_added = True
        if classified["نشست/ورود"]:
            lines.append(
                "   • نشست منقضی شده → python login.py رو "
                "دوباره اجرا کن"
            )
            suggestions_added = True
        if classified["شبکه/اتصال"]:
            lines.append(
                "   • مشکل شبکه → پروکسی/VPN رو چک کن "
                "(BROWSER_PROXY در .env)"
            )
            suggestions_added = True
        if classified["سلکتور/DOM"]:
            lines.append(
                "   • DOM پلتفرم تغییر کرده → dump_dom رو "
                "چک کن و selector آپدیت کن"
            )
            suggestions_added = True
        if classified["مرورگر"]:
            lines.append(
                "   • مرورگر نیست/بسته → CHROME_PATH رو "
                "در .env چک کن"
            )
            suggestions_added = True
        if classified["دیتابیس"]:
            lines.append(
                "   • دیتابیس → storage/database/messages.db "
                "رو چک کن"
            )
            suggestions_added = True
        if not suggestions_added:
            lines.append(
                "   • خطاهای عمومی → storage/logs/app.jsonl "
                "رو دستی ببین"
            )

        return "\n".join(lines)