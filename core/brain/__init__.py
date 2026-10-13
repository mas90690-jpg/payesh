"""Brain — دستیار هوش مصنوعی پروژه.

نسخهٔ ۷ (این نسخه):
  - 🎯 ادغام حافظه: brain_memory.py منبع یکتا
  - 🎯 memory.py حالا alias می‌ده
  - 🎯 PersistentMemory به brain_memory منتقل شد
  - 🎯 consolidation به main وصل شد
"""
from __future__ import annotations

# ─── Constants ───
from core.brain.constants import (
    BACKUP_DIR,
    BRAIN_DIR,
    CACHE_DIR,
    CACHE_TTL_DAYS,
    DEFAULT_MODEL,
    HOOKS_DIR,
    MAX_AGENT_STEPS,
    MAX_CONTENT_LENGTH,
    MAX_CONTEXT_TOKENS,
    MAX_FILE_SIZE_BYTES,
    MEMORY_DIR,
)

# ─── Learning ───
from core.brain.learning import (
    CodeReviewer,
    LearningEngine,
    SelfHealer,
    TaskPlanner,
    TestGenerator,
    WebSearch,
)

# ─── Main ───
from core.brain.main import Brain, get_brain, reset_brain

# ─── Memory (اصلی — brain_memory) ───
from core.brain.brain_memory import (
    BrainMemory,
    LongTermMemory,
    PersistentMemory,
    ProceduralMemory,
    SemanticMemory,
    WorkingMemory,
    get_brain_memory,
    get_persistent_memory,
    reset_brain_memory,
    reset_persistent_memory,
    HybridSearch,
)

# ─── Memory (secondary — memory.py) ───
from core.brain.memory import (
    APIMemory,
    ProjectSemanticSearch,
    SmartCache,
)

# ─── Tools ───
from core.brain.tools_meta import (
    ALLOWED_DIRECT_COMMANDS,
    ALLOWED_TOOLS,
    TOOL_DEFINITIONS,
)

# ─── Utils ───
from core.brain.utils import (
    PersianBrainHandler,
    human_bytes,
    safe_path,
)

# ─── Adapters ───
from core.adapters.memory_store import MemoryStore

# ─── Consolidation ───
from core.brain.consolidation import (
    get_stats as consolidation_stats,
    run_once as consolidation_run_once,
    start_consolidation,
    stop_consolidation,
)


__all__ = [
    # ─── Main ───
    "Brain",
    "get_brain",
    "reset_brain",

    # ─── Memory (اصلی) ───
    "BrainMemory",
    "WorkingMemory",
    "LongTermMemory",
    "ProceduralMemory",
    "SemanticMemory",
    "PersistentMemory",
    "get_brain_memory",
    "reset_brain_memory",
    "get_persistent_memory",
    "reset_persistent_memory",

    # ─── Memory (secondary) ───
    "SmartCache",
    "APIMemory",
    "ProjectSemanticSearch",

    # ─── Learning ───
    "LearningEngine",
    "TaskPlanner",
    "SelfHealer",
    "TestGenerator",
    "WebSearch",
    "CodeReviewer",

    # ─── Consolidation ───
    "start_consolidation",
    "stop_consolidation",
    "consolidation_run_once",
    "consolidation_stats",

    # ─── Utils ───
    "PersianBrainHandler",
    "human_bytes",
    "safe_path",

    # ─── Tools ───
    "ALLOWED_TOOLS",
    "ALLOWED_DIRECT_COMMANDS",
    "TOOL_DEFINITIONS",

    # ─── Constants ───
    "BRAIN_DIR",
    "MEMORY_DIR",
    "CACHE_DIR",
    "BACKUP_DIR",
    "HOOKS_DIR",
    "MAX_FILE_SIZE_BYTES",
    "MAX_CONTENT_LENGTH",
    "MAX_AGENT_STEPS",
    "MAX_CONTEXT_TOKENS",
    "CACHE_TTL_DAYS",
    "DEFAULT_MODEL",

    # ─── Adapters ───
    "MemoryStore",
]