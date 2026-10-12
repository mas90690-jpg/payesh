"""ابزارهای فایل — read, write, edit, list, delete, move, append_knowledge.

نسخهٔ ۴ (این نسخه):
  - 🎯 NEW: Chunk Reading — `_read_file` با پشتیبانی از chunk:
      * `_read_file(path, chunk=N)` — خواندن یک تکه
      * `_read_all(path)` — خواندن کامل بدون chunk
      * `_read_chunk(path, N)` — alias برای `_read_file`
      * `_read_lines(path, start, end)` — خواندن بازهٔ خط
      * `_read_range(path, start, end)` — خواندن بازهٔ کاراکتر
  - 🎯 NEW: File Info — `_file_info(path)` — بدون خواندن کامل
  - 🎯 NEW: File Utils:
      * `_file_exists(path)` — چک وجود
      * `_file_hash(path)` — MD5 hash
      * `_find_files(pattern, root)` — glob
  - 🎯 تقویت `_list_directory` — recursive + pattern filter
  - 🎯 استفاده از `CHUNK_SIZE`, `CHUNK_AUTO`, `CHUNK_MAX_PER_FILE`,
    `CHUNK_AUTO_THRESHOLD` از constants
  - 🎯 `_MAX_WRITE_CONTENT_LENGTH` از 500_000 به 2_000_000
  - 🎯 `_MAX_DIR_LISTING` از 500 به 1000
  - 🎯 `_MAX_APPEND_CONTENT_LENGTH` از 5000 به 10000
  - 🎯 `_LOCKED_WRITE_FILES` حالا شامل `ai_system_prompt.md` هم هست
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۳ (append_knowledge, security).
"""
from __future__ import annotations

import difflib
import hashlib
import os
import shutil
from datetime import datetime
from pathlib import Path

from core.adapters.access_manager import get_access_manager
from core.brain.constants import (
    BACKUP_DIR,
    CHUNK_AUTO,
    CHUNK_AUTO_THRESHOLD,
    CHUNK_MAX_PER_FILE,
    CHUNK_SIZE,
    MAX_CONTENT_LENGTH,
    MAX_FILE_SIZE_BYTES,
    PROJECT_BASE,
)
from core.brain.utils import human_bytes, safe_path
from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)


