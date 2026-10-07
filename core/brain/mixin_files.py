"""ابزارهای فایل — read, write, edit, list, delete, move, append_knowledge.

نسخهٔ ۳ (این نسخه):
  - 🎯 NEW: `_append_knowledge` — افزودن به bot_knowledge.md
    (فقط append، هرگز overwrite، با backup خودکار).
  - 🐛 FIX امنیتی: `_write_file` حالا روی `bot_knowledge.md` قفل است
    (به‌جای بازنویسی، باید از `append_knowledge` استفاده شود).
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۲.

نسخهٔ ۲ (قبلی):
  - 🐛 FIX امنیتی مهم: `_write_file`, `_edit_file`, `_delete_file`,
    `_move_file` حالا `_is_safe_write_path` را صدا می‌زنند.
  - 🐛 FIX: `_move_file` حالا هر دو مسیر را چک می‌کند.
  - 🐛 FIX: `_write_file` limit محتوا از `_MAX_WRITE_CONTENT_LENGTH`.
  - 🐛 FIX: `_edit_file` حالا `old_text`/`new_text` خالی را رد می‌کند.
  - 🐛 FIX: `_move_file` اگر destination وجود داشت، هشدار می‌دهد.
  - 🐛 FIX: backup filename collision — با timestamp میکروثانیه.
  - 🐛 FIX: `_list_directory` حالا limit دارد (پیش‌فرض ۵۰۰).
"""
from __future__ import annotations

import difflib
import os
import shutil
from datetime import datetime
from pathlib import Path

