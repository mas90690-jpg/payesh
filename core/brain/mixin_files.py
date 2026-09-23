"""ابزارهای فایل — read, write, edit, list, delete, move."""
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
from core.kernel.logger import log_error, log_info, log_success, log_warning


class FilesMixin:
    """متدهای ابزار فایل — به Brain اضافه می‌شود."""

    def _safepath(self, p: str) -> Path:
        return safe_path(p, Path(PROJECT_BASE))

    def _is_safe_write_path(self, path_str: str) -> bool:
        ok, reason = get_access_manager().can_write(path_str)
        if not ok:
            log_warning(
                f"⛔ [BRAIN:safe] نوشتن ممنوع | "
                f"path={path_str} | {reason}"
            )
        return ok

    async def _read_file(self, path: str) -> str:
        ok, reason = get_access_manager().can_read(path)
        if not ok:
            log_warning(
                f"⛔ [BRAIN:file:read] ممنوع | {path} | {reason}"
            )
            return f"⛔ خواندن ممنوع: {reason}"
        p = self._safepath(path)
        if not p.exists():
            log_warning(f"⚠️ [BRAIN:file:read] نبود | {path}")
            return f"⚠️ فایل '{path}' وجود ندارد."
        size = p.stat().st_size
        if size > MAX_FILE_SIZE_BYTES:
            return (
                f"⚠️ فایل بزرگتر از "
                f"{human_bytes(MAX_FILE_SIZE_BYTES)} است."
            )
        try:
            content = p.read_text(encoding="utf-8")
            self.last_read_file = path
            log_info(
                f"📄 [BRAIN:file:read] ✅ {path} | "
                f"{len(content)}ch | {human_bytes(size)}"
            )
            if len(content) > MAX_CONTENT_LENGTH:
                return (
                    f"📄 {path} ({len(content)} کاراکتر)\n"
                    f"{content[:MAX_CONTENT_LENGTH]}\n"
                    f"... [ادامه در فایل اصلی]"
                )
            return f"📄 {path} ({len(content)} کاراکتر)\n{content}"
        except UnicodeDecodeError:
            log_warning(f"⚠️ [BRAIN:file:read] باینری | {path}")
            return f"⚠️ فایل باینری: {path} ({human_bytes(size)})"
        except Exception as e:
            log_error(
                f"❌ [BRAIN:file:read] {path} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    async def _write_file(self, path: str, content: str) -> str:
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"
        if len(content) > MAX_CONTENT_LENGTH * 5:
            return "⚠️ محتوا خیلی بزرگ است."

        p = self._safepath(path)
        backup = None
        if p.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = BACKUP_DIR / f"{p.stem}_{ts}{p.suffix}"
            try:
                shutil.copy2(p, backup)
            except Exception as e:
                log_error(
                    f"❌ [BRAIN:file:write] بکاپ ناموفق | "
                    f"{path} | {e}"
                )
                return f"❌ بکاپ ناموفق: {e}"
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            msg = f"✅ فایل '{path}' نوشته شد ({len(content)} کاراکتر)"
            if backup:
                msg += f"\n📦 بکاپ: {backup.name}"
            log_success(
                f"✅ [BRAIN:file:write] {path} | {len(content)}ch | "
                f"backup={'✅' if backup else '—'}"
            )
            return msg
        except Exception as e:
            if backup and backup.exists():
                try:
                    shutil.copy2(backup, p)
                except Exception as e2:
                    log_error(
                        f"❌ [BRAIN:file:write] rollback | "
                        f"{type(e2).__name__}: {e2}"
                    )
            log_error(
                f"❌ [BRAIN:file:write] {path} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا (بازیابی شد): {e}"

    async def _edit_file(
        self, path: str, old_text: str, new_text: str
    ) -> str:
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"
        p = self._safepath(path)
        if not p.exists():
            return f"⚠️ فایل '{path}' وجود ندارد."
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = BACKUP_DIR / f"{p.stem}_{ts}{p.suffix}"
        try:
            shutil.copy2(p, backup)
            content = p.read_text(encoding="utf-8")
            if old_text not in content:
                return "⚠️ متن قدیمی در فایل یافت نشد."
            new_content = content.replace(old_text, new_text, 1)
            p.write_text(new_content, encoding="utf-8")
            diff = "".join(
                list(
                    difflib.unified_diff(
                        content.splitlines(keepends=True),
                        new_content.splitlines(keepends=True),
                        fromfile=f"a/{path}",
                        tofile=f"b/{path}",
                        lineterm="",
                    )
                )[:30]
            )
            log_success(
                f"✏️ [BRAIN:file:edit] {path} | "
                f"Δ={len(new_content) - len(content):+d}ch | "
                f"backup={backup.name}"
            )
            return (
                f"✅ ویرایش شد.\n"
                f"📦 بکاپ: {backup.name}\n"
                f"📊 {diff[:2000]}"
            )
        except Exception as e:
            if backup.exists():
                try:
                    shutil.copy2(backup, p)
                except Exception:
                    pass
            log_error(
                f"❌ [BRAIN:file:edit] {path} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا (بازیابی شد): {e}"

    async def _list_directory(self, path: str = ".") -> str:
        p = self._safepath(path)
        if not p.exists():
            return f"⚠️ مسیر '{path}' وجود ندارد."
        items = []
        for item in sorted(p.iterdir()):
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
                continue
        log_info(
            f"📂 [BRAIN:file:list] {path} | items={len(items)}"
        )
        return "\n".join(items) or "📭 پوشه خالی است."

    async def _delete_file(self, path: str) -> str:
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"
        p = self._safepath(path)
        if not p.exists():
            return f"⚠️ فایل '{path}' وجود ندارد."
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = BACKUP_DIR / f"{p.stem}_{ts}{p.suffix}"
        try:
            shutil.copy2(p, backup)
            os.remove(p)
            log_success(
                f"🗑️ [BRAIN:file:delete] {path} | "
                f"backup={backup.name}"
            )
            return f"✅ حذف شد. 📦 بکاپ: {backup.name}"
        except Exception as e:
            log_error(
                f"❌ [BRAIN:file:delete] {path} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    async def _move_file(
        self, source: str, destination: str
    ) -> str:
        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"
        src = self._safepath(source)
        dst = self._safepath(destination)
        if not src.exists():
            return f"⚠️ '{source}' وجود ندارد."
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, dst)
            log_success(
                f"📦 [BRAIN:file:move] {source} → {destination}"
            )
            return f"✅ {source} → {destination}"
        except Exception as e:
            log_error(
                f"❌ [BRAIN:file:move] | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"
