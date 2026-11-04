"""Brain — کلاس اصلی + singleton.

نسخهٔ ۶ (این نسخه):
  - 🎯 NEW: ۸ ابزار Chunk Reading به `_TOOL_METHODS` اضافه شد:
      * `read_all`     → `_read_all`
      * `read_chunk`   → `_read_chunk`
      * `read_lines`   → `_read_lines`
      * `read_range`   → `_read_range`
  - 🎯 NEW: ۴ ابزار File Utils به `_TOOL_METHODS` اضافه شد:
      * `file_info`    → `_file_info`
      * `file_exists`  → `_file_exists`
      * `file_hash`    → `_file_hash`
      * `find_files`   → `_find_files`
  - 🎯 مجموع ابزارها: 63 (قبلاً ۴۵)
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۵:
      * `_lessons` قبل از `learning_engine`.
      * `l` → `item` (E741).
      * `_background_index` reset flag.
      * tools audit (code, file, fix_fstrings).
"""
from __future__ import annotations
from core.brain.mixin_platforms import PlatformMixin
import os
import threading
import time
from datetime import datetime
from typing import Any

# 🎯 از config (منبع یکتا)
from config import AI, AI_PRIORITY
from core.brain.constants import (
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
    log_error,
    log_info,
    log_success,
    log_warning,
)


# ═══════════════════════════════════════════════════════════
# لیست ابزارهای موردانتظار (نام → نام متد)
# ═══════════════════════════════════════════════════════════
_TOOL_METHODS: dict[str, str] = {
    # ═══ فایل (read) — ۱۰ ابزار ═══
    "read_file": "_read_file",
    "read_all": "_read_all",              # 🆕
    "read_chunk": "_read_chunk",          # 🆕
    "read_lines": "_read_lines",          # 🆕
    "read_range": "_read_range",          # 🆕
    "file_info": "_file_info",            # 🆕
    "file_exists": "_file_exists",        # 🆕
    "file_hash": "_file_hash",            # 🆕
    "find_files": "_find_files",          # 🆕
    "list_directory": "_list_directory",

    # ═══ فایل (write) — ۵ ابزار ═══
    "write_file": "_write_file",
    "edit_file": "_edit_file",
    "append_knowledge": "_append_knowledge",
    "delete_file": "_delete_file",
    "move_file": "_move_file",

    # ═══ Git — ۵ ابزار ═══
    "git_status": "_git_status",
    "git_diff": "_git_diff",
    "git_commit": "_git_commit",
    "git_log": "_git_log",
    "git_branch": "_git_branch",

    # ═══ اجرا — ۵ ابزار ═══
    "execute_command": "_execute_command",
    "execute_python": "_execute_python",
    "run_tests": "_run_tests",
    "run_tests_coverage": "_run_tests_with_coverage",
    "analyze_security": "_analyze_security",

    # ═══ سیستم — ۵ ابزار ═══
    "get_system_info": "_get_system_info",
    "list_processes": "_list_processes",
    "kill_process": "_kill_process",
    "ping_host": "_ping_host",
    "system_dashboard": "_system_dashboard",

    # ═══ تحلیل — ۴ ابزار ═══
    "analyze_code": "_analyze_code",
    "semantic_search": "_semantic_search",
    "grep_search": "_grep_search",
    "get_project_context": "_get_project_context",

    # ═══ دیتابیس — ۱ ابزار ═══
    "query_database": "_query_database",

    # ═══ وب — ۳ ابزار ═══
    "browse_web": "_browse_web",
    "web_search": "_web_search",
    "clipboard_get": "_clipboard_get",

    # ═══ zip — ۱ ابزار ═══
    "zip_archive": "_zip_archive",

    # ═══ AI helpers — ۵ ابزار ═══
    "generate_tests": "_generate_tests",
    "code_review": "_code_review",
    "fix_bug": "_fix_bug",
    "plan_task": "_plan_task",
    "backup_project": "_backup_project",

    # ═══ تنظیمات — ۶ ابزار ═══
    "get_setting": "_get_setting",
    "set_setting": "_set_setting",
    "toggle_platform": "_toggle_platform",
    "platform_status": "_platform_status",
    "change_poll_interval": "_change_poll_interval",
    "get_access_summary": "_get_access_summary",

    # ═══ Audit — ۳ ابزار ═══
    "audit_code": "_audit_code",
    "audit_file": "_audit_file",
    "fix_fstrings": "_fix_fstrings",

    # ═══ پلتفرم‌ها ═══
    "start_platform": "_start_platform",
    "stop_platform": "_stop_platform",
    "platform_health": "_platform_health",
    "platform_diagnose": "_platform_diagnose",
    "platform_run_once": "_platform_run_once",
    "list_platforms": "_list_platforms",

    # 🆕 اضافه کن:
    "diagnose_platform": "_diagnose_platform",
    "run_platform_as_user": "_run_platform_as_user",

    # 🆕 کنترل پلتفرم
    "control_platform": "_control_platform",
    "control_stats": "_control_stats",
}