from core.adapters.access_manager import get_access_manager
from core.brain.constants import (
    BACKUP_DIR,
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
# ثابت‌های محلی
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


# 🎯 FIX: env-based limit برای محتوای write
_MAX_WRITE_CONTENT_LENGTH = _env_int(
    "BRAIN_MAX_WRITE_CONTENT_LEN", 500_000,
    min_val=1000, max_val=50_000_000,
)

# 🎯 FIX: limit برای لیست پوشه
_MAX_DIR_LISTING = _env_int(
    "BRAIN_MAX_DIR_LISTING", 500,
    min_val=10, max_val=10_000,
)

# 🎯 سقف خطوط diff نمایش‌داده‌شده
_DIFF_LINES_PREVIEW = _env_int(
    "BRAIN_DIFF_LINES_PREVIEW", 30,
    min_val=5, max_val=500,
)
_DIFF_CHARS_PREVIEW = _env_int(
    "BRAIN_DIFF_CHARS_PREVIEW", 2000,
    min_val=200, max_val=50_000,
)

# 🎯 NEW: سقف محتوای append_knowledge
_MAX_APPEND_CONTENT_LENGTH = _env_int(
    "BRAIN_MAX_APPEND_LEN", 5000,
    min_val=100, max_val=100_000,
)

# 🎯 NEW: نام فایل‌هایی که نباید با write_file بازنویسی شوند
_LOCKED_WRITE_FILES: tuple[str, ...] = (
    "bot_knowledge.md",
    "persona.md",
    "system.md",
)


def _make_backup_path(src: Path) -> Path:
    """🎯 FIX: ساخت مسیر بکاپ با timestamp میکروثانیه (بدون collision)."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return BACKUP_DIR / f"{src.stem}_{ts}{src.suffix}"


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
        """🎯 NEW: آیا این فایل قفل است (نباید با write_file بازنویسی شود)؟

        Returns:
            نام فایل قفل‌شده اگر پیدا شد، وگرنه None.
        """
        if not path_str:
            return None
        path_lower = str(path_str).lower().replace("\\", "/")
        for locked in _LOCKED_WRITE_FILES:
            if path_lower.endswith(locked) or f"/{locked}" in path_lower:
                return locked
        return None

    # ═══════════════════════════════════════════
    # read
    # ═══════════════════════════════════════════
    async def _read_file(self, path: str) -> str:
        ok, reason = get_access_manager().can_read(path)
        if not ok:
            log_warning(
                f"⛔ [BRAIN:file:read] denied | {path} | {reason}"
            )
            return f"⛔ خواندن ممنوع: {reason}"

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
        lines_count = content.count("\n") + 1 if content else 0

        log_info(
            f"📄 [BRAIN:file:read] OK | {path} | "
            f"{len(content)}ch | {lines_count} lines | "
            f"{human_bytes(size)}"
        )

        # ─── truncate اگر خیلی بزرگ ───
        if len(content) > MAX_CONTENT_LENGTH:
            log_warning(
                f"⚠️ [BRAIN:file:read] truncated | {path} | "
                f"{len(content)} → {MAX_CONTENT_LENGTH}"
            )
            return (
                f"📄 {path} ({len(content)} کاراکتر، "
                f"{lines_count} خط)\n"
                f"{content[:MAX_CONTENT_LENGTH]}\n"
                f"... [ادامه در فایل اصلی]"
            )

        return (
            f"📄 {path} ({len(content)} کاراکتر، "
            f"{lines_count} خط)\n{content}"
        )

    # ═══════════════════════════════════════════
    # write
    # ═══════════════════════════════════════════
    async def _write_file(self, path: str, content: str) -> str:
        if self.read_only:
            log_warning(
                f"⛔ [BRAIN:file:write] read_only | {path}"
            )
            return "⛔ حالت فقط‌خواندنی"

        # 🎯 NEW: قفل فایل‌های دانشی — جلوگیری از بازنویسی
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

        # 🐛 FIX امنیتی: چک write access
        if not self._is_safe_write_path(path):
            return f"⛔ دسترسی نوشتن در '{path}' مجاز نیست."

        try:
            p = self._safepath(path)
        except Exception:
            return f"⛔ مسیر نامعتبر: {path}"

        # ─── backup اگر فایل از قبل هست ───
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

        # ─── write ───
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
            # ─── rollback ───
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

        # 🐛 FIX: چک خالی
        if not old_text:
            return "⚠️ متن قدیمی خالی است."
        if new_text is None:
            new_text = ""

        # 🐛 FIX امنیتی: چک write access
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

        # ─── backup ───
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

        # ─── read ───
        try:
            content = p.read_text(encoding="utf-8")
        except Exception as e:
            log_error(
                f"❌ [BRAIN:file:edit] read | {path} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا در خواندن: {e}"

        if old_text not in content:
            log_warning(
                f"⚠️ [BRAIN:file:edit] old_text not found | "
                f"{path}"
            )
            return (
                "⚠️ متن قدیمی در فایل یافت نشد.\n"
                "(احتمالاً فایل تغییر کرده یا متن دقیق نیست)"
            )

        # ─── replace (فقط اولین occurrence) ───
        occurrences = content.count(old_text)
        new_content = content.replace(old_text, new_text, 1)

        # ─── write ───
        try:
            p.write_text(new_content, encoding="utf-8")
        except Exception as e:
            # rollback
            try:
                shutil.copy2(backup, p)
            except Exception:
                pass
            log_error(
                f"❌ [BRAIN:file:edit] write | {path} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا (بازیابی شد): {e}"

        # ─── diff ───
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
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:file:edit] diff | {path} | "
                f"{type(e).__name__}: {e}"
            )
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
    # list directory
    # ═══════════════════════════════════════════
    async def _list_directory(self, path: str = ".") -> str:
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
            entries = sorted(p.iterdir())
        except Exception as e:
            log_error(
                f"❌ [BRAIN:file:list] iterdir | {path} | "
                f"{type(e).__name__}: {e}"
            )
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
                items.append(
                    f"{icon} {item.name:<30} {size:>12}  {mtime}"
                )
            except Exception:
                errors += 1
                continue

        log_info(
            f"📂 [BRAIN:file:list] {path} | "
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

        # 🐛 FIX امنیتی: چک write access
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

        # ─── backup ───
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

        # ─── delete ───
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

        # 🐛 FIX امنیتی: چک هر دو مسیر
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

        # 🐛 FIX: چک وجود destination
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
    # 🎯 NEW: append_knowledge — فقط اضافه، هرگز پاک نمی‌کند
    # ═══════════════════════════════════════════
    async def _append_knowledge(
        self,
        content: str,
        section: str = "یاد گرفته‌شده‌ها",
    ) -> str:
        """افزودن به bot_knowledge.md — فقط append، هرگز overwrite.

        Args:
            content: متن جدید (حداکثر ۵۰۰۰ کاراکتر).
            section: عنوان بخش (پیش‌فرض: «یاد گرفته‌شده‌ها»).

        Returns:
            پیام نتیجه.
        """
        # ─── validate ───
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

        # ─── مسیر ───
        try:
            from data import paths as data_paths
            path = data_paths.BOT_KNOWLEDGE
        except Exception as e:
            log_error(
                f"❌ [BRAIN:append_knowledge] path | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا در مسیر: {e}"

        # ─── خواندن فایل فعلی ───
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

        # ─── ضدتکرار ───
        if content_clean in existing:
            log_info(
                f"ℹ️ [BRAIN:append_knowledge] تکراری | "
                f"len={len(content_clean)}"
            )
            return "ℹ️ این محتوا از قبل در دانشنامه هست."

        # ─── ساخت بلوک جدید ───
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        section_clean = (section or "یاد گرفته‌شده‌ها").strip()
        if not section_clean:
            section_clean = "یاد گرفته‌شده‌ها"

        new_block = (
            f"\n\n## {section_clean} (اضافه‌شده: {ts})\n"
            f"\n{content_clean}\n"
        )

        # ─── backup (خودکار) ───
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            backup_path = _make_backup_path(path)
            shutil.copy2(path, backup_path) if path.exists() else None
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:append_knowledge] backup | "
                f"{type(e).__name__}: {e}"
            )
            backup_path = None

        # ─── append + fsync ───
        try:
            p = self._safepath(str(path))
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

        # 🎯 همزمان در MemoryStore هم ذخیره کن
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