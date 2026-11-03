"""Loader — بارگذاری داده از data/."""
from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent


def _resolve(rel_path: str) -> Path:
    """مسیر فایل رو پیدا کن — با پشتیبانی از پسوند و configs/.

    مثال:
        "actions" → "configs/actions.json"
        "configs/actions" → "configs/actions.json"
        "actions.json" → "configs/actions.json"
        "prompts/domain_guard.md" → "prompts/domain_guard.md"
    """
    # ─── اگه پسوند داره، همون ───
    if "." in rel_path.split("/")[-1]:
        p = DATA_DIR / rel_path
        if p.exists():
            return p
        # شاید تو configs/ باشه
        if not rel_path.startswith(("configs/", "prompts/", "runtime/")):
            p = DATA_DIR / "configs" / rel_path
            if p.exists():
                return p
        return DATA_DIR / rel_path

    # ─── بدون پسوند → .json اضافه کن ───
    # اول تو configs/ بگرد
    p = DATA_DIR / "configs" / f"{rel_path}.json"
    if p.exists():
        return p

    # بعد تو روت data/
    p = DATA_DIR / f"{rel_path}.json"
    if p.exists():
        return p

    # آخر، مسیر اصلی (برای خطا)
    return DATA_DIR / "configs" / f"{rel_path}.json"


def load_json(
    rel_path: str,
    default: dict | None = None,
    force: bool = False,
) -> dict:
    """بارگذاری JSON از data/.

    Args:
        rel_path: مسیر نسبی (مثلاً 'configs/tiers.json' یا 'actions')
        default: مقدار پیش‌فرض اگه فایل نبود
        force: اگه True، خطا رو نادیده بگیر
    """
    p = _resolve(rel_path)
    if not p.exists():
        if default is not None:
            return default
        if force:
            return {}
        raise FileNotFoundError(f"data/{rel_path} وجود ندارد (مسیر: {p})")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        if default is not None:
            return default
        if force:
            return {}
        raise


def load_text(rel_path: str) -> str:
    """بارگذاری متن از data/."""
    # ─── اول مستقیم ───
    p = DATA_DIR / rel_path
    if p.exists():
        return p.read_text(encoding="utf-8")

    # ─── اگه تو prompts/ نبود، شاید تو روت ───
    p = DATA_DIR / "prompts" / rel_path
    if p.exists():
        return p.read_text(encoding="utf-8")

    raise FileNotFoundError(f"data/{rel_path} وجود ندارد")

def load_md(rel_path: str) -> str:
    """بارگذاری فایل markdown از data/prompts/ یا data/.
    
    Alias برای load_text — با پشتیبانی از prompts/.
    """
    return load_text(rel_path)

def save_json(rel_path: str, data: dict) -> None:
    """ذخیرهٔ JSON در data/."""
    p = DATA_DIR / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


__all__ = [
    "DATA_DIR",
    "load_json",
    "load_md",
    "load_text",
    "save_json",
]