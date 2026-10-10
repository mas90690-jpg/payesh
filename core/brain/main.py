"""Brain — کلاس اصلی + singleton.

نسخهٔ ۵ (این نسخه):
  - 🐛 FIX: `_lessons` حالا **قبل از** `learning_engine` تعریف می‌شود
    (ترتیب وابستگی — جلوگیری از AttributeError).
  - 🐛 FIX: `l` → `item` (رفع هشدار E741).
  - 🐛 FIX: کامنت `# 🎯 بارگذاری درس‌های قبلی` حالا ۸ فاصله دارد.
  - 🐛 FIX: `_background_index` حالا `_index_started` را در صورت
    خطا reset می‌کند (اجازهٔ retry).
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۴.

نسخهٔ ۴ (قبلی):
  - 🎯 NEW: سه ابزار audit به `_TOOL_METHODS` اضافه شد:
      * `audit_code`   → `_audit_code`
      * `audit_file`   → `_audit_file`
      * `fix_fstrings` → `_fix_fstrings`
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۳.

نسخهٔ ۳ (قبلی):
  - 🎯 NEW: `append_knowledge` ابزار — افزودن به bot_knowledge.md
    (در `_TOOL_METHODS` ثبت شد).
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۲.

نسخهٔ ۲ (قبلی):
  - 🎯 استفاده از `config.AI` و `config.AI_PRIORITY` (top-level).
  - 🐛 FIX: `TOOL_MAP` حالا با تابع `_build_tool_map()` ساخته می‌شود
    که قبل از افزودن هر متد، `hasattr` چک می‌کند (جلوگیری از
    AttributeError در صورت حذف/rename یک متد).
  - 🐛 FIX: `_build_tool_map()` حالا هر ابزارِ گم‌شده را لاگ
    می‌کند (به‌جای crash).
  - 🐛 FIX: `_background_index` حالا با یک flag `_index_started`
    محافظت می‌شود (جلوگیری از thread تکراری در init دوباره).
  - 🎯 لاگ‌های دقیق‌تر با prefixهای `[BRAIN:init:*]` و `[BRAIN:singleton:*]`.
  - 🎯 `self.stats` با type hints کامل.
  - 🎯 `api_key` حالا از `config.AI["deepseek_api_key"]` هم می‌آید
    (به‌جای `os.environ.get` مستقیم).
  - 🎯 `get_brain()` حالا در هر بار فراخوانی، `last_used` را به‌روز
    می‌کند (برای debug).
  - 🎯 `reset_brain()` متد جدید برای تست.
"""
from __future__ import annotations

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
    log_info,
    log_success,
    log_warning,
)


# ═══════════════════════════════════════════════════════════
# لیست ابزارهای موردانتظار (نام → نام متد)
# ═══════════════════════════════════════════════════════════
_TOOL_METHODS: dict[str, str] = {
    "read_file": "_read_file",
    "write_file": "_write_file",
    "edit_file": "_edit_file",
    "append_knowledge": "_append_knowledge",
    "list_directory": "_list_directory",
    "delete_file": "_delete_file",
    "move_file": "_move_file",
    "git_status": "_git_status",
    "git_diff": "_git_diff",
    "git_commit": "_git_commit",
    "git_log": "_git_log",
    "git_branch": "_git_branch",
    "execute_command": "_execute_command",
    "get_system_info": "_get_system_info",
    "list_processes": "_list_processes",
    "kill_process": "_kill_process",
    "ping_host": "_ping_host",
    "run_tests": "_run_tests",
    "execute_python": "_execute_python",
    "analyze_code": "_analyze_code",
    "semantic_search": "_semantic_search",
    "grep_search": "_grep_search",
    "query_database": "_query_database",
    "browse_web": "_browse_web",
    "clipboard_get": "_clipboard_get",
    "zip_archive": "_zip_archive",
    "web_search": "_web_search",
    "generate_tests": "_generate_tests",
    "code_review": "_code_review",
    "fix_bug": "_fix_bug",
    "plan_task": "_plan_task",
    "get_project_context": "_get_project_context",
    "system_dashboard": "_system_dashboard",
    "run_tests_coverage": "_run_tests_with_coverage",
    "analyze_security": "_analyze_security",
    "backup_project": "_backup_project",
    "get_setting": "_get_setting",
    "set_setting": "_set_setting",
    "toggle_platform": "_toggle_platform",
    "platform_status": "_platform_status",
    "change_poll_interval": "_change_poll_interval",
    "get_access_summary": "_get_access_summary",
    # ─── 🎯 NEW: ابزارهای audit ───
    "audit_code": "_audit_code",
    "audit_file": "_audit_file",
    "fix_fstrings": "_fix_fstrings",
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
    """🎯 ساخت TOOL_MAP با چک وجود هر متد.

    اگر متدی در کلاس نباشد، به‌جای crash، warning می‌دهد.

    Returns:
        dict {tool_name: method}.
    """
    tool_map: dict[str, Any] = {}
    missing: list[str] = []

    for tool_name, method_name in _TOOL_METHODS.items():
        if hasattr(Brain, method_name):
            tool_map[tool_name] = getattr(Brain, method_name)
        else:
            missing.append(f"{tool_name}→{method_name}")

    if missing:
        log_warning(
            f"⚠️ [BRAIN:toolmap] {len(missing)} ابزار یافت نشد | "
            f"missing={missing[:5]}"
            + (f"... +{len(missing) - 5}" if len(missing) > 5 else "")
        )

    log_success(
        f"✅ [BRAIN:toolmap] {len(tool_map)}/{len(_TOOL_METHODS)} "
        f"ابزار ثبت شد"
    )
    return tool_map


# 🎯 TOOL_MAP را به کلاس متصل می‌کنیم (بعد از تعریف کلاس و mixinها)
Brain.TOOL_MAP = _build_tool_map()


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