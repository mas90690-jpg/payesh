"""Tests for core.brain.tools_audit module."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.brain import tools_audit as ta


# ═══════════════════════════════════════════════════════════
# _env_int
# ═══════════════════════════════════════════════════════════
class TestEnvInt:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_INT_X", raising=False)
        assert ta._env_int("TEST_INT_X", 42) == 42

    def test_reads_value(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_X", "7")
        assert ta._env_int("TEST_INT_X", 42) == 7

    def test_invalid_returns_default(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_X", "not-an-int")
        assert ta._env_int("TEST_INT_X", 42) == 42

    def test_min_clamp(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_X", "1")
        assert ta._env_int("TEST_INT_X", 42, min_val=10) == 10

    def test_max_clamp(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_X", "999")
        assert ta._env_int("TEST_INT_X", 42, max_val=100) == 100

    def test_within_range(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_X", "50")
        assert ta._env_int("TEST_INT_X", 42, min_val=10, max_val=100) == 50


# ═══════════════════════════════════════════════════════════
# _env_float
# ═══════════════════════════════════════════════════════════
class TestEnvFloat:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_FLOAT_X", raising=False)
        assert ta._env_float("TEST_FLOAT_X", 1.5) == 1.5

    def test_reads_value(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT_X", "3.14")
        assert ta._env_float("TEST_FLOAT_X", 1.5) == pytest.approx(3.14)

    def test_invalid_returns_default(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT_X", "abc")
        assert ta._env_float("TEST_FLOAT_X", 1.5) == 1.5

    def test_min_clamp(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT_X", "0.1")
        assert ta._env_float("TEST_FLOAT_X", 1.5, min_val=1.0) == 1.0

    def test_max_clamp(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT_X", "999.0")
        assert ta._env_float("TEST_FLOAT_X", 1.5, max_val=10.0) == 10.0


# ═══════════════════════════════════════════════════════════
# _run_tool
# ═══════════════════════════════════════════════════════════
class TestRunTool:
    def test_success_counts_nonempty_lines(self):
        fake = MagicMock()
        fake.stdout = "line1\nline2\n\n---\nline3\n"
        fake.stderr = ""
        fake.returncode = 0
        with patch.object(ta.subprocess, "run", return_value=fake):
            res = ta._run_tool("pyflakes", ["x.py"])
        assert res["ok"] is True
        assert res["count"] == 3
        assert res["exit_code"] == 0
        assert "line1" in res["output"]

    def test_combines_stdout_and_stderr(self):
        fake = MagicMock()
        fake.stdout = "out\n"
        fake.stderr = "err\n"
        fake.returncode = 1
        with patch.object(ta.subprocess, "run", return_value=fake):
            res = ta._run_tool("flake8", ["x.py"])
        assert "out" in res["output"]
        assert "err" in res["output"]

    def test_timeout(self):
        with patch.object(
            ta.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1),
        ):
            res = ta._run_tool("pyflakes", ["x.py"], timeout=1)
        assert res["ok"] is False
        assert "timeout" in res["error"]
        assert res["count"] == 0

    def test_file_not_found(self):
        with patch.object(
            ta.subprocess, "run",
            side_effect=FileNotFoundError(),
        ):
            res = ta._run_tool("pyflakes", ["x.py"])
        assert res["ok"] is False
        assert "نصب نیست" in res["error"]

    def test_generic_exception(self):
        with patch.object(
            ta.subprocess, "run",
            side_effect=RuntimeError("boom"),
        ):
            res = ta._run_tool("pyflakes", ["x.py"])
        assert res["ok"] is False
        assert "RuntimeError" in res["error"]

    def test_output_preview_truncated(self, monkeypatch):
        monkeypatch.setattr(ta, "_MAX_OUTPUT_PREVIEW", 10)
        fake = MagicMock()
        fake.stdout = "a" * 100
        fake.stderr = ""
        fake.returncode = 0
        with patch.object(ta.subprocess, "run", return_value=fake):
            res = ta._run_tool("pyflakes", ["x.py"])
        assert len(res["output_preview"]) == 10
        assert len(res["output"]) == 100


# ═══════════════════════════════════════════════════════════
# _categorize_pyflakes
# ═══════════════════════════════════════════════════════════
class TestCategorizePyflakes:
    def test_empty(self):
        cats = ta._categorize_pyflakes("")
        assert all(v == [] for v in cats.values())

    def test_unused_import(self):
        out = "x.py:1: 'os' imported but unused"
        cats = ta._categorize_pyflakes(out)
        assert len(cats["unused_imports"]) == 1

    def test_unused_variable(self):
        out = "x.py:2: local variable 'y' is assigned to but never used"
        cats = ta._categorize_pyflakes(out)
        assert len(cats["unused_variables"]) == 1

    def test_undefined_name(self):
        out = "x.py:3: undefined name 'foo'"
        cats = ta._categorize_pyflakes(out)
        assert len(cats["undefined_names"]) == 1

    def test_fstring_no_placeholder(self):
        out = "x.py:4: f-string is missing placeholders"
        cats = ta._categorize_pyflakes(out)
        assert len(cats["f_string_no_placeholder"]) == 1

    def test_redefinition(self):
        out = "x.py:5: redefinition of unused 'foo' from line 1"
        cats = ta._categorize_pyflakes(out)
        assert len(cats["redefinition"]) == 1

    def test_other(self):
        out = "x.py:6: something weird"
        cats = ta._categorize_pyflakes(out)
        assert len(cats["other"]) == 1

    def test_multiple_lines(self):
        out = (
            "a.py:1: 'os' imported but unused\n"
            "a.py:2: undefined name 'x'\n"
            "a.py:3: f-string is missing placeholders\n"
        )
        cats = ta._categorize_pyflakes(out)
        assert len(cats["unused_imports"]) == 1
        assert len(cats["undefined_names"]) == 1
        assert len(cats["f_string_no_placeholder"]) == 1


# ═══════════════════════════════════════════════════════════
# _categorize_flake8
# ═══════════════════════════════════════════════════════════
class TestCategorizeFlake8:
    def test_empty(self):
        cats = ta._categorize_flake8("")
        assert all(v == [] for v in cats.values())

    def test_style_e(self):
        out = "x.py:1:1: E501 line too long"
        cats = ta._categorize_flake8(out)
        assert len(cats["style"]) == 1

    def test_style_w(self):
        out = "x.py:1:1: W291 trailing whitespace"
        cats = ta._categorize_flake8(out)
        assert len(cats["style"]) == 1

    def test_other(self):
        out = "x.py:1:1: X999 unknown code"
        cats = ta._categorize_flake8(out)
        assert len(cats["other"]) == 1


# ═══════════════════════════════════════════════════════════
# audit_code / audit_file
# ═══════════════════════════════════════════════════════════
class TestAuditCode:
    def test_returns_dict_with_expected_keys(self):
        with patch.object(ta, "_run_tool", return_value={
            "ok": True, "count": 0, "output": "", "output_preview": "",
            "exit_code": 0,
        }):
            res = ta.audit_code(".")
        assert isinstance(res, dict)
        assert "ok" in res
        assert "summary" in res
        assert "details" in res

    def test_handles_tool_failure(self):
        with patch.object(ta, "_run_tool", return_value={
            "ok": False, "error": "nope", "count": 0,
        }):
            res = ta.audit_code(".")
        assert isinstance(res, dict)


class TestAuditFile:
    def test_nonexistent_file(self, tmp_path):
        missing = tmp_path / "nope.py"
        res = ta.audit_file(str(missing))
        assert isinstance(res, dict)
        assert res.get("ok") is False

    def test_existing_file(self, tmp_path):
        f = tmp_path / "ok.py"
        f.write_text("x = 1\n")
        with patch.object(ta, "_run_tool", return_value={
            "ok": True, "count": 0, "output": "", "output_preview": "",
            "exit_code": 0,
        }):
            res = ta.audit_file(str(f))
        assert isinstance(res, dict)


# ═════════════════════