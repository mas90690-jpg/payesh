"""tools_meta.py — metadata ابزارها (از data/configs/tools_meta.json)."""

from __future__ import annotations

import copy
from types import MappingProxyType

from data.loader import load_json as _load_json
from core.kernel.logger import log_info, log_warning, log_error, log_debug, log_success
# بارگذاری از data
_tools_data = _load_json("configs/tools_meta.json")

# ─── ابزارهای مجاز (immutable) ───
ALLOWED_TOOLS: MappingProxyType = MappingProxyType(
    _tools_data.get("allowed_tools", {}))

log_info("✅ [tools_meta] بارگذاری شد")
# ─── تعاریف ابزارها ───
TOOL_DEFINITIONS: list[dict] = list(_tools_data.get("tool_definitions", []))

# ─── دستورات مستقیم ───
ALLOWED_DIRECT_COMMANDS: MappingProxyType = MappingProxyType(
    {
        "سلام": "greeting",
        "خوبی": "greeting",
        "درود": "greeting",
        "وضعیت": "status",
        "آمار": "status",
        "گزارش": "report",
        "help": "help",
        "راهنما": "help",
        "کمک": "help",
    }
)


def build_tool_definitions() -> list[dict]:
    """برگرداندن deep-copy از TOOL_DEFINITIONS."""
    return copy.deepcopy(TOOL_DEFINITIONS)


def get_tool_info(name: str) -> dict | None:
    """متادیتای یک ابزار یا None."""
    if not name:
        return None
    info = ALLOWED_TOOLS.get(name)
    return dict(info) if info else None


def get_tools_by_risk(risk: str) -> list[str]:
    """ابزارهای یک سطح ریسک مشخص."""
    if not risk:
        return []
    risk_low = str(risk).lower()
    if risk_low not in {"low", "medium", "high"}:
        return []
    return sorted(name for name, info in ALLOWED_TOOLS.items()
                  if info.get("risk") == risk_low)


def get_tools_requiring_confirm() -> list[str]:
    """ابزارهایی که نیاز به تأیید دارند."""
    return sorted(name for name, info in ALLOWED_TOOLS.items()
                  if info.get("require_confirm"))


def get_tool_count() -> int:
    """تعداد ابزارهای مجاز."""
    return len(ALLOWED_TOOLS)


def get_stats() -> dict:
    """خلاصهٔ وضعیت ابزارها."""
    by_risk = {"low": 0, "medium": 0, "high": 0}
    for info in ALLOWED_TOOLS.values():
        r = info.get("risk", "low")
        if r in by_risk:
            by_risk[r] += 1

    return {
        "total_tools": len(ALLOWED_TOOLS),
        "by_risk": by_risk,
        "require_confirm": len(get_tools_requiring_confirm()),
        "tool_definitions": len(TOOL_DEFINITIONS),
        "direct_commands": len(ALLOWED_DIRECT_COMMANDS),
    }


__all__ = [
    "ALLOWED_DIRECT_COMMANDS",
    "ALLOWED_TOOLS",
    "TOOL_DEFINITIONS",
    "build_tool_definitions",
    "get_stats",
    "get_tool_count",
    "get_tool_info",
    "get_tools_by_risk",
    "get_tools_requiring_confirm",
]
