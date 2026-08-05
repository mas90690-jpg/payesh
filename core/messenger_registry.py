# -*- coding: utf-8 -*-
"""
Tiny registry so handlers can send a reply on the same platform a message came
from, without being coupled to the collectors. Each collector that can send
registers an async sender:  register_sender("bale", coro(chat_id, chat_type, text)).
"""
from typing import Callable, Dict, Optional

_senders: Dict[str, Callable] = {}


def register_sender(platform: str, sender: Callable) -> None:
    _senders[platform] = sender


def get_sender(platform: str) -> Optional[Callable]:
    return _senders.get(platform)


async def notify_admins(text: str) -> bool:
    """ارسالِ یک پیام به همهٔ مدیران روی پلتفرمِ مدیریت (بله). اگر فرستنده/مدیری
    تنظیم نشده باشد، بی‌صدا رد می‌شود (هرگز کرش نمی‌کند)."""
    try:
        from config import PLUGINS_CONFIG

        cfg = PLUGINS_CONFIG.get("adminhandler", {})
        ids = cfg.get("admin_ids", [])
        platform = cfg.get("platform", "bale")
    except Exception:
        return False
    sender = get_sender(platform)
    if not sender or not ids:
        return False
    ok = False
    for aid in ids:
        try:
            await sender(aid, "private", text)
            ok = True
        except Exception:
            pass
    return ok
