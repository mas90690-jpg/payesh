"""
حافظهٔ گفتگوی ChatOps — JSONL با cap.

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX: `recent(n)` حالا `n` را با `max_entries` کلیپ می‌کند
    (قبلاً اگر `n > max_entries` بود، بیشتر از سقف برمی‌گرداند).
  - 🐛 FIX: `remember()` حالا atomic-cap می‌کند (tmp + replace)
    به‌جای بازنویسی مستقیم.
  - 🐛 FIX: `remember()` حالا fsync می‌زند (durability).
  - 🎯 خواندن لاگ با seek از انتها — برای فایل‌های بزرگ.
  - 🎯 `_read_tail_lines()` helper جدید.
  - 🎯 `_atomic_write_lines()` helper جدید.
  - 🎯 کلیپ `n` و `max_entries` با مقادیر معقول.
  - 🎯 اعتبارسنجی ورودی (`q`, `a` خالی).
  - 🎯 `count()` و `clear()` متدهای جدید.
  - 🎯 `get_stats()` برای debug.
  - 🎯 `close()` برای سازگاری با context manager.
  - 🎯 لاگ‌های کامل‌تر با prefixهای [MEM:...].
  - ✅ حفظ همهٔ رفتار نسخهٔ ۱ (JSONL، max_entries).
"""
from __future__ import annotations

import contextlib
import json
import os
from datetime import datetime
from typing import Any

from typing_extensions import Self

from core.kernel.logger import log_error, log_info, log_warning

# ═══════════════════════════════════════════════════════════
# ثابت‌ها
# ═══════════════════════════════════════════════════════════
_MAX_ENTRIES_DEFAULT = 200
_MAX_ENTRIES_HARD_CAP = 10_000

_RECENT_N_DEFAULT = 6
_RECENT_N_HARD_CAP = 200

# سقف خواندن از انتهای فایل (برای فایل‌های بزرگ)
_TAIL_READ_BYTES = 256 * 1024

# پیش‌نمایش متن در لاگ
_LOG_PREVIEW_LEN = 60

# محدودیت طول متن سوال/جواب (جلوگیری از فایل‌های حجیم)
_MAX_QUESTION_LEN = 2000
_MAX_ANSWER_LEN = 5000


