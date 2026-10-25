"""
🧪 ExperienceLearner — یادگیری از تجربه

- یاد می‌گیرد کدام مسیر برای کدام پلتفرم کار می‌کند
- هرگز دوبار یک اشتباه نمی‌کند
- به AIAssistant و کلکتورها سرویس می‌دهد

ساختار:
storage/project_knowledge/experiences.db
storage/project_knowledge/platform_notes.json
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from core.kernel.logger import log_info, log_warning, log_success


# ══════════════════════════════════════════════════════════
# مسیرها
# ══════════════════════════════════════════════════════════
try:
    from data import paths as data_paths
    BASE_DIR = Path(data_paths.PROJECT_ROOT) / "storage" / "project_knowledge"
except Exception:
    BASE_DIR = Path("storage") / "project_knowledge"

DB_FILE = BASE_DIR / "experiences.db"
NOTES_FILE = BASE_DIR / "platform_notes.json"


# ══════════════════════════════════════════════════════════
# ExperienceLearner
# ══════════════════════════════════════════════════════════
class ExperienceLearner:
    """یادگیری از تجربه پلتفرم‌ها."""

    def __init__(self):
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        self.db_path = str(DB_FILE)
        self._lock = threading.RLock()
        self._init_db()
        log_success(
            f"✅ [EXP:init] آماده | db={self.db_path}"
        )

    def _init_db(self):
        """ساخت جداول."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS experiences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    action TEXT NOT NULL,
                    value TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    note TEXT DEFAULT '',
                    count INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(platform, action, value)
                );
                CREATE INDEX IF NOT EXISTS idx_platform_action
                    ON experiences(platform, action);
                CREATE INDEX IF NOT EXISTS idx_success
                    ON experiences(success);

                CREATE TABLE IF NOT EXISTS platform_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    note TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_note_platform
                    ON platform_notes(platform);
            """)

    # ═══════════════════════════════════════════
    # ثبت تجربه
    # ═══════════════════════════════════════════
    def record(
        self,
        platform: str,
        action: str,
        value: str,
        success: bool,
        note: str = "",
    ) -> None:
        """ثبت یک تجربه.

        Args:
            platform: نام پلتفرم (bale, eitaa, ...).
            action: نوع کار (message_selector, login_url, ...).
            value: مقدار (selector, url, ...).
            success: True/False.
            note: توضیح اختیاری.
        """
        if not platform or not action or not value:
            return

        p = platform.lower().strip()
        a = action.lower().strip()
        v = value.strip()
        s = 1 if success else 0

        try:
            with self._lock, sqlite3.connect(self.db_path) as conn:
                # ─── چک موجود ───
                row = conn.execute(
                    "SELECT id, count, success FROM experiences "
                    "WHERE platform=? AND action=? AND value=?",
                    (p, a, v),
                ).fetchone()

                if row:
                    old_id, old_count, old_success = row
                    # ─── اگر قبلاً success بود و حالا fail → لاگ ───
                    if old_success == 1 and s == 0:
                        log_warning(
                            f"⚠️ [EXP:regression] {p}.{a} "
                            f"'{v[:40]}' قبلاً موفق بود، "
                            f"حالا خطا"
                        )
                    conn.execute(
                        "UPDATE experiences "
                        "SET count=?, success=?, note=?, updated_at=? "
                        "WHERE id=?",
                        (old_count + 1, s, note, datetime.now(), old_id),  # noqa: DTZ005
                    )
                else:
                    conn.execute(
                        "INSERT INTO experiences "
                        "(platform, action, value, success, note) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (p, a, v, s, note),
                    )

                conn.commit()

            mark = "✅" if success else "❌"
            log_info(
                f"{mark} [EXP:record] {p}.{a} = '{v[:60]}'"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [EXP:record] {type(e).__name__}: {e}"
            )

    # ═══════════════════════════════════════════
    # بهترین تجربه
    # ═══════════════════════════════════════════
    def get_best(
        self, platform: str, action: str
    ) -> str | None:
        """بهترین تجربه موفق (با بیشترین count)."""
        if not platform or not action:
            return None

        p = platform.lower().strip()
        a = action.lower().strip()

        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT value FROM experiences "
                    "WHERE platform=? AND action=? AND success=1 "
                    "ORDER BY count DESC, updated_at DESC LIMIT 1",
                    (p, a),
                ).fetchone()
                return row[0] if row else None
        except Exception as e:
            log_warning(
                f"⚠️ [EXP:get_best] {type(e).__name__}: {e}"
            )
            return None

    # ═══════════════════════════════════════════
    # آیا قبلاً شکست خورده؟
    # ═══════════════════════════════════════════
    def is_known_failure(
        self, platform: str, action: str, value: str
    ) -> bool:
        """آیا این مقدار قبلاً شکست خورده؟"""
        if not platform or not action or not value:
            return False

        p = platform.lower().strip()
        a = action.lower().strip()
        v = value.strip()

        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT success FROM experiences "
                    "WHERE platform=? AND action=? AND value=?",
                    (p, a, v),
                ).fetchone()
                if not row:
                    return False
                return row[0] == 0
        except Exception:
            return False

    # ═══════════════════════════════════════════
    # لیست شکست‌ها
    # ═══════════════════════════════════════════
    def get_failures(
        self, platform: str, action: str
    ) -> list[str]:
        """همه مقادیری که قبلاً شکست خورده‌اند."""
        if not platform or not action:
            return []

        p = platform.lower().strip()
        a = action.lower().strip()

        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT value FROM experiences "
                    "WHERE platform=? AND action=? AND success=0 "
                    "ORDER BY count DESC",
                    (p, a),
                ).fetchall()
                return [r[0] for r in rows]
        except Exception:
            return []

    # ═══════════════════════════════════════════
    # لیست موفق‌ها
    # ═══════════════════════════════════════════
    def get_successes(
        self, platform: str, action: str
    ) -> list[tuple[str, int]]:
        """همه مقادیر موفق (value, count)."""
        if not platform or not action:
            return []

        p = platform.lower().strip()
        a = action.lower().strip()

        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT value, count FROM experiences "
                    "WHERE platform=? AND action=? AND success=1 "
                    "ORDER BY count DESC",
                    (p, a),
                ).fetchall()
                return [(r[0], r[1]) for r in rows]
        except Exception:
            return []

    # ═══════════════════════════════════════════
    # نکات پلتفرم
    # ═══════════════════════════════════════════
    def add_note(
        self,
        platform: str,
        note: str,
        category: str = "general",
    ) -> None:
        """افزودن نکته."""
        if not platform or not note:
            return

        p = platform.lower().strip()
        c = category.lower().strip()

        try:
            with self._lock, sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO platform_notes "
                    "(platform, category, note) VALUES (?, ?, ?)",
                    (p, c, note[:1000]),
                )
                conn.commit()

            log_info(
                f"📝 [EXP:note] {p}.{c} | {note[:60]}"
            )
        except Exception as e:
            log_warning(
                f"⚠️ [EXP:note] {type(e).__name__}: {e}"
            )

    def get_notes(
        self, platform: str, category: str | None = None
    ) -> list[str]:
        """نکات یک پلتفرم."""
        if not platform:
            return []

        p = platform.lower().strip()

        try:
            with sqlite3.connect(self.db_path) as conn:
                if category:
                    rows = conn.execute(
                        "SELECT note FROM platform_notes "
                        "WHERE platform=? AND category=? "
                        "ORDER BY created_at DESC",
                        (p, category.lower().strip()),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT note FROM platform_notes "
                        "WHERE platform=? "
                        "ORDER BY created_at DESC",
                        (p,),
                    ).fetchall()
                return [r[0] for r in rows]
        except Exception:
            return []

    # ═══════════════════════════════════════════
    # آمار
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict:
        """آمار کامل."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                total = conn.execute(
                    "SELECT COUNT(*) FROM experiences"
                ).fetchone()[0]
                success = conn.execute(
                    "SELECT COUNT(*) FROM experiences WHERE success=1"
                ).fetchone()[0]
                fail = conn.execute(
                    "SELECT COUNT(*) FROM experiences WHERE success=0"
                ).fetchone()[0]
                notes = conn.execute(
                    "SELECT COUNT(*) FROM platform_notes"
                ).fetchone()[0]
                platforms = conn.execute(
                    "SELECT DISTINCT platform FROM experiences"
                ).fetchall()

                return {
                    "total": total,
                    "success": success,
                    "fail": fail,
                    "notes": notes,
                    "platforms": [p[0] for p in platforms],
                }
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    # ═══════════════════════════════════════════
    # Export
    # ═══════════════════════════════════════════
    def export_json(self, path: str | None = None) -> str:
        """Export به JSON."""
        path = path or str(BASE_DIR / "experiences.json")
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                exp = [dict(r) for r in conn.execute(
                    "SELECT * FROM experiences ORDER BY platform, action, count DESC"
                ).fetchall()]
                notes = [dict(r) for r in conn.execute(
                    "SELECT * FROM platform_notes ORDER BY platform, created_at DESC"
                ).fetchall()]

            data = {
                "exported_at": datetime.now().isoformat(),  # noqa: DTZ005
                "experiences": exp,
                "notes": notes,
            }
            Path(path).write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log_success(f"✅ [EXP:export] → {path}")
            return path
        except Exception as e:
            log_warning(
                f"⚠️ [EXP:export] {type(e).__name__}: {e}"
            )
            return ""

    # ═══════════════════════════════════════════
    # Context برای AI
    # ═══════════════════════════════════════════
    def build_context(
        self, platform: str | None = None, limit: int = 5
    ) -> str:
        """ساخت context برای AI.

        Args:
            platform: اگر داده شود، فقط همان پلتفرم.
            limit: حداکثر تعداد.

        Returns:
            متن آماده یا "".
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                if platform:
                    rows = conn.execute(
                        "SELECT platform, action, value, success "
                        "FROM experiences WHERE platform=? "
                        "ORDER BY success DESC, count DESC LIMIT ?",
                        (platform.lower().strip(), limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT platform, action, value, success "
                        "FROM experiences "
                        "ORDER BY success DESC, count DESC LIMIT ?",
                        (limit,),
                    ).fetchall()

            if not rows:
                return ""

            parts = ["## تجربه‌های پلتفرم:"]
            for r in rows:
                p, a, v, s = r
                mark = "✅" if s else "❌"
                parts.append(f"- {mark} {p}.{a} = {v[:80]}")
            return "\n".join(parts)
        except Exception:
            return ""


# ══════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════
_shared: ExperienceLearner | None = None
_lock = threading.Lock()


def get_experience() -> ExperienceLearner:
    """دریافت نمونه Singleton."""
    global _shared
    if _shared is None:
        with _lock:
            if _shared is None:
                _shared = ExperienceLearner()
    return _shared


def reset_experience() -> None:
    """پاک کردن Singleton."""
    global _shared
    with _lock:
        _shared = None
        log_info("🗑️ [EXP:singleton] reset")


__all__ = [
    "ExperienceLearner",
    "get_experience",
    "reset_experience",
]
