# handlers/storage_handler.py
"""
ذخیرهٔ پیام‌های کلیدواژه‌دار به‌عنوان سرنخ.

چهار گارد (به ترتیب اجرا):
  ۱) پیامِ خصوصیِ «مدیر» روی هر پلتفرمی → دستور است، نه سرنخ → رد.
  ۲) گروهِ غیرهدف (data/target_groups.txt) → رد.
  ۳) پیامِ بدون کلیدواژه (نه از finder، نه preset از کلکتور) → رد.
  ۴) پیامِ بدون tier مجاز → رد. پیش‌فرض فقط T1.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from analyzers.keyword_finder import get_keyword_finder
from core.contracts.base_handler import BaseHandler
from core.kernel.logger import log_error, log_info, log_success, log_warning

# ─── ثابت‌ها ───
_LOG_PREVIEW_LEN = 100
_SLOW_HANDLE_MS = 3000
_STATS_INTERVAL = 100
_ALERT_TASK_MAX = 50


class StorageHandler(BaseHandler):
    """پیام کلیدواژه‌دار را به‌عنوان سرنخ ذخیره می‌کند (بعد از چهار گارد)."""

    _TARGET_FILTERED_PLATFORMS = (
        "eitaa", "bale", "rubika", "telegram", "whatsapp",
    )

    def __init__(self, db, scorer=None, **kwargs):
        super().__init__()
        self.db = db
        self.keyword_finder = get_keyword_finder()

        from config import LEAD_ALERTS, PLUGINS_CONFIG

        cfg = PLUGINS_CONFIG.get("storagehandler", {})
        self.keywords_only = cfg.get("keywords_only", True)
        self.require_t1 = cfg.get("require_t1", True)
        self._allowed_tiers = self._parse_allowed_tiers(cfg)
        self._alert_tiers = tuple(
            str(t).upper()
            for t in LEAD_ALERTS.get("tiers", ["T1"])
        )

        _adm = PLUGINS_CONFIG.get("adminhandler", {})
        self._admin_ids = {
            str(x).strip() for x in _adm.get("admin_ids", []) if x
        }

        self._alert_tasks: list[asyncio.Task] = []

        self.stats: dict[str, int] = {
            "total": 0,
            "messages_with_keywords": 0,
            "media_messages": 0,
            "dropped": 0,
            "admin_skipped": 0,
            "target_skipped": 0,
            "t2_only_skipped": 0,
            "errors": 0,
        }

        log_success(
            f"✅ [STORAGE:init] آماده | "
            f"keywords_only={self.keywords_only} | "
            f"require_t1={self.require_t1} | "
            f"allowed_tiers={self._allowed_tiers} | "
            f"alert_tiers={self._alert_tiers} | "
            f"admin_ids={len(self._admin_ids)}"
        )

    @staticmethod
    def _parse_allowed_tiers(cfg: dict[str, Any]) -> tuple:
        raw = cfg.get("allowed_tiers")
        if raw and isinstance(raw, (list, tuple)):
            tiers = tuple(
                str(t).upper() for t in raw if str(t).strip()
            )
            if tiers:
                return tiers
        return ("T1",)

    @property
    def handler_name(self) -> str:
        return "storage_handler"

    def can_handle(self, event_name: str) -> bool:
        return event_name == "message.received"

    # ═══════════════════════════════════════════
    # گارد ۱: پیام خصوصیِ مدیر
    # ═══════════════════════════════════════════
    def _is_admin_message(self, raw: dict) -> bool:
        if not self._admin_ids:
            return False
        user_id = str(raw.get("user_id") or "").strip()
        if not user_id or user_id not in self._admin_ids:
            return False

        ct = (raw.get("chat_type") or "").lower()
        chat_id = str(raw.get("chat_id") or "").strip()

        if ct in ("private", "user"):
            return True
        if not ct and chat_id == user_id:
            return True
        return False

    # ═══════════════════════════════════════════
    # گارد ۲: گروه هدف
    # ═══════════════════════════════════════════
    def _is_target_group(self, raw: dict) -> bool:
        platform = raw.get("platform")
        if platform not in self._TARGET_FILTERED_PLATFORMS:
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
            await self._handle_inner(event)
        except Exception as e:
            self.stats["errors"] += 1
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_error(
                f"❌ [STORAGE:handle] {type(e).__name__}: {e} | "
                f"{elapsed_ms:.0f}ms — event گم شد"
            )

    async def _handle_inner(self, event):
        raw = self._to_dict(event.data)

        # ─── لاگ دیباگ ───
        log_info(
            f"🔍 [STORAGE:enter] platform={raw.get('platform')} | "
            f"chat_type={raw.get('chat_type')} | "
            f"user_id={raw.get('user_id')} | "
            f"chat_id={raw.get('chat_id')}"
        )

        platform = raw.get("platform", "?")
        chat_name = raw.get("chat_name", "?")
        text = (raw.get("text") or raw.get("caption") or "").strip()

        # ─── گارد ۱: مدیر ───
        if self._is_admin_message(raw):
            self.stats["admin_skipped"] += 1
            log_info(
                f"⏭️ [STORAGE:guard1] پیامِ مدیر — رد | "
                f"platform={platform} user_id={raw.get('user_id')}"
            )
            return
        log_info("✅ [STORAGE:guard1] pass")

        # ─── گارد ۲: گروه هدف ───
        if not self._is_target_group(raw):
            self.stats["target_skipped"] += 1
            log_info(
                f"⏭️ [STORAGE:guard2] گروهِ غیرهدف — رد | "
                f"platform={platform} "
                f"chat={chat_name[:_LOG_PREVIEW_LEN]!r}"
            )
            return
        log_info("✅ [STORAGE:guard2] pass")

        self.stats["total"] += 1

        # ─── تحلیل کلیدواژه + tier ───
        analyzed = await self._analyze(raw)
        log_info(
            f"🔍 [STORAGE:analyze] kw={analyzed.keywords} | "
            f"tier={(analyzed.metadata or {}).get('tier', 'T0')}"
        )

        # ─── گارد ۳: کلیدواژه ───
        if self.keywords_only and not analyzed.keywords:
            self.stats["dropped"] += 1
            log_info(
                f"⏭️ [STORAGE:guard3] بدون کلیدواژه — رد | "
                f"text={text[:_LOG_PREVIEW_LEN]!r}"
            )
            return
        log_info(f"✅ [STORAGE:guard3] pass | kw={analyzed.keywords}")

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
        log_info(f"✅ [STORAGE:guard4] pass | tier={tier}")

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
        self._schedule_alert(raw)

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

    def _schedule_alert(self, raw: dict):
        from config import LEAD_ALERTS

        if not LEAD_ALERTS.get("enabled", False):
            return
        try:
            self._alert_tasks = [
                t for t in self._alert_tasks if not t.done()
            ]
            if len(self._alert_tasks) >= _ALERT_TASK_MAX:
                log_warning(
                    f"⚠️ [STORAGE:alert] {len(self._alert_tasks)} task "
                    f"pending — هشدار جدید skip شد"
                )
                return
            task = asyncio.create_task(self._alert_lead(raw))
            self._alert_tasks.append(task)
            task.add_done_callback(self._on_alert_done)
        except Exception as e:
            log_warning(
                f"⚠️ [STORAGE:alert] scheduling | "
                f"{type(e).__name__}: {e}"
            )

    def _on_alert_done(self, task: asyncio.Task) -> None:
        try:
            self._alert_tasks.remove(task)
        except ValueError:
            pass
        try:
            exc = task.exception()
            if exc:
                log_warning(
                    f"⚠️ [STORAGE:alert:task] "
                    f"{type(exc).__name__}: {exc}"
                )
        except (asyncio.CancelledError, Exception):
            pass

    async def _alert_lead(self, raw: dict):
        from config import LEAD_ALERTS
        from core.adapters import messenger_registry
        from core.services.daily_reporter import platform_label

        tier = raw.get("priority_tier")
        if tier not in self._alert_tiers:
            log_info(
                f"⏭️ [STORAGE:alert] tier={tier} "
                f"(مجاز={self._alert_tiers}) — رد"
            )
            return

        chat_name = raw.get("chat_name")
        if not chat_name or chat_name == "?":
            log_info("⏭️ [STORAGE:alert] chat_name خالی — رد")
            return

        score = int(raw.get("opportunity_score") or 0)
        min_score = int(LEAD_ALERTS.get("min_score", 0))
        if score < min_score:
            log_info(
                f"⏭️ [STORAGE:alert] score={score} < min={min_score} "
                f"— رد"
            )
            return

        try:
            plabel = platform_label(raw.get("platform"))
        except Exception as e:
            log_warning(
                f"⚠️ [STORAGE:alert] platform_label | "
                f"{type(e).__name__}: {e}"
            )
            plabel = raw.get("platform") or "?"

        text = (
            "🔔 سرنخ جدید پیدا شد\n"
            f"{plabel}\n"
            f"👥 گروه: {chat_name}\n"
            f"🔑 کلیدواژه: {raw.get('keywords')}\n"
            f"🎯 {raw.get('opportunity_type') or '-'} "
            f"(امتیاز {score})\n"
            f"💬 {(raw.get('text') or raw.get('caption') or '')[:200]}\n"
            f"🕒 {raw.get('timestamp')}"
        )
        if raw.get("chat_link"):
            text += f"\n🔗 {raw.get('chat_link')}"

        t0 = time.monotonic()
        try:
            ok = await messenger_registry.notify_admins(text)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if ok:
                log_success(
                    f"🔔 [STORAGE:alert] هشدار ارسال شد | "
                    f"{elapsed_ms:.0f}ms | tier={tier} score={score} | "
                    f"kw={raw.get('keywords')}"
                )
            else:
                log_warning(
                    f"⚠️ [STORAGE:alert] ارسال نشد (مدیر/فرستنده) | "
                    f"{elapsed_ms:.0f}ms"
                )
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_error(
                f"❌ [STORAGE:alert] notify_admins | "
                f"{type(e).__name__}: {e} | {elapsed_ms:.0f}ms"
            )

    def _log_stats(self):
        log_info(
            f"📊 [STORAGE:stats] total={self.stats['total']} | "
            f"saved={self.stats['messages_with_keywords']} | "
            f"dropped={self.stats['dropped']} | "
            f"t2_only={self.stats['t2_only_skipped']} | "
            f"target_skip={self.stats['target_skipped']} | "
            f"admin_skip={self.stats['admin_skipped']} | "
            f"media={self.stats['media_messages']} | "
            f"errors={self.stats['errors']}"
        )

    async def on_stop(self):
        for task in list(self._alert_tasks):
            if not task.done():
                task.cancel()
        if self._alert_tasks:
            await asyncio.gather(
                *self._alert_tasks, return_exceptions=True
            )
            self._alert_tasks.clear()
        await super().on_stop()