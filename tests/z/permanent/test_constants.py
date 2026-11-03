"""تست‌های core/brain/constants.py — ثابت‌ها."""

from __future__ import annotations

from core.brain import constants


class TestConstants:
    def test_project_base_is_str(self):
        assert isinstance(constants.PROJECT_BASE, str)
        assert constants.PROJECT_BASE

    def test_max_file_size_positive(self):
        assert constants.MAX_FILE_SIZE_BYTES > 0

    def test_max_content_length_positive(self):
        assert constants.MAX_CONTENT_LENGTH > 0

    def test_max_agent_steps_positive(self):
        assert constants.MAX_AGENT_STEPS > 0

    def test_timeouts_positive(self):
        assert constants.DEFAULT_TIMEOUT_S > 0
        assert constants.PY_EXEC_TIMEOUT_S > 0
        assert constants.TESTS_TIMEOUT_S > 0

    def test_chunk_size_positive(self):
        assert constants.CHUNK_SIZE > 0

    def test_chunk_auto_threshold_positive(self):
        assert constants.CHUNK_AUTO_THRESHOLD > 0

    def test_disabled_tools_is_collection(self):
        assert hasattr(constants.DISABLED_TOOLS, "__iter__")

    def test_get_stats_returns_dict(self):
        stats = constants.get_stats()
        assert isinstance(stats, dict)
        assert len(stats) > 0

    def test_get_stats_has_key_values(self):
        stats = constants.get_stats()
        assert "MAX_AGENT_STEPS" in stats or "max_agent_steps" in str(stats).lower()

    def test_all_exports_exist(self):
        for name in constants.__all__:
            assert hasattr(constants, name), f"missing: {name}"

    def test_env_int_helper(self):
        assert constants._env_int("__NONEXISTENT__", 7) == 7

    def test_env_float_helper(self):
        assert constants._env_float("__NONEXISTENT__", 1.5) == 1.5

    def test_env_str_helper(self):
        assert constants._env_str("__NONEXISTENT__", "x") == "x"