# ═══════════════════════════════════════════
# helpers (env-based)
# ═══════════════════════════════════════════
def _env_int(
    name: str, default: int,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    """خواندن int از env — با sanitize."""
    try:
        v = int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


# 🎯 سقف محتوای write
_MAX_WRITE_CONTENT_LENGTH = _env_int(
    "BRAIN_MAX_WRITE_CONTENT_LEN", 2_000_000,
    min_val=1000, max_val=50_000_000,
)

# 🎯 سقف لیست پوشه
_MAX_DIR_LISTING = _env_int(
    "BRAIN_MAX_DIR_LISTING", 1000,
    min_val=10, max_val=100_000,
)

# 🎯 سقف خطوط diff
_DIFF_LINES_PREVIEW = _env_int(
    "BRAIN_DIFF_LINES_PREVIEW", 50,
    min_val=5, max_val=500,
)
_DIFF_CHARS_PREVIEW = _env_int(
    "BRAIN_DIFF_CHARS_PREVIEW", 3000,
    min_val=200, max_val=50_000,
)

# 🎯 سقف محتوای append_knowledge
_MAX_APPEND_CONTENT_LENGTH = _env_int(
    "BRAIN_MAX_APPEND_LEN", 10_000,
    min_val=100, max_val=100_000,
)

# 🎯 آستانه‌ای که بالاتر از آن، شماره خط اضافه نمی‌شود
_MAX_LINES_FOR_NUMBERING = _env_int(
    "BRAIN_MAX_LINES_NUMBERED", 2000,
    min_val=100, max_val=100_000,
)

# 🎯 فایل‌های قفل‌شده
_LOCKED_WRITE_FILES: tuple[str, ...] = (
    "bot_knowledge.md",
    "persona.md",
    "system.md",
    "ai_system_prompt.md",
)


def _make_backup_path(src: Path) -> Path:
    """ساخت مسیر بکاپ با timestamp میکروثانیه."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return BACKUP_DIR / f"{src.stem}_{ts}{src.suffix}"


# ═══════════════════════════════════════════
# FilesMixin
# ═══════════════════════════════════════════
class FilesMixin:
    """متدهای ابزار فایل — به Brain اضافه می‌شود."""

    # ═══════════════════════════════════════════
    # helpers
    # ═══════════════════════════════════════════
    def _safepath(self, p: str) -> Path:
        return safe_path(p, Path(PROJECT_BASE))

    def _is_safe_write_path(self, path_str: str) -> bool:
        ok, reason = get_access_manager().can_write(path_str)
        if not ok:
            log_warning(
                f"⛔ [BRAIN:file:safe] write denied | "
                f"path={path_str} | {reason}"
            )
        return ok

    @staticmethod
    def _is_locked_write_file(path_str: str) -> str | None:
        """آیا این فایل قفل است (نباید با write_file بازنویسی شود)؟"""
        if not path_str:
            return None
        path_lower = str(path_str).lower().replace("\\", "/")
        for locked in _LOCKED_WRITE_FILES:
            if path_lower.endswith(locked) or f"/{locked}" in path_lower:
                return locked
        return None

    @staticmethod
    def _format_lines(
        content: str, start_line: int = 1
    ) -> str:
        """فرمت خطوط با شماره."""
        lines = content.split("\n")
        out: list[str] = []
        for i, line in enumerate(lines, start=start_line):
            out.append(f"{i:5d} │ {line}")
        return "\n".join(out)

    # ═══════════════════════════════════════════
    # read (chunk-aware)
    # ═══════════════════════════════════════════
    async def _read_file(
        self,
        path: str,
        chunk: int = 0,
        chunk_size: int | None = None,
    ) -> str:
        """خواندن یک فایل — با پشتیبانی از chunk.

        Args:
            path: مسیر فایل
            chunk: شمارهٔ تکه (0 = اول)
            chunk_size: اندازهٔ هر تکه (پیش‌فرض از constants)

        Returns:
            محتوای فایل یا پیام خطا.
        """
        # ─── گارد خواندن ───
        ok, reason = get_access_manager().can_read(path)
        if not ok:
            log_warning(
                f"⛔ [BRAIN:file:read] denied | {path} | {reason}"
            )
            return f"⛔ خواندن ممنوع: {reason}"

        # ─── مسیر ───
        try:
            p = self._safepath(path)
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:file:read] safepath | {path} | "
                f"{type(e).__name__}: {e}"
            )
            return f"⛔ مسیر نامعتبر: {path}"

        if not p.exists():
            log_warning(f"⚠️ [BRAIN:file:read] not found | {path}")
            return f"⚠️ فایل '{path}' وجود ندارد."

        if not p.is_file():
            log_warning(
                f"⚠️ [BRAIN:file:read] not a file | {path}"
            )
            return f"⚠️ '{path}' یک فایل نیست."

        # ─── اندازه ───
        try:
            size = p.stat().st_size
        except OSError as e:
            log_warning(
                f"⚠️ [BRAIN:file:read] stat | {path} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا در خواندن اطلاعات فایل: {e}"

        if size > MAX_FILE_SIZE_BYTES:
            log_warning(
                f"⚠️ [BRAIN:file:read] too large | {path} | "
                f"{human_bytes(size)}"
            )
            return (
                f"⚠️ فایل بزرگتر از "
                f"{human_bytes(MAX_FILE_SIZE_BYTES)} است "
                f"({human_bytes(size)})."
            )

        # ─── خواندن ───
        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            log_warning(
                f"⚠️ [BRAIN:file:read] binary | {path} | "
                f"{human_bytes(size)}"
            )
            return f"⚠️ فایل باینری: {path} ({human_bytes(size)})"
        except Exception as e:
            log_error(
                f"❌ [BRAIN:file:read] {path} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

        self.last_read_file = path
        total_chars = len(content)
        lines_count = content.count("\n") + 1

        # ═══════════════════════════════════════════
        # تصمیم: chunk یا یک‌جا؟
        # ═══════════════════════════════════════════
        use_chunk = (
            CHUNK_AUTO
            and total_chars > CHUNK_AUTO_THRESHOLD
        )

        # ─── حالت ساده (بدون chunk) ───
        if not use_chunk:
            log_info(
                f"📄 [BRAIN:file:read] کامل | {path} | "
                f"{total_chars}ch | {lines_count} lines | "
                f"{human_bytes(size)}"
            )

            # فقط اگر خطوط زیاد نیستند، شماره بگذار
            if lines_count <= _MAX_LINES_FOR_NUMBERING:
                body = self._format_lines(content)
            else:
                body = content

            return (
                f"📄 {path} ({total_chars} کاراکتر، "
                f"{lines_count} خط، {human_bytes(size)})\n"
                f"{'─' * 60}\n{body}"
            )

        # ═══════════════════════════════════════════
        # حالت chunk
        # ═══════════════════════════════════════════
        cs = chunk_size or CHUNK_SIZE
        total_chunks = (total_chars + cs - 1) // cs

        # چک سقف
        if total_chunks > CHUNK_MAX_PER_FILE:
            log_warning(
                f"⚠️ [BRAIN:file:read] chunks={total_chunks} > "
                f"max={CHUNK_MAX_PER_FILE} — limit"
            )
            total_chunks = CHUNK_MAX_PER_FILE

        # چک chunk معتبر
        if chunk < 0 or chunk >= total_chunks:
            log_warning(
                f"⚠️ [BRAIN:file:read] chunk={chunk} invalid | "
                f"valid: 0..{total_chunks - 1}"
            )
            return (
                f"⛔ تکه {chunk} وجود ندارد. "
                f"کل تکه‌ها: {total_chunks} "
                f"(0..{total_chunks - 1})"
            )

        start = chunk * cs
        end = min(start + cs, total_chars)
        piece = content[start:end]

        # شماره خطوط این تکه
        line_start = content[:start].count("\n") + 1
        line_end = content[:end].count("\n") + 1

        # ─── header ───
        header = (
            f"📄 {path} — تکه {chunk + 1}/{total_chunks}\n"
            f"   کاراکتر {start}-{end} از {total_chars}\n"
            f"   خط {line_start}-{line_end} از {lines_count}\n"
            f"   اندازه: {human_bytes(size)}"
        )

        # ─── body ───
        body = self._format_lines(
            piece, start_line=line_start
        )

        # ─── navigation ───
        nav: list[str] = []
        if chunk > 0:
            nav.append(
                f"⬅️ قبلی: read_file(\"{path}\", chunk={chunk - 1})"
            )
        if chunk + 1 < total_chunks:
            nav.append(
                f"➡️ بعدی: read_file(\"{path}\", chunk={chunk + 1})"
            )
        nav.append(
            f"💡 کامل: read_all(\"{path}\")"
        )
        nav_text = "\n".join(nav)

        log_info(
            f"📄 [BRAIN:file:read] chunk={chunk + 1}/{total_chunks} | "
            f"{path} | chars={start}-{end} | "
            f"lines={line_start}-{line_end}"
        )

        return (
            f"{header}\n"
            f"{'═' * 60}\n"
            f"{body}\n"
            f"{'═' * 60}\n"
            f"{nav_text}"
        )

    # ═══════════════════════════════════════════
    # read_all
    # ═══════════════════════════════════════════
    async def _read_all(
        self, path: str, max_chars: int | None = None
    ) -> str:
        """خواندن کامل فایل — بدون chunk، تا سقف مشخص.

        Args:
            path: مسیر فایل
            max_chars: سقف کاراکتر

        Returns:
            محتوای کامل فایل.
        """
        ok, reason = get_access_manager().can_read(path)
        if not ok:
            return f"⛔ خواندن ممنوع: {reason}"

        try:
            p = self._safepath(path)
        except Exception:
            return f"⛔ مسیر نامعتبر: {path}"

        if not p.exists():
            return f"⚠️ فایل '{path}' وجود ندارد."

        if not p.is_file():
            return f"⚠️ '{path}' یک فایل نیست."

        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"⚠️ فایل باینری: {path}"
        except Exception as e:
            return f"❌ خطا: {e}"

        limit = max_chars or MAX_CONTENT_LENGTH
        total = len(content)

        if total > limit:
            log_warning(
                f"⚠️ [BRAIN:file:read_all] {path} | "
                f"{total} > {limit} — truncated"
            )
            content = (
                content[:limit]
                + f"\n\n... [بریده شد. کل: {total} کاراکتر]"
            )

        return content

    # ═══════════════════════════════════════════
    # read_chunk (alias)
    # ═══════════════════════════════════════════
    async def _read_chunk(
        self, path: str, chunk: int
    ) -> str:
        """خواندن یک تکهٔ مشخص از فایل.

        این متد فقط alias است برای `_read_file`.
        """
        return await self._read_file(path, chunk=chunk)

    # ═══════════════════════════════════════════
    # read_lines (بازهٔ خط)
    # ═══════════════════════════════════════════
    async def _read_lines(
        self,
        path: str,
        start_line: int = 1,
        end_line: int = 100,
    ) -> str:
        """خواندن بازهٔ مشخص از خطوط.

        Args:
            path: مسیر فایل
            start_line: خط شروع (1-based)
            end_line: خط پایان

        Returns:
            محتوای خطوط با شماره.
        """
        ok, reason = get_access_manager().can_read(path)
        if not ok:
            return f"⛔ خواندن ممنوع: {reason}"

        try:
            p = self._safepath(path)
        except Exception:
            return f"⛔ مسیر نامعتبر: {path}"

        if not p.exists():
            return f"⚠️ فایل '{path}' وجود ندارد."

        try:
            content = p.read_text(encoding="utf-8")
        except Exception as e:
            return f"❌ خطا: {e}"

        lines = content.split("\n")
        total = len(lines)

        start_line = max(1, start_line)
        end_line = min(end_line, total)

        if start_line > total:
            return (
                f"⚠️ start_line={start_line} > total={total}"
            )

        selected = lines[start_line - 1:end_line]
        body = "\n".join(
            f"{i:5d} │ {line}"
            for i, line in enumerate(
                selected, start=start_line
            )
        )

        return (
            f"📄 {path} — خطوط {start_line}-{end_line} "
            f"از {total}\n"
            f"{'─' * 60}\n{body}"
        )

    # ═══════════════════════════════════════════
    # read_range (بازهٔ کاراکتر)
    # ═══════════════════════════════════════════
    async def _read_range(
        self,
        path: str,
        start_char: int = 0,
        end_char: int = 100_000,
    ) -> str:
        """خواندن بازهٔ مشخص از کاراکترها.

        Args:
            path: مسیر فایل
            start_char: شروع (0-based)
            end_char: پایان
        """
        ok, reason = get_access_manager().can_read(path)
        if not ok:
            return f"⛔ خواندن ممنوع: {reason}"

        try:
            p = self._safepath(path)
        except Exception:
            return f"⛔ مسیر نامعتبر: {path}"

        if not p.exists():
            return f"⚠️ فایل '{path}' وجود ندارد."

        try:
            content = p.read_text(encoding="utf-8")
        except Exception as e:
            return f"❌ خطا: {e}"

        total = len(content)
        start_char = max(0, start_char)
        end_char = min(end_char, total)

        if start_char >= total:
            return (
                f"⚠️ start_char={start_char} > total={total}"
            )

        piece = content[start_char:end_char]

        return (
            f"📄 {path} — کاراکتر {start_char}-{end_char} "
            f"از {total}\n"
            f"{'─' * 60}\n{piece}"
        )

    # ═══════════════════════════════════════════
    # file_info
    # ═══════════════════════════════════════════
    async def _file_info(self, path: str) -> str:
        """اطلاعات فایل بدون خواندن کامل.

        Returns:
            حجم، خطوط، mtime، مسیر.
        """
        ok, reason = get_access_manager().can_read(path)
        if not ok:
            return f"⛔ خواندن ممنوع: {reason}"

        try:
            p = self._safepath(path)
        except Exception:
            return f"⛔ مسیر نامعتبر: {path}"

        if not p.exists():
            return f"⚠️ فایل '{path}' وجود ندارد."

        if not p.is_file():
            return f"⚠️ '{path}' یک فایل نیست."

        try:
            st = p.stat()
            size = st.st_size
            mtime = datetime.fromtimestamp(st.st_mtime)
            mtime_str = mtime.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            return f"❌ خطا در خواندن اطلاعات: {e}"

        # شمارش خطوط بدون خواندن کامل
        line_count = 0
        try:
            with open(p, "rb") as f:
                for _ in f:
                    line_count += 1
        except Exception:
            line_count = -1

        return (
            f"📊 اطلاعات فایل:\n"
            f"  📁 مسیر: {path}\n"
            f"  📏 حجم: {human_bytes(size)}\n"
            f"  📄 خطوط: {line_count}\n"
            f"  🕒 آخرین تغییر: {mtime_str}\n"
            f"  🔗 absolute: {p}"
        )

    # ═══════════════════════════════════════════
    # file_exists
    # ═══════════════════════════════════════════
    async def _file_exists(self, path: str) -> str:
        """چک وجود فایل یا پوشه."""
        try:
            p = self._safepath(path)
            if p.exists():
                kind = "📄 فایل" if p.is_file() else "📁 پوشه"
                return f"✅ {kind} وجود دارد: {path}"
            return f"❌ وجود ندارد: {path}"
        except Exception as e:
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════════
    # file_hash
    # ═══════════════════════════════════════════
    async def _file_hash(self, path: str) -> str:
        """محاسبهٔ MD5 hash یک فایل."""
        ok, reason = get_access_manager().can_read(path)
        if not ok:
            return f"⛔ خواندن ممنوع: {reason}"

        try:
            p = self._safepath(path)
        except Exception:
            return f"⛔ مسیر نامعتبر: {path}"

        if not p.exists():
            return f"⚠️ فایل '{path}' وجود ندارد."

        try:
            h = hashlib.md5(usedforsecurity=False)
            with open(p, "rb") as f:
                for chunk in iter(
                    lambda: f.read(8192), b""
                ):
                    h.update(chunk)
            return (
                f"🔐 {path}\n"
                f"   MD5: {h.hexdigest()}"
            )
        except Exception as e:
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════════
    # find_files
    # ═══════════════════════════════════════════
    async def _find_files(
        self,
        pattern: str = "*.py",
        root: str = ".",
        max_results: int = 100,
    ) -> str:
        """جستجوی فایل با glob pattern.

        Args:
            pattern: مثل `*.py`، `**/*.json`
            root: پوشهٔ شروع
            max_results: حداکثر نتیجه
        """
        try:
            base = self._safepath(root)
        except Exception:
            return f"⛔ مسیر نامعتبر: {root}"

        if not base.exists():
            return f"⚠️ '{root}' وجود ندارد."

        try:
            import glob
            matches = glob.glob(
                pattern,
                root_dir=str(base),
                recursive=True,
            )
        except Exception as e:
            return f"❌ خطا در glob: {e}"

        if not matches:
            return f"🔍 نتیجه‌ای برای «{pattern}» نیست."

        matches = sorted(matches)[:max_results]

        lines = [f"🔍 {len(matches)} نتیجه:"]
        for m in matches:
            full = base / m
            try:
                size = full.stat().st_size
                lines.append(
                    f"  • {m} ({human_bytes(size)})"
                )
            except Exception:
                lines.append(f"  • {m}")

        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # write
    # ═══════════════════════════════════════════
    async def _write_file(self, path: str, content: str) -> str:
        if self.read_only:
            log_warning(
                f"⛔ [BRAIN:file:write] read_only | {path}"
            )
            return "⛔ حالت فقط‌خواندنی"

        # قفل فایل‌های دانشی
        locked = self._is_locked_write_file(path)
        if locked:
            log_warning(
                f"⛔ [BRAIN:file:write] locked | {path} | "
                f"file={locked}"
            )
            return (
                f"⛔ فایل «{locked}» قفل است — نباید با "
                f"`write_file` بازنویسی شود.\n"
                f"✅ برای افزودن محتوا از `append_knowledge` "
                f"استفاده کن."
            )

        if not content:
            return "⚠️ محتوا خالی است."

        if len(content) > _MAX_WRITE_CONTENT_LENGTH:
            log_warning(
                f"⚠️ [BRAIN:file:write] too large | {path} | "
                f"{len(content)} > {_MAX_WRITE_CONTENT_LENGTH}"
            )
            return (
                f"⚠️ محتوا خیلی بزرگ است "
                f"({len(content)} > {_MAX_WRITE_CONTENT_LENGTH})."
            )

        if not self._is_safe_write_path(path):
            return f"⛔ دسترسی نوشتن در '{path}' مجاز نیست."

        try:
            p = self._safepath(path)
        except Exception:
            return f"⛔ مسیر نامعتبر: {path}"

        # backup
        backup: Path | None = None
        if p.exists():
            backup = _make_backup_path(p)
            try:
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, backup)
            except Exception as e:
                log_error(
                    f"❌ [BRAIN:file:write] backup | {path} | "
                    f"{type(e).__name__}: {e}"
                )
                return f"❌ بکاپ ناموفق: {e}"

        # write
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            lines_count = content.count("\n") + 1

            msg = (
                f"✅ فایل '{path}' نوشته شد "
                f"({len(content)} کاراکتر، {lines_count} خط)"
            )
            if backup:
                msg += f"\n📦 بکاپ: {backup.name}"

            log_success(
                f"✅ [BRAIN:file:write] OK | {path} | "
                f"{len(content)}ch | {lines_count} lines | "
                f"backup={'✅' if backup else '—'}"
            )
            return msg

        except Exception as e:
            # rollback
            if backup and backup.exists():
                try:
                    shutil.copy2(backup, p)
                    log_info(
                        f"♻️ [BRAIN:file:write] rollback OK | "
                        f"{path}"
                    )
                except Exception as e2:
                    log_error(
                        f"❌ [BRAIN:file:write] rollback FAILED | "
                        f"{path} | {type(e2).__name__}: {e2}"
                    )
                    return (
                        f"❌ خطا در نوشتن و rollback ناموفق: {e} "
                        f"(rollback: {e2})"
                    )

            log_error(
                f"❌ [BRAIN:file:write] FAILED | {path} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا (بازیابی شد): {e}"

    # ═══════════════════════════════════════════
    # edit
    # ═══════════════════════════════════════════
    async def _edit_file(
        self, path: str, old_text: str, new_text: str
    ) -> str:
        if self.read_only:
            log_warning(
                f"⛔ [BRAIN:file:edit] read_only | {path}"
            )
            return "⛔ حالت فقط‌خواندنی"

        if not old_text:
            return "⚠️ متن قدیمی خالی است."
        if new_text is None:
            new_text = ""

        if not self._is_safe_write_path(path):
            return f"⛔ دسترسی نوشتن در '{path}' مجاز نیست."

        try:
            p = self._safepath(path)
        except Exception:
            return f"⛔ مسیر نامعتبر: {path}"

        if not p.exists():
            return f"⚠️ فایل '{path}' وجود ندارد."

        if not p.is_file():
            return f"⚠️ '{path}' یک فایل نیست."

        # backup
        backup = _make_backup_path(p)
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, backup)
        except Exception as e:
            log_error(
                f"❌ [BRAIN:file:edit] backup | {path} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ بکاپ ناموفق: {e}"

        # read
        try:
            content = p.read_text(encoding="utf-8")
        except Exception as e:
            return f"❌ خطا در خواندن: {e}"

        if old_text not in content:
            return (
                "⚠️ متن قدیمی در فایل یافت نشد.\n"
                "(احتمالاً فایل تغییر کرده یا متن دقیق نیست)"
            )

        occurrences = content.count(old_text)
        new_content = content.replace(old_text, new_text, 1)

        try:
            p.write_text(new_content, encoding="utf-8")
        except Exception as e:
            try:
                shutil.copy2(backup, p)
            except Exception:
                pass
            return f"❌ خطا (بازیابی شد): {e}"

        # diff
        try:
            diff_lines = list(
                difflib.unified_diff(
                    content.splitlines(keepends=True),
                    new_content.splitlines(keepends=True),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                    lineterm="",
                )
            )[:_DIFF_LINES_PREVIEW]
            diff = "".join(diff_lines)
            added = sum(
                1 for ln in diff_lines
                if ln.startswith("+") and not ln.startswith("+++")
            )
            removed = sum(
                1 for ln in diff_lines
                if ln.startswith("-") and not ln.startswith("---")
            )
        except Exception:
            diff = ""
            added = removed = 0

        log_success(
            f"✏️ [BRAIN:file:edit] OK | {path} | "
            f"Δch={len(new_content) - len(content):+d} | "
            f"+{added}/-{removed} lines | "
            f"occurrences={occurrences} | "
            f"backup={backup.name}"
        )

        msg = (
            f"✅ ویرایش شد.\n"
            f"📦 بکاپ: {backup.name}\n"
            f"📊 +{added}/-{removed} خط"
        )
        if occurrences > 1:
            msg += (
                f"\nℹ️ توجه: «متن قدیمی» {occurrences} بار در فایل "
                f"بود — فقط اولین مورد جایگزین شد."
            )
        if diff:
            msg += f"\n\n📄 Diff:\n{diff[:_DIFF_CHARS_PREVIEW]}"
        return msg

    # ═══════════════════════════════════════════
    # list directory (تقویت‌شده)
    # ═══════════════════════════════════════════
    async def _list_directory(
        self,
        path: str = ".",
        recursive: bool = False,
        pattern: str = "",
    ) -> str:
        """لیست محتویات پوشه.

        Args:
            path: مسیر پوشه
            recursive: اگر True، زیرپوشه‌ها هم اسکن
            pattern: فیلتر glob (مثل `*.py`)
        """
        try:
            p = self._safepath(path)
        except Exception:
            return f"⛔ مسیر نامعتبر: {path}"

        if not p.exists():
            return f"⚠️ مسیر '{path}' وجود ندارد."

        if not p.is_dir():
            return f"⚠️ '{path}' یک پوشه نیست."

        items: list[str] = []
        errors = 0
        total = 0

        try:
            if recursive:
                entries: list[Path] = []
                for root, dirs, files in os.walk(p):
                    # فیلتر پوشه‌های مخفی/سنگین
                    dirs[:] = [
                        d for d in dirs
                        if not d.startswith(".")
                        and d not in (
                            "__pycache__", "venv",
                            ".venv", "node_modules",
                        )
                    ]
                    for name in files:
                        if pattern:
                            import fnmatch
                            if not fnmatch.fnmatch(
                                name, pattern
                            ):
                                continue
                        entries.append(
                            Path(root) / name
                        )
                entries = sorted(entries)
            else:
                entries = sorted(p.iterdir())
        except Exception as e:
            return f"❌ خطا: {e}"

        for item in entries:
            total += 1
            if len(items) >= _MAX_DIR_LISTING:
                break
            try:
                st = item.stat()
                icon = "📁" if item.is_dir() else "📄"
                size = (
                    "<DIR>"
                    if item.is_dir()
                    else human_bytes(st.st_size)
                )
                mtime = datetime.fromtimestamp(
                    st.st_mtime
                ).strftime("%Y-%m-%d %H:%M")
                # مسیر نسبی
                try:
                    rel = item.relative_to(p)
                except Exception:
                    rel = item.name
                items.append(
                    f"{icon} {str(rel):<40} "
                    f"{size:>12}  {mtime}"
                )
            except Exception:
                errors += 1
                continue

        log_info(
            f"📂 [BRAIN:file:list] {path} | "
            f"recursive={recursive} pattern={pattern!r} | "
            f"shown={len(items)} total={total} errors={errors}"
        )

        result = "\n".join(items) if items else "📭 پوشه خالی است."
        if total > len(items):
            result += (
                f"\n\n... و {total - len(items)} مورد دیگر "
                f"(limit: {_MAX_DIR_LISTING})"
            )
        return result

    # ═══════════════════════════════════════════
    # delete
    # ═══════════════════════════════════════════
    async def _delete_file(self, path: str) -> str:
        if self.read_only:
            log_warning(
                f"⛔ [BRAIN:file:delete] read_only | {path}"
            )
            return "⛔ حالت فقط‌خواندنی"

        if not self._is_safe_write_path(path):
            return f"⛔ دسترسی حذف در '{path}' مجاز نیست."

        try:
            p = self._safepath(path)
        except Exception:
            return f"⛔ مسیر نامعتبر: {path}"

        if not p.exists():
            return f"⚠️ فایل '{path}' وجود ندارد."

        if not p.is_file():
            return f"⚠️ '{path}' یک فایل نیست."

        # backup
        backup = _make_backup_path(p)
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, backup)
        except Exception as e:
            log_error(
                f"❌ [BRAIN:file:delete] backup | {path} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ بکاپ ناموفق: {e}"

        # delete
        try:
            os.remove(p)
            log_success(
                f"🗑️ [BRAIN:file:delete] OK | {path} | "
                f"backup={backup.name}"
            )
            return f"✅ حذف شد. 📦 بکاپ: {backup.name}"

        except Exception as e:
            log_error(
                f"❌ [BRAIN:file:delete] {path} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════════
    # move
    # ═══════════════════════════════════════════
    async def _move_file(
        self, source: str, destination: str
    ) -> str:
        if self.read_only:
            log_warning(
                f"⛔ [BRAIN:file:move] read_only | "
                f"{source} → {destination}"
            )
            return "⛔ حالت فقط‌خواندنی"

        if not self._is_safe_write_path(source):
            return (
                f"⛔ دسترسی به مسیر مبدأ '{source}' مجاز نیست."
            )
        if not self._is_safe_write_path(destination):
            return (
                f"⛔ دسترسی به مسیر مقصد '{destination}' مجاز نیست."
            )

        try:
            src = self._safepath(source)
            dst = self._safepath(destination)
        except Exception as e:
            return f"⛔ مسیر نامعتبر: {e}"

        if not src.exists():
            return f"⚠️ '{source}' وجود ندارد."

        if dst.exists():
            log_warning(
                f"⚠️ [BRAIN:file:move] destination exists | "
                f"{destination}"
            )
            return (
                f"⛔ '{destination}' از قبل وجود دارد. "
                f"ابتدا حذفش کن یا نام دیگری بده."
            )

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            log_success(
                f"📦 [BRAIN:file:move] OK | "
                f"{source} → {destination}"
            )
            return f"✅ {source} → {destination}"

        except Exception as e:
            log_error(
                f"❌ [BRAIN:file:move] {source} → {destination} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════════
    # append_knowledge — فقط اضافه، هرگز پاک نمی‌کند
    # ═══════════════════════════════════════════
    async def _append_knowledge(
        self,
        content: str,
        section: str = "یاد گرفته‌شده‌ها",
    ) -> str:
        """افزودن به bot_knowledge.md — فقط append، هرگز overwrite.

        Args:
            content: متن جدید (حداکثر ۱۰۰۰۰ کاراکتر).
            section: عنوان بخش.

        Returns:
            پیام نتیجه.
        """
        # validate
        if not content or not str(content).strip():
            return "⚠️ محتوا خالی است."

        content_clean = str(content).strip()

        if len(content_clean) > _MAX_APPEND_CONTENT_LENGTH:
            return (
                f"⚠️ محتوا خیلی بزرگ است "
                f"(حداکثر {_MAX_APPEND_CONTENT_LENGTH} کاراکتر)."
            )

        if self.read_only:
            return "⛔ حالت فقط‌خواندنی."

        # مسیر
        try:
            from data import paths as data_paths
            path = data_paths.BOT_KNOWLEDGE
        except Exception as e:
            log_error(
                f"❌ [BRAIN:append_knowledge] path | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا در مسیر: {e}"

        # خواندن فایل فعلی
        try:
            if path.exists():
                existing = path.read_text(encoding="utf-8")
            else:
                existing = "# دانشِ ربات — WinDoctor\n"
        except Exception as e:
            log_error(
                f"❌ [BRAIN:append_knowledge] read | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا در خواندن: {e}"

        # ضدتکرار
        if content_clean in existing:
            log_info(
                f"ℹ️ [BRAIN:append_knowledge] تکراری | "
                f"len={len(content_clean)}"
            )
            return "ℹ️ این محتوا از قبل در دانشنامه هست."

        # ساخت بلوک جدید
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        section_clean = (section or "یاد گرفته‌شده‌ها").strip()
        if not section_clean:
            section_clean = "یاد گرفته‌شده‌ها"

        new_block = (
            f"\n\n## {section_clean} (اضافه‌شده: {ts})\n"
            f"\n{content_clean}\n"
        )

        # backup
        backup_path: Path | None = None
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            if path.exists():
                backup_path = _make_backup_path(path)
                shutil.copy2(path, backup_path)
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:append_knowledge] backup | "
                f"{type(e).__name__}: {e}"
            )
            backup_path = None

        # append + fsync
        try:
            p = self._safepath(str(path))
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(str(p), "a", encoding="utf-8") as f:
                f.write(new_block)
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except (AttributeError, OSError):
                    pass
        except Exception as e:
            log_error(
                f"❌ [BRAIN:append_knowledge] write | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا در نوشتن: {e}"

        log_success(
            f"📚 [BRAIN:append_knowledge] OK | "
            f"+{len(content_clean)}ch | "
            f"section={section_clean!r} | "
            f"backup={'✅' if backup_path else '—'}"
        )

        # همزمان در MemoryStore
        try:
            from core.adapters.memory_store import MemoryStore
            store = MemoryStore()
            store.add(
                category="knowledge",
                key=f"{section_clean}: {content_clean[:100]}",
                value=content_clean,
                importance=0.9,
            )
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:append] memory_store | {e}")

        msg = (
            f"✅ به دانشنامه اضافه شد.\n"
            f"📏 {len(content_clean)} کاراکتر\n"
            f"📂 بخش: «{section_clean}»"
        )
        if backup_path:
            msg += f"\n📦 بکاپ: {backup_path.name}"
        return msg