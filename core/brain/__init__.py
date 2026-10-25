"""Brain — دستیار هوش مصنوعی پروژه.

نسخهٔ ۷ (این نسخه):
  - 🎯 ادغام حافظه: brain_memory.py منبع یکتا
  - 🎯 memory.py حالا alias می‌ده
  - 🎯 PersistentMemory به brain_memory منتقل شد
  - 🎯 consolidation به main وصل شد
"""
from __future__ import annotations

# ─── Adapters ───
from core.adapters.memory_store import MemoryStore

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
)

# ─── Consolidation ───
from core.brain.consolidation import (
    get_stats as consolidation_stats,
)
from core.brain.consolidation import (
    run_once as consolidation_run_once,
)
from core.brain.consolidation import (
    start_consolidation,
    stop_consolidation,
)

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

__all__ = [
    "ALLOWED_DIRECT_COMMANDS",
    # ─── Tools ───
    "ALLOWED_TOOLS",
    "BACKUP_DIR",
    # ─── Constants ───
    "BRAIN_DIR",
    "CACHE_DIR",
    "CACHE_TTL_DAYS",
    "DEFAULT_MODEL",
    "HOOKS_DIR",
    "MAX_AGENT_STEPS",
    "MAX_CONTENT_LENGTH",
    "MAX_CONTEXT_TOKENS",
    "MAX_FILE_SIZE_BYTES",
    "MEMORY_DIR",
    "TOOL_DEFINITIONS",
    "APIMemory",
    # ─── Main ───
    "Brain",
    # ─── Memory (اصلی) ───
    "BrainMemory",
    "CodeReviewer",
    # ─── Learning ───
    "LearningEngine",
    "LongTermMemory",
    # ─── Adapters ───
    "MemoryStore",
    # ─── Utils ───
    "PersianBrainHandler",
    "PersistentMemory",
    "ProceduralMemory",
    "ProjectSemanticSearch",
    "SelfHealer",
    "SemanticMemory",
    # ─── Memory (secondary) ───
    "SmartCache",
    "TaskPlanner",
    "TestGenerator",
    "WebSearch",
    "WorkingMemory",
    "consolidation_run_once",
    "consolidation_stats",
    "get_brain",
    "get_brain_memory",
    "get_persistent_memory",
    "human_bytes",
    "reset_brain",
    "reset_brain_memory",
    "reset_persistent_memory",
    "safe_path",
    # ─── Consolidation ───
    "start_consolidation",
    "stop_consolidation",
]
