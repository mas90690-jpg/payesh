"""ابزارهای جستجو و اطلاعات — semantic, grep, db, web, zip."""
from __future__ import annotations

import glob
import time
import zipfile
from pathlib import Path

from core.adapters.access_manager import get_access_manager
from core.brain.constants import (
    LOG_PREVIEW_LEN,
    PROJECT_BASE,
)
from core.kernel.logger import log_error, log_info, log_warning


class InfoMixin:
    """متدهای جستجو و اطلاعات — به Brain اضافه می‌شود."""

    # ═══════════════════════════════════════════
    # Semantic search
    # ═══════════════════════════════════════════
    async def _semantic_search(
        self, query: str, n_results: int = 5
    ) -> str:
        res = self.semantic_search.search(query, n_results)
        log_info(
            f"🔍 [BRAIN:sem] q={query[:LOG_PREVIEW_LEN]!r} | "
            f"hits={len(res)}"
        )
        if not res:
            return "🔍 نتیجه‌ای یافت نشد."
        lines = []
        for r in res:
            lines.append(f"📄 {r['path']}")
            lines.append(f"   {r['content'][:200]}...")
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # Grep search
    # ═══════════════════════════════════════════
    async def _grep_search(
        self, pattern: str, file_pattern: str = "*.py"
    ) -> str:
        import re

        results = []
        for fpath in glob.glob(
            f"**/{file_pattern}",
            root_dir=PROJECT_BASE,
            recursive=True,
        ):
            try:
                full = Path(PROJECT_BASE) / fpath
                with open(full, encoding="utf-8") as f:
                    for i, line in enumerate(f, 1):
                        if re.search(pattern, line, re.IGNORECASE):
                            results.append(
                                f"{fpath}:{i}: {line.strip()[:80]}"
                            )
                            if len(results) >= 30:
                                break
            except Exception:
                continue

        log_info(
            f"🔎 [BRAIN:grep] pattern={pattern[:LOG_PREVIEW_LEN]!r} | "
            f"file_pattern={file_pattern} | hits={len(results)}"
        )
        return "\n".join(results) or "🔍 چیزی یافت نشد."

    # ═══════════════════════════════════════════
    # Query database
    # ═══════════════════════════════════════════
    async def _query_database(
        self, query: str, limit: int = 50
    ) -> str:
        ok, reason = get_access_manager().can_query_db(query)
        if not ok:
            log_warning(f"⛔ [BRAIN:db] ممنوع | {reason}")
            return f"⛔ کوئری ممنوع: {reason}"

        q_upper = query.strip().upper()
        if not q_upper.startswith("SELECT"):
            return "⛔ فقط SELECT مجاز است."

        if not self.db:
            return "⚠️ دیتابیس متصل نیست."

        try:
            t0 = time.monotonic()
            rows = await self.db.execute(query)
            all_rows = await rows.fetchall()
            elapsed_ms = (time.monotonic() - t0) * 1000

            if not all_rows:
                log_info(f"📊 [BRAIN:db] {query[:60]!r} | خالی")
                return "📭 نتیجه‌ای یافت نشد."

            log_info(
                f"📊 [BRAIN:db] {query[:60]!r} | "
                f"rows={len(all_rows)} | {elapsed_ms:.0f}ms"
            )
            lines = ["📊 نتایج کوئری:"]
            for row in all_rows[:limit]:
                lines.append(" | ".join(str(c) for c in row))
            if len(all_rows) > limit:
                lines.append(f"... و {len(all_rows) - limit} نتیجه دیگر")
            return "\n".join(lines)
        except Exception as e:
            log_error(f"❌ [BRAIN:db] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════════
    # Browse web
    # ═══════════════════════════════════════════
    async def _browse_web(self, url: str) -> str:
        try:
            import requests
            from bs4 import BeautifulSoup

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                )
            }
            t0 = time.monotonic()
            r = requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(
                ["script", "style", "nav", "footer", "header", "noscript"]
            ):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_info(
                f"🌐 [BRAIN:web:browse] {url} | "
                f"{len(text)}ch | {elapsed_ms:.0f}ms"
            )
            return text[:5000] or "📄 محتوای خالی"
        except ImportError:
            return "⚠️ requests/bs4 نصب نیست."
        except Exception as e:
            log_error(
                f"❌ [BRAIN:web:browse] {url} | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════════
    # Clipboard
    # ═══════════════════════════════════════════
    async def _clipboard_get(self) -> str:
        try:
            import pyperclip

            content = pyperclip.paste()[:2000]
            log_info(f"📋 [BRAIN:clipboard] {len(content)}ch")
            return f"📋 کلیپ‌بورد:\n{content}"
        except ImportError:
            return "⚠️ pyperclip نصب نیست."
        except Exception as e:
            log_error(
                f"❌ [BRAIN:clipboard] {type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════════
    # Zip archive
    # ═══════════════════════════════════════════
    async def _zip_archive(
        self, source: str, dest: str = "", action: str = "compress"
    ) -> str:
        import shutil

        src = self._safepath(source)
        if not src.exists():
            return f"⚠️ '{source}' وجود ندارد."

        if action == "compress":
            if not dest:
                dest = f"{src.name}.zip"
            dst = self._safepath(dest)
            try:
                shutil.make_archive(
                    str(dst).replace(".zip", ""), "zip", src
                )
                log_success(f"✅ [BRAIN:zip] فشرده → {dest}")
                return f"✅ آرشیو {dest} ایجاد شد."
            except Exception as e:
                log_error(f"❌ [BRAIN:zip] {type(e).__name__}: {e}")
                return f"❌ خطا: {e}"

        if not dest:
            dest = str(src.parent / src.stem)
        dst = self._safepath(dest)
        try:
            with zipfile.ZipFile(src, "r") as z:
                z.extractall(dst)
            log_success(f"✅ [BRAIN:zip] باز → {dest}")
            return f"✅ آرشیو در {dest} باز شد."
        except Exception as e:
            log_error(f"❌ [BRAIN:zip] {type(e).__name__}: {e}")
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════════
    # Delegates
    # ═══════════════════════════════════════════
    async def _web_search(
        self, query: str, num_results: int = 5
    ) -> str:
        if self.web_search:
            return await self.web_search.search(query, num_results)
        return "❌ جستجوی وب غیرفعال است."

    async def _generate_tests(self, path: str) -> str:
        if self.test_gen:
            return await self.test_gen.generate_tests(path)
        return "❌ تولید تست غیرفعال است."

    async def _code_review(self, path: str) -> str:
        if self.reviewer:
            return await self.reviewer.review(path)
        return "❌ بازبینی کد غیرفعال است."

    async def _fix_bug(self, path: str, error: str) -> str:
        if self.healer:
            return await self.healer.fix_bug(path, error)
        return "❌ خودترمیمی غیرفعال است."

    async def _plan_task(self, task: str) -> str:
        if self.planner:
            return await self.planner.execute_plan(task)
        return "❌ برنامه‌ریزی غیرفعال است."

    # ═══════════════════════════════════════════
    # Project context
    # ═══════════════════════════════════════════
    async def _get_project_context(self) -> str:
        info = f"📂 پروژه: {PROJECT_BASE}\n"
        try:
            if self.semantic_search.collection:
                items = len(
                    self.semantic_search.collection.get().get("ids", [])
                )
                info += f"🔹 فایل‌های ایندکس‌شده: {items}\n"
                info += "🔹 حافظه معنایی: فعال\n"
            else:
                info += "🔹 حافظه معنایی: غیرفعال\n"
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:ctx] semantic | {type(e).__name__}: {e}"
            )
            info += "🔹 حافظه معنایی: خطا در خواندن\n"
        return info

    # ═══════════════════════════════════════════
    # System dashboard
    # ═══════════════════════════════════════════
    async def _system_dashboard(self) -> str:
        try:
            import psutil

            cpu = psutil.cpu_percent(interval=0.3)
            ram = psutil.virtual_memory()
            disk_path = "C:\\" if self._is_windows() else "/"
            disk = psutil.disk_usage(disk_path)
            uptime = time.time() - psutil.boot_time()
            return (
                f"📊 داشبورد:\n"
                f"CPU: {cpu}%\n"
                f"RAM: {ram.percent}%\n"
                f"دیسک: {disk.percent}%\n"
                f"آپ‌تایم: {int(uptime // 3600)}h"
            )
        except ImportError:
            return "⚠️ psutil نصب نیست."
        except Exception as e:
            log_error(
                f"❌ [BRAIN:dashboard] {type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    @staticmethod
    def _is_windows() -> bool:
        import os

        return os.name == "nt"