# ═══════════════════════════════════════════════════════════
# Brain
# ═══════════════════════════════════════════════════════════
class Brain(
    FilesMixin,
    SystemMixin,
    InfoMixin,
    ReportsMixin,
    AgentMixin,
    PlatformMixin,
):
    """مغز هوشمند امن — ترکیب همهٔ mixinها."""

    # ─── reference به TOOL_MAP (در _build_tool_map پر می‌شود) ───
    TOOL_MAP: dict[str, Any] = {}

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

        # ─── انتخاب هوشمند مدل ───
        if model is None:
            model = (
                (AI or {}).get("model")
                or DEFAULT_MODEL
                or "qwen2.5:3b"
            )

        log_info(
            f"🧠 [BRAIN:init:start] model={model} | "
            f"read_only={read_only} | full_access={full_access} | "
            f"ai_priority={AI_PRIORITY}"
        )

        PersianBrainHandler.setup_environment()

        # ─── state پایه ───
        self.event_bus = event_bus
        self.db = db
        self.read_only = read_only
        self.full_access = full_access

        # 🎯 api_key: اولویت api_key > config.AI > env
        self.api_key = (
            api_key
            or (AI or {}).get("deepseek_api_key")
            or ""
        )
        if not self.api_key:
            log_warning(
                "⚠️ [BRAIN:init:key] DEEPSEEK_API_KEY تنظیم نشده "
                "(Ollama fallback استفاده می‌شود)"
            )

        # 🎯 ai_priority از config (نه از AI dict تکراری)
        self._ai_priority = (AI_PRIORITY or "auto").strip().lower()

        # ─── client ───
        self.model = model
        self.client = None
        self._init_client()

        # ─── caches ───
        self.cache = SmartCache("brain_cache")
        self.agent_cache = SmartCache("agent_cache")

        # ─── memory ───
        self.memory = BrainMemory(
            max_tokens=MAX_CONTEXT_TOKENS,
            use_api=use_api_memory,
            api_url=memory_api_url,
            api_key=memory_api_key,
        )

        # ─── semantic + learning ───
        self.semantic_search = ProjectSemanticSearch()

        # 🎯 بارگذاری درس‌های قبلی (Reflexion) از MemoryStore
        # ⚠️ FIX: _lessons **قبل از** learning_engine تعریف می‌شود
        self._lessons: list[str] = []
        try:
            from core.adapters.memory_store import MemoryStore
            store = MemoryStore()
            lessons = store.query(
                category="reflexion",
                query="خطاهای قبلی",
                limit=5,
            )
            self._lessons = [
                item.get("value", "") for item in lessons
            ]
            log_info(
                f"📚 [BRAIN:lessons] {len(self._lessons)} درس بارگذاری شد"
            )
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:lessons] {e}")

        # 🎯 FIX: learning_engine **بعد** از _lessons
        self.learning_engine = LearningEngine(self)
        self.planner = TaskPlanner(self)
        self.healer = SelfHealer(self)
        self.test_gen = TestGenerator(self)
        self.web_search = WebSearch()
        self.reviewer = CodeReviewer(self)

        # ─── state اجرا ───
        self.busy = False
        self.current_mode = "precise"
        self.last_read_file: str | None = None
        self.tools_definition: list[dict] = self._build_tools()

        # 🎯 FIX: ساخت lazy TOOL_MAP در init (نه import)
        if not Brain.TOOL_MAP:
            Brain.TOOL_MAP = _build_tool_map()

        self.stats: dict[str, Any] = {
            "commands_processed": 0,
            "agents_run": 0,
            "errors": 0,
            "started_at": datetime.now().isoformat(),
        }

        # 🐛 FIX: flag برای جلوگیری از thread تکراری
        self._index_started = False

        if (
            self.semantic_search.collection
            and not self._index_started
        ):
            self._index_started = True
            threading.Thread(
                target=self._background_index,
                daemon=True,
                name="brain_index",
            ).start()
            log_info("📚 [BRAIN:init:index] thread ایندکس شروع شد")
        elif not self.semantic_search.collection:
            log_info(
                "ℹ️ [BRAIN:init:index] semantic_search غیرفعال — "
                "بدون ایندکس"
            )

        elapsed_ms = (time.monotonic() - t0) * 1000
        client_state = "✅" if self.client else "❌"
        log_success(
            f"✅ [BRAIN:init:done] {elapsed_ms:.0f}ms | "
            f"tools={len(self.tools_definition)} | "
            f"client={client_state} ({self.model})"
        )


