"""ابزارهای جستجو و اطلاعات — semantic, grep, db, web, zip.

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX امنیتی مهم: `_zip_archive` حالا zip-slip check دارد
    (جلوگیری از extract خارج از پوشهٔ مقصد).
  - 🐛 FIX امنیتی مهم: `_zip_archive` حالا `_is_safe_write_path` را
    برای dest چک می‌کند.
  - 🐛 FIX مهم: `_browse_web` حالا از `asyncio.to_thread` استفاده
    می‌کند (قبلاً `requests.get` sync بود و event loop را بلاک
    می‌کرد).
  - 🐛 FIX: `_system_dashboard` حالا `psutil.cpu_percent` را با
    `asyncio.to_thread` صدا می‌زند (قبلاً ۳۰۰ms بلاک می‌کرد).
  - 🐛 FIX: `_grep_search` حالا size limit دارد (جلوگیری از
    memory bloat) و regex نامعتبر را catch می‌کند.
  - 🐛 FIX: `_semantic_search` حالا query خالی را رد می‌کند.
  - 🐛 FIX: `_browse_web` حالا URL validation ساده دارد.
  - 🎯 `_is_windows()` حالا `lru_cache` دارد.
  - 🎯 env-based: `BRAIN_GREP_MAX_RESULTS`, `BRAIN_GREP_MAX_FILE_KB`,
    `BRAIN_WEB_TIMEOUT_S`, `BRAIN_WEB_MAX_CONTENT`, `BRAIN_CLIPBOARD_MAX_LEN`.
  - 🎯 لاگ‌های دقیق‌تر با prefixهای یکدست.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۱.
"""
from __future__ import annotations

import asyncio
import glob
import os
import re
import time
import zipfile
from functools import lru_cache
from pathlib import Path

from core.adapters.access_manager import get_access_manager
from core.brain.constants import (
    LOG_PREVIEW_LEN,
    PROJECT_BASE,
)
from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)


# ═══════════════════════════════════════════
# helpers
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


