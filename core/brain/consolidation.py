"""
🌙 Consolidation Task — خواب مغز

هر بار اجرا:
- حافظه کوتاه → بلندمدت (فقط مهم‌ها)
- پاکسازی حافظه کوتاه
- ذخیره آمار
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime
from pathlib import Path

from core.kernel.logger import log_error, log_info, log_success, log_warning

# ══════════════════════════════════════════════════════════
# تنظیمات
# ══════════════════════════════════════════════════════════
try:
    from data import paths as data_paths

    BASE_DIR = Path(data_paths.PROJECT_ROOT) / "storage" / "project_knowledge"
except Exception:
    BASE_DIR = Path("storage") / "project_knowledge"

STATS_FILE = BASE_DIR / "consolidation_stats.json"
# 🆕 FIX-BRAIN #6: حداقل ۴ ساعت فاصله (نه ۲) — خواب مغز نباید مکرر باشد
INTERVAL_HOURS = 4
IMPORTANCE_THRESHOLD = 0.7
WORKING_KEEP = 10


# ══════════════════════════════════════════════════════════
# آمار
# ══════════════════════════════════════════════════════════
def _load_stats() -> dict:
    if STATS_FILE.exists():
        try:
            return json.loads(STATS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log_warning(
                f"⚠️ [core.brain.consolidation] {
                    type(e).__name__}: {e}")
    return {
        "runs": 0,
        "moved_total": 0,
        "last_run": None,
        "started_at": None,
    }


def _save_stats(stats: dict) -> None:
    try:
        STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATS_FILE.write_text(
            json.dumps(stats, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log_warning(f"⚠️ [CONS:save_stats] {e}")


# ══════════════════════════════════════════════════════════
# اجرای اصلی
# ══════════════════════════════════════════════════════════
async def run_once() -> dict:
    """یک بار اجرای Consolidation."""
    from core.brain.brain_memory import get_brain_memory

    bm = get_brain_memory()
    working_before = len(bm.working.items)
    moved = 0

    # ۱. انتقال مهم‌ها به بلندمدت
    for item in bm.working.items:
        imp = item.get("imp", 0.5)
        if imp > IMPORTANCE_THRESHOLD:
            try:
                bm.long_term.add(
                    category="consolidated",
                    key=item["q"][:200],
                    value=item["a"][:1000],
                    importance=imp,
                )
                moved += 1
            except Exception as e:
                log_warning(f"⚠️ [CONS:move] {e}")

    # ۲. پاکسازی حافظه کوتاه (از طریق API عمومی — بدون دست‌زدن به _save)
    trimmed = bm.working.trim(WORKING_KEEP)
    if trimmed:
        log_info(f"🧹 [CONS:trim] {trimmed} آیتم از حافظه کوتاه حذف شد")

    working_after = len(bm.working.items)

    # ۳. ذخیره آمار
    stats = _load_stats()
    stats["runs"] += 1
    stats["moved_total"] += moved
    stats["last_run"] = datetime.now().isoformat()  # noqa: DTZ005
    if not stats["started_at"]:
        stats["started_at"] = datetime.now().isoformat()  # noqa: DTZ005
    _save_stats(stats)

    # 🆕 Memory Decay (فراموشی تدریجی)
    decay_result = {"updated": 0, "deleted": 0}
    try:
        from core.brain.brain_memory import (
            get_persistent_memory,
        )

        pm = get_persistent_memory()
        if pm and pm.enabled:
            decay_result = pm.decay(
                factor=0.95,
                days_per_cycle=7,
                min_importance=0.2,
                delete_after_days=30,
            )
            log_info(
                f"🧹 [CONS:decay] "
                f"updated={decay_result['updated']} | "
                f"deleted={decay_result['deleted']}"
            )
    except Exception as e:
        log_warning(f"⚠️ [CONS:decay] {e}")

    return {
        "moved": moved,
        "working_before": working_before,
        "working_after": working_after,
        "runs": stats["runs"],
        "moved_total": stats["moved_total"],
        # 🆕 Decay stats
        "decay_updated": decay_result.get("updated", 0),
        "decay_deleted": decay_result.get("deleted", 0),
    }


# ══════════════════════════════════════════════════════════
# حلقه پس‌زمینه
# ══════════════════════════════════════════════════════════
_task: asyncio.Task | None = None
_lock = threading.Lock()


async def _loop():
    """حلقه Consolidation."""
    log_info(
        f"🌙 [CONS:loop] شروع | interval={INTERVAL_HOURS}h | "
        f"threshold={IMPORTANCE_THRESHOLD} | keep={WORKING_KEEP}"
    )

    # اولین اجرا: ۳۰ ثانیه بعد
    await asyncio.sleep(30)

    while True:
        try:
            # چک: از آخرین اجرا ۲ ساعت گذشته؟
            stats = _load_stats()
            last = stats.get("last_run")
            if last:
                try:
                    last_dt = datetime.fromisoformat(last)
                    elapsed = (datetime.now() - last_dt).total_seconds()  # noqa: DTZ005
                    if elapsed < INTERVAL_HOURS * 3600:
                        remaining = INTERVAL_HOURS * 3600 - elapsed
                        log_info(
                            f"⏳ [CONS:loop] آخرین اجرا "
                            f"{int(elapsed / 60)} دقیقه پیش — "
                            f"صبر {int(remaining / 60)} دقیقه"
                        )
                        await asyncio.sleep(min(remaining, 3600))
                        continue
                except Exception as e:
                    log_warning(
                        f"⚠️ [core.brain.consolidation] {
                            type(e).__name__}: {e}")

            result = await run_once()
            log_success(
                f"🌙 [CONS:done] run#{result['runs']} | "
                f"moved={result['moved']} | "
                f"working: {result['working_before']}→"
                f"{result['working_after']} | "
                f"total_moved={result['moved_total']}"
            )
        except asyncio.CancelledError:
            log_info("⏹️ [CONS:loop] متوقف شد")
            raise
        except Exception as e:
            log_warning(f"⚠️ [CONS:loop] {type(e).__name__}: {e}")

        await asyncio.sleep(INTERVAL_HOURS * 3600)


def start_consolidation() -> asyncio.Task:
    """شروع حلقه در پس‌زمینه."""
    global _task
    with _lock:
        if _task is not None and not _task.done():
            log_info("ℹ️ [CONS:start] از قبل در حال اجرا")
            return _task
        _task = asyncio.create_task(_loop())
        log_success("✅ [CONS:start] حلقه راه افتاد")
        return _task


def stop_consolidation() -> None:
    """توقف حلقه."""
    global _task
    with _lock:
        if _task is not None:
            _task.cancel()
            _task = None
            log_info("🛑 [CONS:stop] توقف")


def get_stats() -> dict:
    """آمار Consolidation."""
    return _load_stats()


__all__ = [
    "get_stats",
    "run_once",
    "start_consolidation",
    "stop_consolidation",
]