class AdminMemory:
    """ذخیره/خواندنِ آخرین گفتگوها برای context دادن به AI.

    فرمت فایل: JSONL (یک JSON در هر خط)
    هر خط: {"ts": "...", "q": "...", "a": "..."}
    """

    def __init__(self, path: str, max_entries: int = _MAX_ENTRIES_DEFAULT):
        self.path = path

        # 🎯 FIX: کلیپ max_entries
        try:
            max_e = int(max_entries)
        except (TypeError, ValueError):
            max_e = _MAX_ENTRIES_DEFAULT
        self.max_entries = max(
            1, min(max_e, _MAX_ENTRIES_HARD_CAP)
        )

    # ═══════════════════════════════════════════
    # helpers
    # ═══════════════════════════════════════════
    @staticmethod
    def _read_tail_lines(
        path: str, max_bytes: int = _TAIL_READ_BYTES
    ) -> list[str]:
        """خواندن خطوط از انتهای فایل — با seek.

        Args:
            path: مسیر فایل.
            max_bytes: حداکثر بایت از انتها.

        Returns:
            لیست خطوط (بدون newline).
        """
        if not os.path.exists(path):
            return []
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                if size > max_bytes:
                    f.seek(-max_bytes, os.SEEK_END)
                    f.readline()  # خط ناقص اول را دور بریز
                data = f.read()
            return data.decode("utf-8", errors="ignore").splitlines()
        except Exception as e:
            log_warning(
                f"⚠️ [MEM:tail_read] {type(e).__name__}: {e} | "
                f"path={os.path.basename(path)}"
            )
            return []

    @staticmethod
    def _atomic_write_lines(
        path: str, lines: list[str]
    ) -> bool:
        """نوشتن atomic خطوط (tmp + replace + fsync).

        Returns:
            True اگر موفق.
        """
        tmp = path + ".tmp"
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(lines)
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except (AttributeError, OSError):
                    pass

            os.replace(tmp, path)

            # fsync دایرکتوری (POSIX)
            if parent:
                try:
                    dir_fd = os.open(parent, os.O_DIRECTORY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except (AttributeError, OSError):
                    pass

            return True
        except Exception as e:
            log_warning(
                f"⚠️ [MEM:atomic_write] {type(e).__name__}: {e} | "
                f"path={os.path.basename(path)}"
            )
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            return False

    # ═══════════════════════════════════════════
    # recent
    # ═══════════════════════════════════════════
    def recent(self, n: int = _RECENT_N_DEFAULT) -> str:
        """🎯 FIX: متن آماده برای درج در system prompt.

        Args:
            n: تعداد آیتم‌های اخیر.

        Returns:
            متن آماده برای system prompt (یا "" اگر خالی).
        """
        # ─── کلیپ n ───
        try:
            n_int = int(n)
        except (TypeError, ValueError):
            n_int = _RECENT_N_DEFAULT

        # 🎯 FIX: کلیپ n با max_entries
        n_int = max(1, min(n_int, self.max_entries, _RECENT_N_HARD_CAP))

        if not os.path.exists(self.path):
            return ""

        # 🎯 FIX: خواندن با seek از انتها
        lines = self._read_tail_lines(self.path)
        if not lines:
            return ""

        # آخرین n خط
        recent_lines = lines[-n_int:] if len(lines) > n_int else lines

        items: list[str] = []
        for ln in recent_lines:
            if not ln.strip():
                continue
            try:
                o = json.loads(ln)
                q = str(o.get("q", "") or "")[:120]
                a = str(o.get("a", "") or "")[:160]
                if q or a:
                    items.append(f"س: {q}\nج: {a}")
            except json.JSONDecodeError:
                # خط ناقص/نامعتبر — بی‌صدا رد
                continue
            except Exception:
                continue

        if not items:
            return ""

        return "\n\n# گفتگوی اخیر (حافظه):\n" + "\n".join(items)

    # ═══════════════════════════════════════════
    # remember
    # ═══════════════════════════════════════════
    def remember(self, q: str, a: str) -> bool:
        """🎯 FIX: ذخیرهٔ گفتگو — با cap و atomic.

        Args:
            q: سوال.
            a: جواب.

        Returns:
            True اگر ذخیره شد، False اگر رد شد.
        """
        # ─── اعتبارسنجی ───
        q_str = str(q or "").strip()
        a_str = str(a or "").strip()

        if not q_str and not a_str:
            return False

        # محدودیت طول
        if len(q_str) > _MAX_QUESTION_LEN:
            q_str = q_str[:_MAX_QUESTION_LEN]
        if len(a_str) > _MAX_ANSWER_LEN:
            a_str = a_str[:_MAX_ANSWER_LEN]

        # ─── append ───
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            entry = {
                "ts": datetime.now().isoformat(),  # noqa: DTZ005
                "q": q_str,
                "a": a_str,
            }

            with open(self.path, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(entry, ensure_ascii=False) + "\n"
                )
        except Exception as e:
            log_warning(
                f"⚠️ [MEM:write] append | "
                f"{type(e).__name__}: {e} | "
                f"path={os.path.basename(self.path)}"
            )
            return False

        # ─── cap (اگر لازم بود) ───
        try:
            lines = self._read_tail_lines(
                self.path, max_bytes=_TAIL_READ_BYTES * 4
            )
            if len(lines) > self.max_entries:
                # 🎯 FIX: atomic cap
                kept = lines[-self.max_entries:]
                if self._atomic_write_lines(self.path, kept):
                    log_info(
                        f"🧹 [MEM:cap] {len(lines)} → {len(kept)} | "
                        f"path={os.path.basename(self.path)}"
                    )
                else:
                    log_warning(
                        f"⚠️ [MEM:cap] atomic write ناموفق | "
                        f"path={os.path.basename(self.path)}"
                    )
        except Exception as e:
            log_warning(
                f"⚠️ [MEM:cap] {type(e).__name__}: {e} | "
                f"path={os.path.basename(self.path)}"
            )

        return True

    # ═══════════════════════════════════════════
    # count
    # ═══════════════════════════════════════════
    def count(self) -> int:
        """🎯 تعداد آیتم‌های ذخیره‌شده."""
        if not os.path.exists(self.path):
            return 0
        try:
            lines = self._read_tail_lines(
                self.path, max_bytes=_TAIL_READ_BYTES * 4
            )
            return sum(1 for ln in lines if ln.strip())
        except Exception as e:
            log_warning(
                f"⚠️ [MEM:count] {type(e).__name__}: {e}"
            )
            return 0

    # ═══════════════════════════════════════════
    # clear
    # ═══════════════════════════════════════════
    def clear(self) -> bool:
        """🎯 پاک کردن حافظه — atomic.

        Returns:
            True اگر موفق.
        """
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
                log_info(
                    f"🗑️ [MEM:clear] پاک شد | "
                    f"path={os.path.basename(self.path)}"
                )
            return True
        except Exception as e:
            log_warning(
                f"⚠️ [MEM:clear] {type(e).__name__}: {e}"
            )
            return False

    # ═══════════════════════════════════════════
    # get_stats
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت — برای debug."""
        exists = os.path.exists(self.path)
        size_bytes = 0
        if exists:
            with contextlib.suppress(Exception):
                size_bytes = os.path.getsize(self.path)

        return {
            "path": self.path,
            "exists": exists,
            "size_bytes": size_bytes,
            "count": self.count() if exists else 0,
            "max_entries": self.max_entries,
        }

    # ═══════════════════════════════════════════
    # close (compat)
    # ═══════════════════════════════════════════
    def close(self) -> None:
        """🎯 no-op — برای سازگاری با context manager.

        (این کلاس فایل را باز نگه نمی‌دارد، پس چیز خاصی برای بستن نیست.)
        """

    # ═══════════════════════════════════════════
    # __enter__ / __exit__ (compat)
    # ═══════════════════════════════════════════
    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ARG002
        self.close()
