"""Brain — کلاس اصلی + singleton."""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Any

from core.brain.constants import (
    AI,
    DEFAULT_MODEL,
    MAX_CONTEXT_TOKENS,
)
from core.brain.learning import (
    CodeReviewer,
    LearningEngine,
    SelfHealer,
    TaskPlanner,
    TestGenerator,
    WebSearch,
)
from core.brain.memory import (
    APIMemory,
    BrainMemory,
    ProjectSemanticSearch,
    SmartCache,
)
from core.brain.mixin_agent import AgentMixin
from core.brain.mixin_files import FilesMixin
from core.brain.mixin_info import InfoMixin
from core.brain.mixin_reports import ReportsMixin
from core.brain.mixin_system import SystemMixin
from core.brain.utils import PersianBrainHandler
from core.kernel.event_bus import EventBus
from core.kernel.logger import (
    log_info,
    log_success,
    log_warning,
)


class Brain(
    FilesMixin,
    SystemMixin,
    InfoMixin,
    ReportsMixin,
    AgentMixin,
):
    """مغز هوشمند امن — ترکیب همهٔ mixinها."""

    def __init__(
        self,
        event_bus: EventBus | None = None,
        db: Any | None = None,
        read_only: bool = False,
        api_key: str | None = None,
        model: str | None = None,
        full_access: bool = False,
        use_api_memory: bool = False,
        memory_api_url: str = "",
        memory_api_key: str = "",
    ):
        t0 = time.monotonic()

        # 🎯 انتخاب هوشمند مدل
        if model is None:
            model = (
                (AI or {}).get("model")
                or DEFAULT_MODEL
                or "qwen2.5:3b"
            )

        log_info(
            f"🧠 [BRAIN:init] شروع | model={model} "
            f"read_only={read_only} full_access={full_access}"
        )
            
        PersianBrainHandler.setup_environment()

        self.event_bus = event_bus
        self.db = db
        self.read_only = read_only
        self.full_access = full_access
        self.api_key = api_key or os.environ.get(
            "DEEPSEEK_API_KEY"
        )
        self._ai_priority = (
            (AI.get("priority") or "auto").strip().lower()
        )

        if not self.api_key:
            log_warning(
                "⚠️ [BRAIN:init] DEEPSEEK_API_KEY تنظیم نشده"
            )

        self.model = model
        self.client = None
        self._init_client()

        self.cache = SmartCache("brain_cache")
        self.agent_cache = SmartCache("agent_cache")

        self.memory = BrainMemory(
            max_tokens=MAX_CONTEXT_TOKENS,
            use_api=use_api_memory,
            api_url=memory_api_url,
            api_key=memory_api_key,
        )

        self.semantic_search = ProjectSemanticSearch()
        self.learning_engine = LearningEngine(self)
        self.planner = TaskPlanner(self)
        self.healer = SelfHealer(self)
        self.test_gen = TestGenerator(self)
        self.web_search = WebSearch()
        self.reviewer = CodeReviewer(self)

        self.busy = False
        self.current_mode = "precise"
        self.last_read_file: str | None = None
        self.tools_definition = self._build_tools()
        self.stats: dict = {
            "commands_processed": 0,
            "agents_run": 0,
            "errors": 0,
            "started_at": datetime.now().isoformat(),
        }

        if self.semantic_search.collection:
            threading.Thread(
                target=self._background_index, daemon=True
            ).start()

        elapsed_ms = (time.monotonic() - t0) * 1000
        log_success(
            f"✅ [BRAIN:init] کامل | {elapsed_ms:.0f}ms | "
            f"tools={len(self.tools_definition)} | "
            f"client={'✅' if self.client else '❌'}"
        )


# ═══════════════════════════════════════════
# TOOL_MAP — بعد از تعریف کلاس ساخته می‌شود
# ═══════════════════════════════════════════
Brain.TOOL_MAP = {
    "read_file": Brain._read_file,
    "write_file": Brain._write_file,
    "edit_file": Brain._edit_file,
    "list_directory": Brain._list_directory,
    "delete_file": Brain._delete_file,
    "move_file": Brain._move_file,
    "git_status": Brain._git_status,
    "git_diff": Brain._git_diff,
    "git_commit": Brain._git_commit,
    "git_log": Brain._git_log,
    "git_branch": Brain._git_branch,
    "execute_command": Brain._execute_command,
    "get_system_info": Brain._get_system_info,
    "list_processes": Brain._list_processes,
    "kill_process": Brain._kill_process,
    "ping_host": Brain._ping_host,
    "run_tests": Brain._run_tests,
    "execute_python": Brain._execute_python,
    "analyze_code": Brain._analyze_code,
    "semantic_search": Brain._semantic_search,
    "grep_search": Brain._grep_search,
    "query_database": Brain._query_database,
    "browse_web": Brain._browse_web,
    "clipboard_get": Brain._clipboard_get,
    "zip_archive": Brain._zip_archive,
    "web_search": Brain._web_search,
    "generate_tests": Brain._generate_tests,
    "code_review": Brain._code_review,
    "fix_bug": Brain._fix_bug,
    "plan_task": Brain._plan_task,
    "get_project_context": Brain._get_project_context,
    "system_dashboard": Brain._system_dashboard,
    "run_tests_coverage": Brain._run_tests_with_coverage,
    "analyze_security": Brain._analyze_security,
    "backup_project": Brain._backup_project,
    "get_setting": Brain._get_setting,
    "set_setting": Brain._set_setting,
    "toggle_platform": Brain._toggle_platform,
    "platform_status": Brain._platform_status,
    "change_poll_interval": Brain._change_poll_interval,
    "get_access_summary": Brain._get_access_summary,
}


# ═══════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════
_brain_instance: Brain | None = None
_brain_lock = threading.Lock()


def get_brain(
    event_bus: EventBus | None = None,
    db: Any | None = None,
    read_only: bool = False,
    api_key: str | None = None,
    full_access: bool = False,
    use_api_memory: bool = False,
    memory_api_url: str = "",
    memory_api_key: str = "",
) -> Brain:
    """Singleton."""
    global _brain_instance
    with _brain_lock:
        if _brain_instance is None:
            log_info("🏗️ [BRAIN:singleton] ساخت نمونهٔ اول")
            _brain_instance = Brain(
                event_bus=event_bus,
                db=db,
                read_only=read_only,
                api_key=api_key,
                full_access=full_access,
                use_api_memory=use_api_memory,
                memory_api_url=memory_api_url,
                memory_api_key=memory_api_key,
            )
        else:
            updated = []
            if event_bus and not _brain_instance.event_bus:
                _brain_instance.set_event_bus(event_bus)
                updated.append("event_bus")
            if db and not _brain_instance.db:
                _brain_instance.set_database(db)
                updated.append("db")
            if full_access and not _brain_instance.full_access:
                _brain_instance.full_access = full_access
                updated.append("full_access")
            if (
                use_api_memory
                and memory_api_url
                and _brain_instance.memory.api_memory is None
            ):
                try:
                    _brain_instance.memory.api_memory = APIMemory(
                        memory_api_url, memory_api_key
                    )
                    _brain_instance.memory.use_api = True
                    updated.append("memory_api")
                except Exception as e:
                    log_warning(
                        f"⚠️ [BRAIN:singleton] {type(e).__name__}"
                    )
            if updated:
                log_info(
                    f"♻️ [BRAIN:singleton] {updated}"
                )
    return _brain_instance
