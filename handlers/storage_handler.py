# handlers/storage_handler.py
"""
ذخیرهٔ پیام‌های کلیدواژه‌دار به‌عنوان سرنخ.

چهار گارد (به ترتیب اجرا):
  ۰) پیامِ خودی / Saved Messages → رد (مگر save_self_messages=True و «فضای شخصی»).
  ۱) پیامِ خصوصیِ «مدیر» روی هر پلتفرمی → دستور است، نه سرنخ → رد.
  ۲) گروهِ غیرهدف (data/target_groups.txt) → رد.
  ۳) پیامِ بدون کلیدواژه (نه از finder، نه preset از کلکتور) → رد.
  ۴) پیامِ بدون tier مجاز → رد. پیش‌فرض فقط T1.

نسخهٔ ۵ (این نسخه — داده‌محور):
  - 🎯 همهٔ مارکرها از data/configs/markers.json می‌آید:
      * saved_markers           → لیست مارکرها
      * self_chat_marker        → مارکر «فضای شخصی»
      * target_filtered_platforms → پلتفرم‌های گروه‌محور
  - 🎯 `_allowed_tiers` از data/configs/tiers.json + PLUGINS_CONFIG.
  - 🐛 FIX: کلاس‌متغیرهای `_SAVED_MARKERS` و `_SELF_CHAT_MARKER` حذف شدند
    (به instance-attribute تبدیل شدند).
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۴.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any

from analyzers.keyword_finder import get_keyword_finder
from config import (
    ADMIN_IDS,
    PLATFORMS_ORDER,
)
from core.contracts.base_handler import BaseHandler
from core.kernel.logger import log_error, log_info, log_success, log_warning

# 🎯 داده‌محور
from data.loader import load_json


# ─── ثابت‌ها (env-based، بدون دیتا) ───
_LOG_PREVIEW_LEN = 100
_SLOW_HANDLE_MS = 3000
_STATS_INTERVAL = 100
_GUARD3_FIRST_LOG = 5        # اولین N بار لاگ کامل
_GUARD3_LOG_EVERY = 20       # بعد از آن، هر N بار یک لاگ


# ═══════════════════════════════════════════
# helpers — داده‌محور
# ═══════════════════════════════════════════
def _get_markers_data() -> dict:
    """بارگذاری markers.json — با fallback امن."""
    return load_json("markers")


def _get_tiers_data() -> dict:
    """بارگذاری tiers.json — با fallback امن."""
    return load_json("tiers")


def _get_target_filtered_platforms_fallback() -> tuple[str, ...]:
    """پلتفرم‌های گروه‌محور — از PLATFORMS_ORDER بدون web/linkedin/instagram.

    فقط اگر markers.json نداشت target_filtered_platforms استفاده می‌شود.
    """
    excluded = frozenset({"web", "linkedin", "instagram"})
    return tuple(p for p in PLATFORMS_ORDER if p not in excluded)


class StorageHandler(BaseHandler):
    """پیام کلیدواژه‌دار را به‌عنوان سرنخ ذخیره می‌کند (بعد از چهار گارد)."""

    # ═══════════════════════════════════════════
    # __init__
    # ═══════════════════════════════════════════
    def __init__(self, db, scorer=None, **kwargs):
        super().__init__()
        self.db = db
        self.keyword_finder = get_keyword_finder()

        from config import PLUGINS_CONFIG

        cfg = PLUGINS_CONFIG.get("storagehandler", {})

        # ─── تنظیمات از kwargs (اولویت) یا config ───
        self.keywords_only = kwargs.get(
            "keywords_only",
            cfg.get("keywords_only", True),
        )
        self.require_t1 = kwargs.get(
            "require_t1",
            cfg.get("require_t1", True),
        )
        self.save_self_messages = kwargs.get(
            "save_self_messages",
            cfg.get("save_self_messages", False),
        )

        # ═══════════════════════════════════════════
        # 🎯 داده‌محور: markers.json
        # ═══════════════════════════════════════════
        markers = _get_markers_data()

        self._saved_markers: tuple[str, ...] = tuple(
            str(x).strip()
            for x in markers.get("saved_markers", [])
            if str(x).strip()
        )
        self._self_chat_marker: str = str(
            markers.get("self_chat_marker", "")
        ).strip()

        # ─── پلتفرم‌های گروه‌محور ───
        tfp = markers.get("target_filtered_platforms")
        if isinstance(tfp, list) and tfp:
            self._target_filtered_platforms: tuple[str, ...] = tuple(
                str(p).strip() for p in tfp if str(p).strip()
            )
        else:
            self._target_filtered_platforms = (
                _get_target_filtered_platforms_fallback()
            )

        # ═══════════════════════════════════════════
        # 🎯 داده‌محور: tiers.json + config
        # ═══════════════════════════════════════════
        tiers = _get_tiers_data()
        self._valid_tiers: tuple[str, ...] = tuple(
            str(t).upper()
            for t in tiers.get("valid", ["T1", "T2", "T3"])
            if str(t).strip()
        )
        self._default_tier: str = str(
            tiers.get("default", "T1")
        ).upper()

        raw_tiers = (
            kwargs.get("allowed_tiers")
            or cfg.get("allowed_tiers")
        )
        self._allowed_tiers = self._parse_allowed_tiers(
            {"allowed_tiers": raw_tiers}
        )

        self.save_leads = kwargs.get(
            "save_leads", cfg.get("save_leads", True)
        )
        self.save_media = kwargs.get(
            "save_media", cfg.get("save_media", True)
        )

        # ─── admin_ids ───
        self._admin_ids: set[str] = {
            str(x).strip() for x in ADMIN_IDS if x and str(x).strip()
        }

        # ─── BALE_ADMIN_CHAT ───
        self._bale_admin_chat: str = (
            os.getenv("BALE_ADMIN_CHAT", "").strip()
        )

        # ─── آمار ───
        self.stats: dict[str, int] = {
            "total": 0,
            "messages_with_keywords": 0,
            "media_messages": 0,
            "dropped": 0,
            "admin_skipped": 0,
            "target_skipped": 0,
            "t2_only_skipped": 0,
            "self_skipped": 0,
            "errors": 0,
        }

        # ─── لاگ init ───
        log_success(
            f"✅ [STORAGE:init] آماده | "
            f"keywords_only={self.keywords_only} | "
            f"require_t1={self.require_t1} | "
            f"allowed_tiers={self._allowed_tiers} | "
            f"save_self_messages={self.save_self_messages} | "
            f"saved_markers={len(self._saved_markers)} | "
            f"self_marker={self._self_chat_marker!r} | "
            f"target_filtered={len(self._target_filtered_platforms)} | "
            f"admin_ids={len(self._admin_ids)} | "
            f"bale_admin_chat={'✅' if self._bale_admin_chat else '—'}"
        )

    def _parse_allowed_tiers(
        self, cfg: dict[str, Any]
    ) -> tuple[str, ...]:
        """اعتبارسنجی allowed_tiers.

        - اگر مقدار داشت → همان
        - وگرنه → (default_tier,) از tiers.json
        """
        raw = cfg.get("allowed_tiers")
        if raw and isinstance(raw, (list, tuple)):
            tiers = tuple(
                str(t).upper() for t in raw if str(t).strip()
            )
            if tiers:
                return tiers
        return (self._default_tier,)

    # ═══════════════════════════════════════════
    # هویت
    # ═══════════════════════════════════════════
    @property
    def handler_name(self) -> str:
        return "storage_handler"

    def can_handle(self, event_name: str) -> bool:
        return event_name == "message.received"

    # ═══════════════════════════════════════════
    # گارد ۱: پیام خصوصیِ مدیر
    # ═══════════════════════════════════════════
    def _is_admin_message(self, raw: dict) -> bool:
        """تشخیص پیام مدیر — سه لایه دفاعی شفاف.

        لایه ۱: user_id تو ADMIN_IDS (استاندارد)
        لایه ۲: chat_id تو ADMIN_IDS (برای بله که user_id نمی‌ده)
        لایه ۳: BALE_ADMIN_CHAT == chat_name (fallback نهایی)
        """
        if not self._admin_ids:
            return False

        user_id = str(raw.get("user_id") or "").strip()
        chat_id = str(raw.get("chat_id") or "").strip()
        chat_name = str(raw.get("chat_name") or "").strip()
        ct = (raw.get("chat_type") or "").lower()
        platform = raw.get("platform", "")

        # ─── فقط چت خصوصی می‌تونه «دستور» باشه ───
        is_private = (
            ct in ("private", "user")
            or (not ct and chat_id == user_id)
        )
        if not is_private:
            return False

        # ─── لایه ۱: user_id ───
        if user_id and user_id in self._admin_ids:
            log_info(
                f"⏭️ [STORAGE:guard1:L1] user_id={user_id}"
            )
            return True

        # ─── لایه ۲: chat_id ───
        if chat_id and chat_id in self._admin_ids:
            log_info(
                f"⏭️ [STORAGE:guard1:L2] chat_id={chat_id}"
            )
            return True

        # ─── لایه ۳: BALE_ADMIN_CHAT ───
        if (
            platform == "bale"
            and self._bale_admin_chat
            and chat_name == self._bale_admin_chat
        ):
            log_info(
                f"⏭️ [STORAGE:guard1:L3] "
                f"chat_name=«{chat_name}» == BALE_ADMIN_CHAT"
            )
            return True

        return False

    # ═══════════════════════════════════════════
    # گارد ۰: پیام خودی / Saved Messages
    # ═══════════════════════════════════════════
    def _is_self_or_saved(self, raw: dict) -> bool:
        """تشخیص پیام خودی / Saved Messages — داده‌محور.

        مارکرها از `markers.json`:
          - saved_markers → لیست
          - self_chat_marker → «فضای شخصی»

        حالت‌ها:
          - save_self_messages=True:
              «فضای شخصی» → قبول (return False)
              بقیه markerها → رد (return True)
          - save_self_messages=False:
              همه markerها → رد (return True)

        ⚠️ اگر لیست مارکرها خالی باشد → گارد خاموش (هیچ‌چیز رد نمی‌شود).
        """
        chat_name = str(raw.get("chat_name") or "").strip()
        if not chat_name:
            return False

        # ─── گارد خاموش اگر مارکرها نبودند ───
        if not self._saved_markers:
            return False

        self_marker = self._self_chat_marker

        # ─── حالت save_self_messages=True ───
        if self.save_self_messages:
            # ─── اگر «فضای شخصی» در نام هست → مجاز ───
            if self_marker and self_marker in chat_name:
                # چک کن مارکر «دیگر» هم نباشه
                # (مثلاً «Saved Messages فضای شخصی» → رد)
                for marker in self._saved_markers:
                    if marker == self_marker:
                        continue
                    if marker in chat_name:
                        log_info(
                            f"⏭️ [STORAGE:guard0] marker=«{marker}» "
                            f"(+{self_marker}) | "
                            f"chat_name=«{chat_name}»"
                        )
                        return True
                # فقط «فضای شخصی» → مجاز
                return False

            # ─── «فضای شخصی» نیست → بقیه مارکرها ───
            for marker in self._saved_markers:
                if marker == self_marker:
                    continue
                if marker in chat_name:
                    log_info(
                        f"⏭️ [STORAGE:guard0] marker=«{marker}» "
                        f"| chat_name=«{chat_name}»"
                    )
                    return True
            return False

        # ─── حالت save_self_messages=False: همه مارکرها رد ───
        for marker in self._saved_markers:
            if marker in chat_name:
                log_info(
                    f"⏭️ [STORAGE:guard0] marker=«{marker}» "
                    f"| chat_name=«{chat_name}»"
                )
                return True

        return False

    # ═══════════════════════════════════════════
    # گارد ۲: گروه هدف
    # ═══════════════════════════════════════════
    def _is_target_group(self, raw: dict) -> bool:
        """فیلتر گروه هدف — داده‌محور.

        پلتفرم‌های گروه‌محور از `markers.json`:
          target_filtered_platforms

        اگر این لیست خالی بود → گارد خاموش.
        """
        platform = raw.get("platform")

        # ─── گارد خاموش اگر لیست نبود ───
        if not self._target_filtered_platforms:
            return True

        if platform not in self._target_filtered_platforms:
            return True

        try:
            from analyzers.target_filter import get_target_groups
            tg = get_target_groups()
            if not tg.enabled():
                return True
            return tg.is_target(
                raw.get("chat_name"),
                raw.get("chat_username"),
                platform,
            )
        except Exception as e:
            log_warning(
                f"⚠️ [STORAGE:target] خطا در target_filter | "
                f"{type(e).__name__}: {e} — پیش‌فرض: قبول"
            )
            return True

    @staticmethod
    def _to_dict(raw) -> dict:
        """تبدیل event.data به dict — با همهٔ فیلدهای ممکن."""
        if isinstance(raw, dict):
            return dict(raw)
        return {
            "id": getattr(raw, "id", None),
            "text": getattr(raw, "text", ""),
            "chat_id": getattr(raw, "chat_id", ""),
            "chat_name": getattr(raw, "chat_name", "Unknown"),
            "chat_username": getattr(raw, "chat_username", None),
            "chat_link": getattr(raw, "chat_link", None),
            "chat_type": getattr(raw, "chat_type", None),
            "user_id": getattr(raw, "user_id", "unknown"),
            "sender_name": getattr(raw, "sender_name", None),
            "platform": getattr(raw, "platform", "bale"),
            "timestamp": (
                getattr(raw, "timestamp", None) or datetime.now()
            ),
            "media_type": getattr(raw, "media_type", "text"),
            "file_id": getattr(raw, "file_id", None),
            "local_file_path": getattr(raw, "local_file_path", None),
            "caption": getattr(raw, "caption", None),
            "reply_to": getattr(raw, "reply_to", None),
            "keywords": getattr(raw, "keywords", None),
        }

    # ═══════════════════════════════════════════
    # پردازش اصلی
    # ═══════════════════════════════════════════
    async def handle(self, event):
        t0 = time.monotonic()
        try:
            await self._handle_inner(event, t0)
        except Exception as e:
            self.stats["errors"] += 1
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_error(
                f"❌ [STORAGE:handle] {type(e).__name__}: {e} | "
                f"{elapsed_ms:.0f}ms — event گم شد"
            )

    async def _handle_inner(self, event, t0: float):
        raw = self._to_dict(event.data)

        log_info(
            f"🔍 [STORAGE:enter] platform={raw.get('platform')} | "
            f"chat_type={raw.get('chat_type')} | "
            f"user_id={raw.get('user_id')} | "
            f"chat_id={raw.get('chat_id')} | "
            f"chat_name={raw.get('chat_name')!r} | "
            f"admin_ids={self._admin_ids}"
        )

        platform = raw.get("platform", "?")
        chat_name = raw.get("chat_name", "?")
        text = (raw.get("text") or raw.get("caption") or "").strip()

        # ─── گارد ۰: پیام خودی/Saved ───
        if self._is_self_or_saved(raw):
            self.stats["self_skipped"] += 1
            log_info(
                f"⏭️ [STORAGE:guard0] پیام خودی/Saved — رد | "
                f"platform={platform} "
                f"chat={chat_name[:_LOG_PREVIEW_LEN]!r}"
            )
            return

        # ─── گارد ۱: مدیر ───
        if self._is_admin_message(raw):
            self.stats["admin_skipped"] += 1
            log_info(
                f"⏭️ [STORAGE:guard1] پیامِ مدیر — رد | "
                f"platform={platform} user_id={raw.get('user_id')}"
            )
            return

        # ─── گارد ۲: گروه هدف ───
        if not self._is_target_group(raw):
            self.stats["target_skipped"] += 1
            log_info(
                f"⏭️ [STORAGE:guard2] گروهِ غیرهدف — رد | "
                f"platform={platform} "
                f"chat={chat_name[:_LOG_PREVIEW_LEN]!r}"
            )
            return

        self.stats["total"] += 1

        # ─── تحلیل کلیدواژه + tier ───
        analyzed = await self._analyze(raw)

        # ─── گارد ۳: کلیدواژه ───
        if self.keywords_only and not analyzed.keywords:
            self.stats["dropped"] += 1
            dropped = self.stats["dropped"]
            if (
                dropped <= _GUARD3_FIRST_LOG
                or dropped % _GUARD3_LOG_EVERY == 0
            ):
                log_info(
                    f"🚮 [STORAGE:guard3] رد شد (بدون کلیدواژه) "
                    f"#{dropped} | platform={platform} | "
                    f"chat={chat_name[:_LOG_PREVIEW_LEN]!r} | "
                    f"text={text[:80]!r}"
                )
            return

        # ─── گارد ۴: tier مجاز ───
        tier = (getattr(analyzed, "metadata", {}) or {}).get(
            "tier", "T0"
        )
        if self.require_t1 and tier not in self._allowed_tiers:
            self.stats["t2_only_skipped"] += 1
            log_info(
                f"⏭️ [STORAGE:guard4] tier={tier} "
                f"(مجاز={self._allowed_tiers}) — رد | "
                f"platform={platform} kw={analyzed.keywords}"
            )
            return

        # ─── غنی‌سازی + ذخیره ───
        raw["keywords"] = ",".join(analyzed.keywords)
        raw = self._apply_score(raw, analyzed)
        raw = self._make_platform_unique_id(raw)

        if not await self._save_message(raw):
            return

        mt = raw.get("media_type") or "text"
        if mt != "text":
            self.stats["media_messages"] += 1
            log_info(
                f"📎 [STORAGE:media] {mt} | platform={platform} "
                f"chat={chat_name}"
            )

        await self._save_lead(raw, analyzed.keywords)

        self.stats["messages_with_keywords"] += 1
        total = self.stats["messages_with_keywords"]

        elapsed_ms = (time.monotonic() - t0) * 1000
        text_preview = text[:_LOG_PREVIEW_LEN]
        log_success(
            f"✅ [STORAGE:handle] #{total} | {platform} | "
            f"chat={chat_name} | kw={raw['keywords']} | "
            f"tier={raw.get('priority_tier', 'T0')} | "
            f"score={raw.get('opportunity_score', 0)} | "
            f"{elapsed_ms:.0f}ms | text={text_preview!r}"
        )

        if total % _STATS_INTERVAL == 0:
            self._log_stats()

        if elapsed_ms > _SLOW_HANDLE_MS:
            log_warning(
                f"🐌 [STORAGE:handle] کند | {elapsed_ms:.0f}ms | "
                f"platform={platform}"
            )

    # ═══════════════════════════════════════════
    # گام‌های داخلی
    # ═══════════════════════════════════════════
    async def _analyze(self, raw: dict):
        """تحلیل کلیدواژه + tier + priority."""
        from core.kernel.models import AnalyzedMessage

        msg_text = (
            raw.get("text") or raw.get("caption") or ""
        ) or ""

        analyzed = AnalyzedMessage(
            platform=raw.get("platform") or "unknown",
            platform_id=raw.get("id") or "",
            chat_id=raw.get("chat_id") or "",
            chat_name=raw.get("chat_name") or "Unknown",
            sender_name=(
                raw.get("sender_name") or raw.get("user_id") or ""
            ),
            text=msg_text,
            time=raw.get("timestamp") or datetime.now(),
        )
        analyzed = await self.keyword_finder.analyze(analyzed)

        # ─── Priority / tier ───
        try:
            from analyzers.priority_finder import get_priority_finder
            analyzed.metadata = getattr(analyzed, "metadata", {}) or {}
            info = get_priority_finder().analyze(analyzed.text)
            analyzed.metadata["tier"] = info["tier"]
            analyzed.metadata["products_str"] = info["products_str"]
            analyzed.metadata["brands_str"] = info["brands_str"]
        except Exception as e:
            log_warning(
                f"⚠️ [STORAGE:analyze] priority_finder | "
                f"{type(e).__name__}: {e} — tier=T0"
            )
            if not getattr(analyzed, "metadata", None):
                analyzed.metadata = {}
            analyzed.metadata.setdefault("tier", "T0")

        # ─── preset از کلکتور ───
        preset = raw.get("keywords")
        if preset:
            merged = set(analyzed.keywords or [])
            merged.update(
                k.strip()
                for k in str(preset).split(",")
                if k.strip()
            )
            analyzed.keywords = sorted(merged)

        return analyzed

    @staticmethod
    def _apply_score(raw: dict, analyzed) -> dict:
        """محاسبهٔ امتیاز قصد خرید."""
        try:
            from analyzers.lead_scorer import get_lead_scorer
            score, level, _ = get_lead_scorer().score(
                analyzed.text or "", has_keyword=True
            )
            raw["opportunity_score"] = score
            raw["opportunity_type"] = level
            meta = getattr(analyzed, "metadata", {}) or {}
            raw["priority_tier"] = meta.get("tier", "T0")
            raw["matched_products"] = meta.get("products_str", "")
            raw["brands"] = meta.get("brands_str", "")
        except Exception as e:
            log_warning(
                f"⚠️ [STORAGE:score] {type(e).__name__}: {e} — "
                f"score=0 tier=T0"
            )
            raw["opportunity_score"] = 0
            raw["opportunity_type"] = None
            raw["priority_tier"] = "T0"
            raw["matched_products"] = ""
            raw["brands"] = ""
        return raw

    @staticmethod
    def _make_platform_unique_id(raw: dict) -> dict:
        """یکتاسازی ID با prefix پلتفرم."""
        platform = raw.get("platform") or "unknown"
        current_id = raw.get("id")
        if not current_id:
            return raw

        current_str = str(current_id)
        prefix = f"{platform}_"

        if current_str.startswith(prefix):
            if not raw.get("platform_message_id"):
                raw["platform_message_id"] = current_str[len(prefix):]
            return raw

        raw["platform_message_id"] = current_str
        raw["id"] = f"{prefix}{current_str}"
        return raw

    async def _save_message(self, raw: dict) -> bool:
        """ذخیرهٔ پیام در DB."""
        t0 = time.monotonic()
        try:
            await self.db.save_message(raw)
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_info(
                f"💾 [STORAGE:save_msg] {elapsed_ms:.0f}ms | "
                f"id={raw.get('id')} platform={raw.get('platform')}"
            )
            return True
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_error(
                f"❌ [STORAGE:save_msg] {type(e).__name__}: {e} | "
                f"{elapsed_ms:.0f}ms | id={raw.get('id')} "
                f"platform={raw.get('platform')}"
            )
            return False

    async def _save_lead(self, raw: dict, keywords):
        """ذخیرهٔ سرنخ در جدول leads."""
        lead = {
            "id": raw.get("id"),
            "message_id": raw.get("id"),
            "platform": raw.get("platform"),
            "text": raw.get("text") or raw.get("caption", "") or "",
            "chat_id": raw.get("chat_id"),
            "chat_name": raw.get("chat_name"),
            "user_id": raw.get("user_id"),
            "timestamp": raw.get("timestamp"),
            "keywords": ",".join(keywords),
            "media_type": raw.get("media_type", "text"),
            "file_id": raw.get("file_id"),
            "opportunity_score": raw.get("opportunity_score", 0),
            "opportunity_type": raw.get("opportunity_type"),
            "priority_tier": raw.get("priority_tier", "T0"),
            "matched_products": raw.get("matched_products", ""),
            "brands": raw.get("brands", ""),
        }
        t0 = time.monotonic()
        try:
            await self.db.save_lead(lead)
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_info(
                f"🎯 [STORAGE:save_lead] {elapsed_ms:.0f}ms | "
                f"id={raw.get('id')} kw={lead['keywords']}"
            )
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_error(
                f"❌ [STORAGE:save_lead] {type(e).__name__}: {e} | "
                f"{elapsed_ms:.0f}ms | id={raw.get('id')}"
            )

    def _log_stats(self):
        """لاگ آمار — با درصد موفقیت."""
        total = self.stats["total"]
        saved = self.stats["messages_with_keywords"]
        success_rate = (saved / total * 100) if total else 0.0

        log_info(
            f"📊 [STORAGE:stats] total={total} | "
            f"saved={saved} ({success_rate:.1f}%) | "
            f"dropped={self.stats['dropped']} | "
            f"t2_only={self.stats['t2_only_skipped']} | "
            f"target_skip={self.stats['target_skipped']} | "
            f"admin_skip={self.stats['admin_skipped']} | "
            f"self_skip={self.stats['self_skipped']} | "
            f"media={self.stats['media_messages']} | "
            f"errors={self.stats['errors']}"
        )

    async def on_stop(self):
        await super().on_stop()