def _env_float(
    name: str, default: float,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    """خواندن float از env — با sanitize."""
    try:
        v = float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


# ═══════════════════════════════════════════
# آستانه‌ها (env-based)
# ═══════════════════════════════════════════
_GREP_MAX_RESULTS = _env_int(
    "BRAIN_GREP_MAX_RESULTS", 30, min_val=1, max_val=1000
)
_GREP_MAX_FILE_KB = _env_int(
    "BRAIN_GREP_MAX_FILE_KB", 500, min_val=10, max_val=100_000
)
_GREP_MAX_MATCH_LEN = _env_int(
    "BRAIN_GREP_MAX_MATCH_LEN", 120, min_val=20, max_val=2000
)
_WEB_TIMEOUT_S = _env_float(
    "BRAIN_WEB_TIMEOUT_S", 15.0, min_val=1.0, max_val=120.0
)
_WEB_MAX_CONTENT = _env_int(
    "BRAIN_WEB_MAX_CONTENT", 5000,
    min_val=500, max_val=1_000_000,
)
_CLIPBOARD_MAX_LEN = _env_int(
    "BRAIN_CLIPBOARD_MAX_LEN", 2000,
    min_val=100, max_val=100_000,
)


# ═══════════════════════════════════════════
# InfoMixin
# ═══════════════════════════════════════════
class InfoMixin:
    """متدهای جستجو و اطلاعات — به Brain اضافه می‌شود."""

    # ═══════════════════════════════════════════
    # Semantic search
    # ═══════════════════════════════════════════
    async def _semantic_search(
        self, query: str, n_results: int = 5
    ) -> str:
        if not query or not query.strip():
            return "⚠️ query خالی است."

        if not getattr(self, "semantic_search", None):
            return "❌ semantic_search در دسترس نیست."

        if not self.semantic_search.collection:
            return "❌ حافظهٔ معنایی غیرفعال است."

        # 🎯 fallback به MemoryStore جدید
        if not self.semantic_search.collection:
            try:
                from core.adapters.memory_store import MemoryStore
                store = MemoryStore()
                results = store.query(
                    category="knowledge",
                    query=query,
                    limit=n_results,
                )
                if results:
                    lines = []
                    for r in results:
                        lines.append(f"📄 {r.get('key', '?')}")
                        lines.append(f"   {r.get('value', '')[:200]}...")
                    return "\n".join(lines)
            except Exception as e:
                log_warning(f"⚠️ [BRAIN:sem:fallback] {e}")
            return "❌ حافظهٔ معنایی غیرفعال است."

        try:
            t0 = time.monotonic()
            res = self.semantic_search.search(query, n_results)
            elapsed_ms = (time.monotonic() - t0) * 1000
        except Exception as e:
            log_error(
                f"❌ [BRAIN:sem:search] "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا در جستجوی معنایی: {e}"

        log_info(
            f"🔍 [BRAIN:sem:search] "
            f"q={query[:LOG_PREVIEW_LEN]!r} | "
            f"hits={len(res)} | {elapsed_ms:.0f}ms"
        )

        if not res:
            return "🔍 نتیجه‌ای یافت نشد."

        lines: list[str] = []
        for r in res:
            path = r.get("path", "?")
            content = (r.get("content") or "")[:200]
            lines.append(f"📄 {path}")
            lines.append(f"   {content}...")
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # Grep search
    # ═══════════════════════════════════════════
    async def _grep_search(
        self, pattern: str, file_pattern: str = "*.py"
    ) -> str:
        if not pattern:
            return "⚠️ pattern خالی است."

        # 🐛 FIX: validate regex
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            log_warning(
                f"⚠️ [BRAIN:grep] regex نامعتبر | "
                f"pattern={pattern!r} | {e}"
            )
            return f"⛔ الگوی regex نامعتبر: {e}"

        if not file_pattern:
            file_pattern = "*.py"

        results: list[str] = []
        files_scanned = 0
        files_skipped_size = 0
        files_failed = 0
        t0 = time.monotonic()

        try:
            matched_files = glob.glob(
                f"**/{file_pattern}",
                root_dir=PROJECT_BASE,
                recursive=True,
            )
        except Exception as e:
            log_error(
                f"❌ [BRAIN:grep] glob | "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا در glob: {e}"

        max_bytes = _GREP_MAX_FILE_KB * 1024

        for fpath in matched_files:
            if len(results) >= _GREP_MAX_RESULTS:
                break

            try:
                full = Path(PROJECT_BASE) / fpath
                if not full.is_file():
                    continue
                # 🐛 FIX: size limit
                if full.stat().st_size > max_bytes:
                    files_skipped_size += 1
                    continue

                files_scanned += 1
                with open(full, encoding="utf-8") as f:  # noqa: ASYNC230
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            snippet = line.strip()[
                                :_GREP_MAX_MATCH_LEN
                            ]
                            results.append(
                                f"{fpath}:{i}: {snippet}"
                            )
                            if (
                                len(results)
                                >= _GREP_MAX_RESULTS
                            ):
                                break
            except UnicodeDecodeError:
                # فایل باینری — skip
                continue
            except Exception as e:
                files_failed += 1
                if files_failed <= 3:
                    log_warning(
                        f"⚠️ [BRAIN:grep] {fpath} | "
                        f"{type(e).__name__}: {e}"
                    )
                continue

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_info(
            f"🔎 [BRAIN:grep] pattern={pattern[:LOG_PREVIEW_LEN]!r} | "
            f"file_pattern={file_pattern} | "
            f"hits={len(results)} | "
            f"files_scanned={files_scanned} | "
            f"skipped_size={files_skipped_size} | "
            f"failed={files_failed} | "
            f"{elapsed_ms:.0f}ms"
        )

        if not results:
            return "🔍 چیزی یافت نشد."

        output = "\n".join(results)
        if len(results) >= _GREP_MAX_RESULTS:
            output += (
                f"\n\n... (limit {_GREP_MAX_RESULTS} — ممکنه "
                f"نتایج بیشتری باشه)"
            )
        return output

    # ═══════════════════════════════════════════
    # Query database
    # ═══════════════════════════════════════════
    async def _query_database(
        self, query: str, limit: int = 50
    ) -> str:
        if not query or not query.strip():
            return "⚠️ کوئری خالی است."

        ok, reason = get_access_manager().can_query_db(query)
        if not ok:
            log_warning(
                f"⛔ [BRAIN:db:query] denied | {reason}"
            )
            return f"⛔ کوئری ممنوع: {reason}"

        q_upper = query.strip().upper()
        if not q_upper.startswith("SELECT"):
            return "⛔ فقط SELECT مجاز است."

        if not self.db:
            return "⚠️ دیتابیس متصل نیست."

        # normalize limit
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 1000))

        try:
            t0 = time.monotonic()
            result = await self.db.execute(query)
            all_rows = await result.fetchall()
            elapsed_ms = (time.monotonic() - t0) * 1000
        except Exception as e:
            log_error(
                f"❌ [BRAIN:db:query] "
                f"{type(e).__name__}: {e} | "
                f"q={query[:LOG_PREVIEW_LEN]!r}"
            )
            return f"❌ خطا: {e}"

        if not all_rows:
            log_info(
                f"📊 [BRAIN:db:query] empty | "
                f"{query[:LOG_PREVIEW_LEN]!r} | {elapsed_ms:.0f}ms"
            )
            return "📭 نتیجه‌ای یافت نشد."

        log_info(
            f"📊 [BRAIN:db:query] "
            f"{query[:LOG_PREVIEW_LEN]!r} | "
            f"rows={len(all_rows)} | {elapsed_ms:.0f}ms"
        )

        lines = [f"📊 نتایج کوئری ({len(all_rows)} ردیف):"]

        def _row_to_str(row) -> str:
            """🎯 FIX: سازگار با dict و tuple."""
            try:
                if isinstance(row, dict):
                    return " | ".join(
                        f"{k}={v}" for k, v in row.items()
                    )
                # sqlite3.Row یا tuple
                return " | ".join(str(c) for c in row)
            except Exception:
                return str(row)

        for row in all_rows[:limit]:
            lines.append(_row_to_str(row))

        if len(all_rows) > limit:
            lines.append(
                f"... و {len(all_rows) - limit} ردیف دیگر "
                f"(limit={limit})"
            )
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # Browse web
    # ═══════════════════════════════════════════
    async def _browse_web(self, url: str) -> str:
        if not url or not url.strip():
            return "⚠️ URL خالی است."

        url = url.strip()

        # 🐛 FIX: URL validation ساده
        if not (url.startswith("http://") or url.startswith("https://")):
            log_warning(
                f"⚠️ [BRAIN:web] URL نامعتبر | url={url!r}"
            )
            return (
                "⛔ URL باید با http:// یا https:// شروع شود."
            )

        # ─── import ───
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError as e:
            log_warning(
                f"⚠️ [BRAIN:web] import | "
                f"{type(e).__name__}: {e}"
            )
            return "⚠️ requests/bs4 نصب نیست."

        # 🐛 FIX: اجرا در thread جدا (async-safe)
        def _do_request() -> tuple[str | None, str]:
            try:
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36"
                    )
                }
                r = requests.get(
                    url,
                    headers=headers,
                    timeout=_WEB_TIMEOUT_S,
                )
                r.raise_for_status()
                return r.text, ""
            except Exception as e:
                return None, f"{type(e).__name__}: {e}"

        t0 = time.monotonic()
        try:
            html, err = await asyncio.to_thread(_do_request)
        except Exception as e:
            log_error(
                f"❌ [BRAIN:web:browse] to_thread | "
                f"{type(e).__name__}: {e} | url={url}"
            )
            return f"❌ خطا: {e}"

        elapsed_ms = (time.monotonic() - t0) * 1000

        if not html:
            log_warning(
                f"⚠️ [BRAIN:web:browse] fetch failed | "
                f"url={url} | {err} | {elapsed_ms:.0f}ms"
            )
            return f"❌ خطا در دریافت: {err}"

        # ─── parse ───
        try:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(
                ["script", "style", "nav", "footer", "header", "noscript"]
            ):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
        except Exception as e:
            log_error(
                f"❌ [BRAIN:web:browse] parse | "
                f"{type(e).__name__}: {e} | url={url}"
            )
            return f"❌ خطا در parse: {e}"

        log_info(
            f"🌐 [BRAIN:web:browse] {url} | "
            f"{len(text)}ch | {elapsed_ms:.0f}ms"
        )
        return text[:_WEB_MAX_CONTENT] or "📄 محتوای خالی"

    # ═══════════════════════════════════════════
    # Clipboard
    # ═══════════════════════════════════════════
    async def _clipboard_get(self) -> str:
        try:
            import pyperclip
        except ImportError:
            return "⚠️ pyperclip نصب نیست."

        try:
            content = pyperclip.paste() or ""
        except Exception as e:
            log_error(
                f"❌ [BRAIN:clipboard] "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

        content = str(content)[:_CLIPBOARD_MAX_LEN]
        log_info(f"📋 [BRAIN:clipboard] {len(content)}ch")
        return f"📋 کلیپ‌بورد:\n{content}"

    # ═══════════════════════════════════════════
    # Zip archive
    # ═══════════════════════════════════════════
    async def _zip_archive(
        self, source: str, dest: str = "", action: str = "compress"
    ) -> str:
        import shutil

        if self.read_only:
            return "⛔ حالت فقط‌خواندنی"

        if action not in ("compress", "extract"):
            return f"⛔ action نامعتبر: {action}"

        try:
            src = self._safepath(source)
        except Exception:
            return f"⛔ مسیر نامعتبر: {source}"

        if not src.exists():
            return f"⚠️ '{source}' وجود ندارد."

        # ─── compress ───
        if action == "compress":
            if not dest:
                dest = f"{src.name}.zip"

            # 🐛 FIX: چک write access
            if not self._is_safe_write_path(dest):
                return f"⛔ دسترسی نوشتن در '{dest}' مجاز نیست."

            try:
                dst = self._safepath(dest)
            except Exception as e:
                return f"⛔ مسیر مقصد نامعتبر: {e}"

            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                base = str(dst).replace(".zip", "")
                shutil.make_archive(base, "zip", src)
                log_success(
                    f"✅ [BRAIN:zip:compress] {source} → {dest}"
                )
                return f"✅ آرشیو {dest} ایجاد شد."
            except Exception as e:
                log_error(
                    f"❌ [BRAIN:zip:compress] "
                    f"{type(e).__name__}: {e}"
                )
                return f"❌ خطا: {e}"

        # ─── extract ───
        if not dest:
            dest = str(src.parent / src.stem)

        # 🐛 FIX: چک write access
        if not self._is_safe_write_path(dest):
            return f"⛔ دسترسی نوشتن در '{dest}' مجاز نیست."

        try:
            dst = self._safepath(dest)
        except Exception as e:
            return f"⛔ مسیر مقصد نامعتبر: {e}"

        # ─── validate zip ───
        try:
            with zipfile.ZipFile(src, "r") as z:
                bad_file = z.testzip()
                if bad_file is not None:
                    return (
                        f"⚠️ فایل «{bad_file}» در zip خراب است."
                    )
        except zipfile.BadZipFile as e:
            return f"⛔ فایل zip نامعتبر: {e}"
        except Exception as e:
            return f"❌ خطا در باز کردن zip: {e}"

        # 🐛 FIX امنیتی: zip-slip protection
        try:
            dst.mkdir(parents=True, exist_ok=True)
            dst_resolved = dst.resolve()

            with zipfile.ZipFile(src, "r") as z:
                for member in z.namelist():
                    member_path = (dst / member).resolve()
                    # چک اینکه member داخل dst هست
                    try:
                        member_path.relative_to(dst_resolved)
                    except ValueError:
                        log_warning(
                            f"⛔ [BRAIN:zip:extract] zip-slip | "
                            f"member={member!r} | dst={dst}"
                        )
                        return (
                            f"⛔ فایل zip شامل مسیر ناامن است: "
                            f"{member}"
                        )
                # اگر همه امن بودند، extract
                z.extractall(dst)

            log_success(
                f"✅ [BRAIN:zip:extract] {source} → {dest}"
            )
            return f"✅ آرشیو در {dest} باز شد."

        except Exception as e:
            log_error(
                f"❌ [BRAIN:zip:extract] "
                f"{type(e).__name__}: {e}"
            )
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
            if (
                getattr(self, "semantic_search", None)
                and self.semantic_search.collection
            ):
                try:
                    ids = (
                        self.semantic_search.collection.get()
                        .get("ids", [])
                    )
                    items = len(ids)
                except Exception:
                    items = "?"
                info += f"🔹 فایل‌های ایندکس‌شده: {items}\n"
                info += "🔹 حافظه معنایی: فعال\n"
            else:
                info += "🔹 حافظه معنایی: غیرفعال\n"
        except Exception as e:
            log_warning(
                f"⚠️ [BRAIN:ctx] semantic | "
                f"{type(e).__name__}: {e}"
            )
            info += "🔹 حافظه معنایی: خطا در خواندن\n"
        return info

    # ═══════════════════════════════════════════
    # System dashboard
    # ═══════════════════════════════════════════
    async def _system_dashboard(self) -> str:
        try:
            import psutil
        except ImportError:
            return "⚠️ psutil نصب نیست."

        try:
            # 🐛 FIX: cpu_percent بلاک‌کننده (interval=0.3)
            # → اجرا در thread جدا
            cpu = await asyncio.to_thread(
                psutil.cpu_percent, 0.3
            )
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
        except Exception as e:
            log_error(
                f"❌ [BRAIN:dashboard] "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"
    # ═══════════════════════════════════════════
    # 🆕 خواب مغز
    # ═══════════════════════════════════════════

    async def _consolidation_run(self) -> str:
        """اجرای دستی خواب مغز."""
        try:
            from core.brain.consolidation import (
                run_once,
                get_stats,
            )
            result = await run_once()
            stats = get_stats()
            return (
                f"🌙 خواب مغز اجرا شد:\n"
                f"  • انتقال‌یافته: {result['moved']} مورد\n"
                f"  • Working: {result['working_before']} → "
                f"{result['working_after']}\n"
                f"  • کل اجراها: {stats['runs']}\n"
                f"  • کل انتقال: {stats['moved_total']}"
            )
        except Exception as e:
            log_error(
                f"❌ [BRAIN:consolidation_run] "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    async def _brain_status(self) -> str:
        """وضعیت کامل مغز — برای خودآگاهی AI."""
        try:
            mem = self.memory
            stats = mem.get_stats()

            lines = ["🧠 **وضعیت مغز:**", ""]

            # ─── حافظه‌ها ───
            lines.append("📚 حافظه‌ها:")
            lines.append(f"  • Working: {stats.get('working', 0)}")
            lt = stats.get("long_term", {})
            lines.append(f"  • Knowledge: {lt.get('knowledge', 0)}")
            lines.append(f"  • Lessons: {lt.get('lessons', 0)}")
            proc = stats.get("procedural", {})
            lines.append(f"  • Tasks: {proc.get('tasks', 0)}")
            sem = stats.get("semantic", {})
            lines.append(
                f"  • ChromaDB: {sem.get('count', 0)} "
                f"(enabled={sem.get('enabled', False)})"
            )
            pers = stats.get("persistent", {})
            lines.append(
                f"  • Persistent: {pers.get('total', 0)} "
                f"(enabled={pers.get('enabled', False)})"
            )

            # ─── ابزارها ───
            lines.append("")
            lines.append("🔧 ابزارها:")
            lines.append(f"  • TOOL_MAP: {len(self.TOOL_MAP)}")
            lines.append(f"  • ALLOWED: {len(self.ALLOWED_TOOLS)}")

            # ─── Client ───
            lines.append("")
            lines.append("🤖 Client:")
            lines.append(f"  • Model: {self.model}")
            lines.append(f"  • Ready: {self.client is not None}")

            # ─── Consolidation ───
            try:
                from core.brain.consolidation import get_stats
                cstats = get_stats()
                lines.append("")
                lines.append("🌙 خواب مغز:")
                lines.append(f"  • Runs: {cstats.get('runs', 0)}")
                lines.append(
                    f"  • Moved: {cstats.get('moved_total', 0)}"
                )
            except Exception:
                pass

            return "\n".join(lines)
        except Exception as e:
            log_error(
                f"❌ [BRAIN:brain_status] "
                f"{type(e).__name__}: {e}"
            )
            return f"❌ خطا: {e}"

    # ═══════════════════════════════════════════
    # 🆕 Proactive Diagnosis
    # ═══════════════════════════════════════════

    async def _proactive_diagnosis(self) -> str:
        """چک خودکار سلامت سیستم.

        Returns:
            گزارش خوانا.
        """
        issues: list[str] = []
        oks: list[str] = []

        # ─── چک حافظه ───
        try:
            stats = self.memory.get_stats()
            sem = stats.get("semantic", {})
            if sem.get("count", 0) < 50:
                issues.append(
                    f"⚠️ ChromaDB کم‌حجم: "
                    f"{sem.get('count', 0)} آیتم"
                )
            else:
                oks.append(
                    f"✅ ChromaDB: {sem.get('count', 0)}"
                )

            pers = stats.get("persistent", {})
            if not pers.get("enabled"):
                issues.append("⚠️ PersistentMemory غیرفعال")
            else:
                oks.append("✅ PersistentMemory فعال")
        except Exception as e:
            issues.append(f"❌ حافظه: {e}")

        # ─── چک Client ───
        if self.client is None:
            issues.append("❌ AI Client آفلاین")
        else:
            oks.append(f"✅ Client: {self.model}")

        # ─── چک پلتفرم‌های فعال ───
        try:
            from core.brain.mixin_platforms import (
                PlatformManager,
            )
            pm = await PlatformManager.get()
            active = await pm.list_active()
            if active:
                oks.append(
                    f"✅ پلتفرم‌های فعال: {', '.join(active)}"
                )
            else:
                oks.append("ℹ️ هیچ پلتفرمی فعال نیست")
        except Exception:
            pass

        # ─── جمع‌بندی ───
        lines = ["🩺 **تشخیص خودکار:**", ""]
        if oks:
            lines.append("**سالم:**")
            lines.extend(f"  {o}" for o in oks)
        if issues:
            lines.append("")
            lines.append("**مشکلات:**")
            lines.extend(f"  {i}" for i in issues)
        if not issues:
            lines.append("")
            lines.append("🎉 همه چیز سالمه!")

        return "\n".join(lines)

    @staticmethod
    @lru_cache(maxsize=1)
    def _is_windows() -> bool:
        """🎯 FIX: با lru_cache (فقط یک‌بار محاسبه)."""
        return os.name == "nt"
