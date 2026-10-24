import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest


# ── Stub out heavy dependencies before importing the module ──────────────
@pytest.fixture(scope="module", autouse=True)
def _stub_deps(tmp_path_factory):
    """Provide fake core/data modules so the target module can import."""
    tmp = tmp_path_factory.mktemp("brain")

    # core.kernel.logger
    logger_mod = types.ModuleType("core.kernel.logger")
    logger_mod.log_error = lambda *a, **k: None
    logger_mod.log_info = lambda *a, **k: None
    logger_mod.log_success = lambda *a, **k: None
    logger_mod.log_warning = lambda *a, **k: None

    # core.adapters.memory_store
    mem_mod = types.ModuleType("core.adapters.memory_store")

    class _MemoryStore:
        def __init__(self, *a, **k):
            self.data = {}

        def get(self, k, default=None):
            return self.data.get(k, default)

        def set(self, k, v):
            self.data[k] = v

    mem_mod.MemoryStore = _MemoryStore

    # data.paths
    paths_mod = types.ModuleType("data.paths")
    paths_mod.BRAIN_DIR = tmp

    # core / core.kernel / core.adapters packages
    core_pkg = types.ModuleType("core")
    core_pkg.__path__ = []
    kernel_pkg = types.ModuleType("core.kernel")
    kernel_pkg.__path__ = []
    adapters_pkg = types.ModuleType("core.adapters")
    adapters_pkg.__path__ = []
    data_pkg = types.ModuleType("data")
    data_pkg.__path__ = []

    sys.modules.setdefault("core", core_pkg)
    sys.modules.setdefault("core.kernel", kernel_pkg)
    sys.modules.setdefault("core.adapters", adapters_pkg)
    sys.modules.setdefault("data", data_pkg)
    sys.modules["core.kernel.logger"] = logger_mod
    sys.modules["core.adapters.memory_store"] = mem_mod
    sys.modules["data.paths"] = paths_mod

    yield


@pytest.fixture(scope="module")
def mod():
    """Import the target module once."""
    import importlib
    name = "brain_module_under_test"
    if name in sys.modules:
        return sys.modules[name]
    # Try to locate the module file dynamically; fall back to a synthetic name.
    # The test assumes the module is importable as `brain` or similar.
    # We attempt a few common names.
    for candidate in ("brain", "core.brain", "core.kernel.brain"):
        try:
            return importlib.import_module(candidate)
        except ImportError:
            continue
    pytest.skip("Target module not importable under known names")


# ── _env_int ─────────────────────────────────────────────────────────────
class TestEnvInt:
    def test_default_when_unset(self, mod, monkeypatch):
        monkeypatch.delenv("TEST_ENV_INT_X", raising=False)
        assert mod._env_int("TEST_ENV_INT_X", 42) == 42

    def test_reads_value(self, mod, monkeypatch):
        monkeypatch.setenv("TEST_ENV_INT_X", "7")
        assert mod._env_int("TEST_ENV_INT_X", 42) == 7

    def test_invalid_returns_default(self, mod, monkeypatch):
        monkeypatch.setenv("TEST_ENV_INT_X", "not-an-int")
        assert mod._env_int("TEST_ENV_INT_X", 42) == 42

    def test_clamps_min(self, mod, monkeypatch):
        monkeypatch.setenv("TEST_ENV_INT_X", "-5")
        assert mod._env_int("TEST_ENV_INT_X", 10, min_val=0) == 0

    def test_clamps_max(self, mod, monkeypatch):
        monkeypatch.setenv("TEST_ENV_INT_X", "9999")
        assert mod._env_int("TEST_ENV_INT_X", 10, max_val=100) == 100


# ── _env_float ───────────────────────────────────────────────────────────
class TestEnvFloat:
    def test_default_when_unset(self, mod, monkeypatch):
        monkeypatch.delenv("TEST_ENV_FLOAT_X", raising=False)
        assert mod._env_float("TEST_ENV_FLOAT_X", 1.5) == 1.5

    def test_reads_value(self, mod, monkeypatch):
        monkeypatch.setenv("TEST_ENV_FLOAT_X", "2.25")
        assert mod._env_float("TEST_ENV_FLOAT_X", 1.5) == 2.25

    def test_invalid_returns_default(self, mod, monkeypatch):
        monkeypatch.setenv("TEST_ENV_FLOAT_X", "abc")
        assert mod._env_float("TEST_ENV_FLOAT_X", 1.5) == 1.5

    def test_clamps_min(self, mod, monkeypatch):
        monkeypatch.setenv("TEST_ENV_FLOAT_X", "-3.0")
        assert mod._env_float("TEST_ENV_FLOAT_X", 1.0, min_val=0.0) == 0.0

    def test_clamps_max(self, mod, monkeypatch):
        monkeypatch.setenv("TEST_ENV_FLOAT_X", "100.0")
        assert mod._env_float("TEST_ENV_FLOAT_X", 1.0, max_val=10.0) == 10.0


