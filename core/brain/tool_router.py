"""ToolRouter — انتخاب هوشمند ابزار."""
from __future__ import annotations

from typing import ClassVar

from core.kernel.logger import log_info


class ToolRouter:
    ROUTES: ClassVar[dict[str, list[str]]] = {
        "وضعیت": ["brain_status", "system_dashboard"],
        "آمار": ["brain_status", "system_dashboard"],
        "خواب": ["consolidation_run"],
        "consolidation": ["consolidation_run"],
        "دانش": ["semantic_search"],
        "چی می‌دونی": ["semantic_search"],
        "پلتفرم": ["list_platforms", "diagnose_platform"],
        "بله": ["platform_health", "diagnose_platform"],
        "ایتا": ["platform_health", "diagnose_platform"],
        "روبیکا": ["platform_health", "diagnose_platform"],
        "تلگرام": ["platform_health", "diagnose_platform"],
        "وصل نمی": ["diagnose_platform", "platform_health"],
        "کار نمی": ["diagnose_platform", "platform_health"],
        "خطا": ["diagnose_platform", "logs"],
        "باگ": ["audit_code", "audit_file"],
        "کد": ["read_file", "grep_search", "analyze_code"],
        "فایل": ["read_file", "file_info"],
        "بخوان": ["read_file"],
        "بگرد": ["find_files", "grep_search"],
        "cpu": ["system_dashboard"],
        "ram": ["system_dashboard"],
        "دیسک": ["system_dashboard"],
        "پردازه": ["list_processes"],
        "جستجو": ["web_search"],
        "سایت": ["browse_web"],
        "تست": ["run_tests"],
        "دیتابیس": ["query_database"],
        "sql": ["query_database"],
        "پروژه": ["get_project_context"],
        "ساختار": ["list_directory"],
    }

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
        return (
            "\n\n# راهنمای ابزار:\n"
            + "\n".join(f"  - {t}" for t in tools)
        )


def get_tool_router():
    return ToolRouter


__all__ = ["ToolRouter", "get_tool_router"]
