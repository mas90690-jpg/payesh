"""ToolRouter — انتخاب هوشمند ابزار (از data/configs/routes.json)."""

from __future__ import annotations

from typing import ClassVar

from core.kernel.logger import log_error, log_info, log_warning
from data.loader import load_json


class ToolRouter:
    # 🆕 بارگذاری ROUTES از data/configs/routes.json
    _routes_data = load_json("configs/routes.json")
    ROUTES: ClassVar[dict[str, list[str]]] = _routes_data.get("routes", {})

    @classmethod
    def route(cls, question):
        if not question:
            return []
        q = question.lower().strip()
        matched = []
        for kw, tools in cls.ROUTES.items():
            if kw.lower() in q:
                for t in tools:
                    if t not in matched:
                        matched.append(t)
        matched = matched[:5]
        if matched:
            log_info(f"ToolRouter: {question[:40]} -> {matched}")
        return matched

    @classmethod
    def build_hint(cls, question):
        tools = cls.route(question)
        if not tools:
            return ""
        return "\n\n# راهنمای ابزار:\n" + "\n".join(f"  - {t}" for t in tools)


def get_tool_router():
    return ToolRouter


__all__ = ["ToolRouter", "get_tool_router"]