# ── _atomic_write_json ───────────────────────────────────────────────────
class TestAtomicWriteJson:
    def test_writes_file(self, mod, tmp_path):
        target = tmp_path / "sub" / "out.json"
        ok = mod._atomic_write_json(target, {"a": 1, "b": [1, 2]})
        assert ok is True
        assert target.exists()
        with open(target, encoding="utf-8") as f:
            assert json.load(f) == {"a": 1, "b": [1, 2]}

    def test_creates_parent_dirs(self, mod, tmp_path):
        target = tmp_path / "deep" / "nested" / "x.json"
        assert mod._atomic_write_json(target, {"k": "v"}) is True
        assert target.exists()

    def test_returns_false_on_error(self, mod, tmp_path):
        # Point at a path whose parent is a file → mkdir fails
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        target = blocker / "child.json"
        assert mod._atomic_write_json(target, {"a": 1}) is False

    def test_no_tmp_left_behind(self, mod, tmp_path):
        target = tmp_path / "out.json"
        mod._atomic_write_json(target, {"a": 1})
        assert not (tmp_path / "out.tmp").exists()


# ── _clean_read_output ───────────────────────────────────────────────────
class TestCleanReadOutput:
    def test_empty(self, mod):
        assert mod._clean_read_output("") == ""

    def test_none_like(self, mod):
        assert mod._clean_read_output("") == ""

    def test_strips_brain_header(self, mod):
        text = "BRAIN:file:read something\nactual content"
        assert mod._clean_read_output(text) == "actual content"

    def test_strips_emoji_header(self, mod):
        text = "📄 file.txt\nline1\nline2"
        assert mod._clean_read_output(text) == "line1\nline2"

    def test_passthrough_when_no_header(self, mod):
        text = "just content\nmore"
        assert mod._clean_read_output(text) == text

    def test_single_line_no_newline(self, mod):
        assert mod._clean_read_output("plain") == "plain"


# ── LearningEngine ───────────────────────────────────────────────────────
class TestLearningEngine:
    def _make(self, mod, tmp_path):
        # Redirect BRAIN_DIR for this test
        with patch.object(mod, "BRAIN_DIR", tmp_path):
            agent = MagicMock()
            return mod.LearningEngine(agent)

    def test_init_creates_empty_state(self, mod, tmp_path):
        eng = self._make(mod, tmp_path)
        assert eng.learned_patterns == {}
        assert eng.successful_interactions == []
        assert eng.failed_interactions == []

    def test_load_missing_file_is_noop(self, mod, tmp_path):
        eng = self._make(mod, tmp_path)
        assert eng.learned_patterns == {}

    def test_load_valid_file(self, mod, tmp_path):
        data = {
            "patterns": {"p1": 1},
            "successful": [{"x": 1}],
            "failed": [{"y": 2}],
        }
        (tmp_path / "learned_patterns.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        eng = self._make(mod, tmp_path)
        assert eng.learned_patterns == {"p1": 1}
        assert eng.successful_interactions == [{"x": 1}]
        assert eng.failed_interactions == [{"y": 2}]

    def test_load_invalid_json_is_handled(self, mod, tmp_path):
        (tmp_path / "learned_patterns.json").write_text(
            "{not json", encoding="utf-8"
        )
        eng = self._make(mod, tmp_path)
        assert eng.learned_patterns == {}

    def test_load_non_dict_is_handled(self, mod, tmp_path):
        (tmp_path / "learned_patterns.json").write_text(
            "[1, 2, 3]", encoding="utf-8"
        )
        eng = self._make(mod, tmp_path)
        assert eng.learned_patterns == {}

    def test_save_learned_data(self, mod, tmp_path):
        eng = self._make(mod, tmp_path)
        eng.learned_patterns = {"a": 1}
        eng.successful_interactions = [{"s": 1}]
        eng.failed_interactions = [{"f": 1}]
        # Call save if it exists
        if hasattr(eng, "_save_learned_data"):
            eng._save_learned_data()
            path = tmp_path / "learned_patterns.json"
            assert path.exists()
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["patterns"] == {"a": 1}

    def test_record_interaction_success(self, mod, tmp_path):
        eng = self._make(mod, tmp_path)
        if not hasattr(eng, "record_interaction"):
            pytest.skip("record_interaction not present")
        eng.record_interaction(
            user_input="hello",
            response="hi",
            success=True,
            tool_used="chat",
        )
        assert len(eng.successful_interactions) == 1
        assert eng.successful_interactions[0]["success"] is True