# ═══════════════════════════════════════════════════════════
# ساخت TOOL_MAP — با بررسی وجود هر متد
# ═══════════════════════════════════════════════════════════
def _build_tool_map() -> dict[str, Any]:
    """ساخت TOOL_MAP با چک وجود هر متد."""
    tool_map: dict[str, Any] = {}
    missing: list[str] = []

    for tool_name, method_name in _TOOL_METHODS.items():
        if hasattr(Brain, method_name):
            tool_map[tool_name] = getattr(Brain, method_name)
        else:
            missing.append(f"{tool_name}→{method_name}")

    # چک هم‌خوانی سه لیست
    from core.brain.tools_meta import ALLOWED_TOOLS, TOOL_DEFINITIONS
    allowed_names = set(ALLOWED_TOOLS)
    def_names = {td["function"]["name"] for td in TOOL_DEFINITIONS}
    method_names = set(_TOOL_METHODS)

    if method_names - allowed_names:
        log_warning(
            f"⚠️ [BRAIN:toolmap] در methods ولی نه allowed: "
            f"{sorted(method_names - allowed_names)}"
        )
    if allowed_names - def_names:
        log_warning(
            f"⚠️ [BRAIN:toolmap] در allowed ولی نه definitions: "
            f"{sorted(allowed_names - def_names)}"
        )
    if def_names - allowed_names:
        log_warning(
            f"⚠️ [BRAIN:toolmap] در definitions ولی نه allowed: "
            f"{sorted(def_names - allowed_names)}"
        )

    if missing:
        _msg = (
            f"⚠️ [BRAIN:toolmap] {len(missing)} ابزار یافت نشد | "
            f"missing={missing[:5]}"
            + (f"... +{len(missing) - 5}" if len(missing) > 5 else "")
        )
        # 🎯 FIX: در حالت strict، خطا بالا بده (نه فقط warning)
        _strict = os.getenv(
            "BRAIN_STRICT_TOOLMAP", "false"
        ).strip().lower() == "true"
        if _strict:
            log_error(_msg)
            raise RuntimeError(
                f"STRICT_TOOLMAP: {len(missing)} ابزار گمشده: "
                f"{missing}"
            )
        log_warning(_msg)

    log_success(
        f"✅ [BRAIN:toolmap] {len(tool_map)}/{len(_TOOL_METHODS)} "
        f"ابزار ثبت شد"
    )
    return tool_map


# 🎯 TOOL_MAP به‌صورت lazy در Brain.__init__ پر می‌شود (نه در import)
Brain.TOOL_MAP = {}

# ═══════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════
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
    """Singleton — instance مشترک سراسری.

    در هر فراخوانی، اگر پارامترهای جدیدی داده شوند که instance قبلی
    نداشته باشد، آن‌ها را به‌روز می‌کند.
    """
    global _brain_instance
    with _brain_lock:
        if _brain_instance is None:
            log_info(
                "🏗️ [BRAIN:singleton:create] ساخت نمونهٔ اول"
            )
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
            updated: list[str] = []
            if event_bus is not None and _brain_instance.event_bus is None:
                _brain_instance.set_event_bus(event_bus)
                updated.append("event_bus")
            if db is not None and _brain_instance.db is None:
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
                        f"⚠️ [BRAIN:singleton:mem] "
                        f"{type(e).__name__}: {e}"
                    )
            if updated:
                log_info(
                    f"♻️ [BRAIN:singleton:update] {updated}"
                )
            else:
                log_info(
                    "ℹ️ [BRAIN:singleton:reuse] instance موجود برگشت"
                )
    return _brain_instance


def reset_brain() -> None:
    """🎯 پاک‌کردن singleton — فقط برای تست.

    ⚠️ در runtime صدا نزن — ممکنه referenceهای موجود بی‌اعتبار بشن.
    """
    global _brain_instance
    with _brain_lock:
        if _brain_instance is not None:
            log_info("🗑️ [BRAIN:singleton:reset] instance پاک شد")
            _brain_instance = None
