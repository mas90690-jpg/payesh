"""
loader — بارگذاری داده از فایل‌های JSON و MD.

با hot-reload (mtime-aware) و cache.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from core.kernel.logger import log_info, log_warning
from data import paths as data_paths


# ═══════════════════════════════════════════
# cache
# ═══════════════════════════════════════════
_cache: dict[str, tuple[float, Any]] = {}
_RELOAD_SECONDS = 60.0
_last_check: dict[str, float] = {}


# ═══════════════════════════════════════════
# JSON loader
# ═══════════════════════════════════════════
def load_json(
    name: str,
    default: dict | None = None,
    force: bool = False,
) -> dict:
    """بارگذاری JSON از data/configs/.

    Args:
        name: نام فایل بدون .json (مثلاً "domain").
        default: مقدار پیش‌فرض اگر فایل نبود.
        force: نادیده گرفتن cache.

    Returns:
        dict — اگر فایل نبود/خراب بود، default یا {}.
    """
    path = data_paths.CONFIGS_DIR / f"{name}.json"
    return _load_file(path, default or {}, force)


def load_md(
    name: str,
    default: str = "",
    force: bool = False,
) -> str:
    """بارگذاری Markdown از data/templates/."""
    path = data_paths.PROJECT_ROOT / "data" / "templates" / f"{name}.md"
    if not path.exists():
        return default
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        log_warning(f"⚠️ [LOADER:md] {name} | {type(e).__name__}: {e}")
        return default


def _load_file(
    path: Path,
    default: Any,
    force: bool,
) -> Any:
    """بارگذاری فایل JSON با cache mtime-aware."""
    key = str(path)

    # ─── cache check ───
    if not force:
        try:
            mtime = path.stat().st_mtime if path.exists() else 0.0
        except OSError:
            mtime = 0.0

        if key in _cache:
            cached_mtime, cached_data = _cache[key]
            if cached_mtime == mtime:
                return cached_data

    # ─── load ───
    if not path.exists():
        _cache[key] = (0.0, default)
        return default

    try:
        mtime = path.stat().st_mtime
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        _cache[key] = (mtime, data)
        log_info(f"📂 [LOADER:json] {path.name} بارگذاری شد")
        return data
    except json.JSONDecodeError as e:
        log_warning(
            f"⚠️ [LOADER:json] {path.name} | "
            f"JSON نامعتبر | line={e.lineno}"
        )
        return default
    except Exception as e:
        log_warning(
            f"⚠️ [LOADER:json] {path.name} | "
            f"{type(e).__name__}: {e}"
        )
        return default


def clear_cache() -> None:
    """پاک کردن cache — برای تست."""
    _cache.clear()


__all__ = [
    "load_json",
    "load_md",
    "clear_cache",
]