"""Brain — دستیار هوش مصنوعی پروژه."""
from __future__ import annotations

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
from core.brain.learning import (
    CodeReviewer,
    LearningEngine,
    SelfHealer,
    TaskPlanner,
    TestGenerator,
    WebSearch,
)
from core.brain.main import Brain, get_brain
from core.brain.memory import (
    APIMemory,
    BrainMemory,
    ProjectSemanticSearch,
    SmartCache,
)
from core.brain.tools_meta import (
    ALLOWED_DIRECT_COMMANDS,
    ALLOWED_TOOLS,
    TOOL_DEFINITIONS,
)
from core.brain.utils import (
    PersianBrainHandler,
    human_bytes,
    safe_path,
)

__all__ = [
    "Brain", "get_brain",
    "SmartCache", "APIMemory", "BrainMemory", "ProjectSemanticSearch",
    "LearningEngine", "TaskPlanner", "SelfHealer",
    "TestGenerator", "WebSearch", "CodeReviewer",
    "PersianBrainHandler", "human_bytes", "safe_path",
    "ALLOWED_TOOLS", "ALLOWED_DIRECT_COMMANDS", "TOOL_DEFINITIONS",
    "BRAIN_DIR", "MEMORY_DIR", "CACHE_DIR", "BACKUP_DIR", "HOOKS_DIR",
    "MAX_FILE_SIZE_BYTES", "MAX_CONTENT_LENGTH", "MAX_AGENT_STEPS",
    "MAX_CONTEXT_TOKENS", "CACHE_TTL_DAYS", "DEFAULT_MODEL", "MemoryStore",
]

from core.brain.memory import (
    APIMemory,
    BrainMemory,
    ProjectSemanticSearch,
    SmartCache,
)
from core.adapters.memory_store import MemoryStore
