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
  - 🎯 مجموع ابزارها: ۵۳ (قبلاً ۴۵)
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۵:
      * `_lessons` قبل از `learning_engine`.
      * `l` → `item` (E741).
      * `_background_index` reset flag.
      * tools audit (code, file, fix_fstrings).
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

# 🎯 از config (منبع یکتا)
from config import AI, AI_PRIORITY

# 🆕 از brain_memory (سیستم اصلی با ۵ حافظه)
from core.brain.brain_memory import (
    BrainMemory,
    get_brain_memory,
)
from core.brain.constants import (
    DEFAULT_MODEL,
)
from core.brain.learning import (
    CodeReviewer,
    LearningEngine,
    SelfHealer,
    TaskPlanner,
    TestGenerator,
    WebSearch,
)

# APIMemory / SmartCache / ProjectSemanticSearch هنوز از memory.py
from core.brain.memory import (
    APIMemory,
    ProjectSemanticSearch,
    SmartCache,
)
from core.brain.mixin_agent import AgentMixin
from core.brain.mixin_files import FilesMixin
from core.brain.mixin_info import InfoMixin
from core.brain.mixin_platforms import PlatformMixin
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
    # ═══ فایل (read) — ۹ ابزار ═══
    "read_file": "_read_file",
    "read_all": "_read_all",  # 🆕
    "read_chunk": "_read_chunk",  # 🆕
    "read_lines": "_read_lines",  # 🆕
    "read_range": "_read_range",  # 🆕
    "file_info": "_file_info",  # 🆕
    "file_exists": "_file_exists",  # 🆕
    "file_hash": "_file_hash",  # 🆕
    "find_files": "_find_files",  # 🆕
    "list_directory": "_list_directory",
    # ═══ فایل (write) — ۴ ابزار ═══
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
    # ═══ اجرا — ۴ ابزار ═══
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
    # ═══ تنظیمات — ۵ ابزار ═══
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
    "list_all_platforms": "_list_all_platforms",
    # 🆕 خواب مغز
    "consolidation_run": "_consolidation_run",
    "brain_status": "_brain_status",
    "proactive_diagnosis": "_proactive_diagnosis",
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
    TOOL_MAP: dict[str, Any] = {}  # noqa: RUF012 — ClassVar با _build_tool_map سازگار نیست

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
            model = (AI or {}).get(
                "model") or DEFAULT_MODEL or "deepseek-flash"

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
        self.api_key = api_key or (AI or {}).get("deepseek_api_key") or ""
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

        # ─── memory (سیستم جدید با ۵ حافظه) ───
        if use_api_memory and memory_api_url:
            # حالت API memory
            self.memory = BrainMemory(
                use_api=True,
                api_url=memory_api_url,
                api_key=memory_api_key,
            )
        else:
            # حالت پیش‌فرض — Singleton
            self.memory = get_brain_memory()

        # 🆕 وصل کردن consolidation (خواب مغز)
        self._consolidation_started = False
        try:
            import asyncio

            from core.brain.consolidation import start_consolidation

            try:
                # چک: آیا event loop فعاله؟
                asyncio.get_running_loop()
                # بله → task بساز
                self._consolidation_started = True
                # 🐛 FIX: start_consolidation خودش Task می‌سازه — create_task لازم نیست
                start_consolidation()
                log_info("🌙 [BRAIN:consolidation] به main وصل شد")
            except RuntimeError:
                # نه → وقتی event loop بالا اومد، وصل می‌شه
                log_info(
                    "ℹ️ [BRAIN:consolidation] event loop فعال نیست — منتظر اجرا")
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:consolidation] {e}")

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
            self._lessons = [item.get("value", "") for item in lessons]
            log_info(f"📚 [BRAIN:lessons] {len(self._lessons)} درس بارگذاری شد")
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:lessons] {e}")

        # 🎯 FIX: learning_engine **بعد** از _lessons
        self.learning_engine = LearningEngine(self)
        self.planner = TaskPlanner(self)
        self.healer = SelfHealer(self)
        self.test_gen = TestGenerator(self)
        self.web_search = WebSearch()
        self.reviewer = CodeReviewer(self)

        # 🆕 ProductLearner — یادگیری محصولات جدید
        try:
            from core.brain.learning import ProductLearner

            self.product_learner = ProductLearner(self)
            log_info("🧬 [BRAIN:init] ProductLearner ساخته شد")
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:init] ProductLearner: {e}")
            self.product_learner = None
        # ─── state اجرا ───
        self.busy = False
        self.current_mode = "precise"
        self.last_read_file: str | None = None
        self.tools_definition: list[dict] = self._build_tools()

        self.stats: dict[str, Any] = {
            "commands_processed": 0,
            "agents_run": 0,
            "errors": 0,
            "started_at": datetime.now().isoformat(),  # noqa: DTZ005
        }

        # 🎯 module-level flag — thread ایندکس فقط یک‌بار در عمر پروسه
        global _index_thread_started
        self._index_started = _index_thread_started
        with _index_thread_lock:
            if not _index_thread_started:
                _index_thread_started = True
                self._index_started = True
                threading.Thread(
                    target=self._background_index_safe,
                    daemon=True,
                    name="brain_index",
                ).start()
                log_info("📚 [BRAIN:init:index] thread ایندکس (در انتظار semantic)")
                
        elapsed_ms = (time.monotonic() - t0) * 1000
        client_state = "✅" if self.client else "❌"
        log_success(
            f"✅ [BRAIN:init:done] {elapsed_ms:.0f}ms | "
            f"tools={len(self.tools_definition)} | "
            f"client={client_state} ({self.model})"
        )

    def _background_index_safe(self) -> None:
        """منتظر semantic می‌ماند، بعد ایندکس می‌کند."""
        try:
            if self.semantic_search is None:
                return
            ready = self.semantic_search.wait_ready(timeout=300.0)
            if not ready:
                log_warning("⚠️ [BRAIN:init:index] semantic در ۳۰۰s آماده نشد")
                return
            if not self.semantic_search.collection:
                log_info(
                    "ℹ️ [BRAIN:init:index] semantic غیرفعال — بدون ایندکس")
                return
            self._background_index()
            log_info("✅ [BRAIN:init:index] ایندکس کامل شد")
        except Exception as e:
            log_warning(f"⚠️ [BRAIN:init:index] {type(e).__name__}: {e}")


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
        f"✅ [BRAIN:toolmap] {len(tool_map)}/{len(_TOOL_METHODS)} ابزار ثبت شد")
    return tool_map


# 🎯 TOOL_MAP را به کلاس متصل می‌کنیم (بعد از تعریف کلاس و mixinها)
Brain.TOOL_MAP = _build_tool_map()


# ═══════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════
_brain_instance: Brain | None = None
_brain_lock = threading.Lock()


# 🎯 module-level flag — thread ایندکس فقط یک‌بار در عمر پروسه
_index_thread_started = False
_index_thread_lock = threading.Lock()

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
            log_info("🏗️ [BRAIN:singleton:create] ساخت نمونهٔ اول")
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
                and getattr(
                    _brain_instance.memory,
                    "_api_memory",
                    None,
                )
                is None
            ):
                try:
                    _brain_instance.memory._api_memory = APIMemory(
                        memory_api_url, memory_api_key
                    )
                    _brain_instance.memory.use_api = True
                    updated.append("memory_api")
                except Exception as e:
                    log_warning(
                        f"⚠️ [BRAIN:singleton:mem] {
                            type(e).__name__}: {e}")
            if updated:
                log_info(f"♻️ [BRAIN:singleton:update] {updated}")
            else:
                log_info("ℹ️ [BRAIN:singleton:reuse] instance موجود برگشت")
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
