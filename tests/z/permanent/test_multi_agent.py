"""تست‌های core/brain/multi_agent.py — چند Expert."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.brain import multi_agent as ma_mod
from core.brain.multi_agent import MultiAgent, get_multi_agent, reset_multi_agent


@pytest.fixture(autouse=True)
def _reset():
    reset_multi_agent()
    yield
    reset_multi_agent()


class TestInit:
    def test_defaults(self):
        m = MultiAgent()
        assert m.timeout_s == 120.0
        assert m.max_experts == 3

    def test_custom(self):
        m = MultiAgent(timeout_s=10.0, max_experts=2)
        assert m.timeout_s == 10.0
        assert m.max_experts == 2


class TestFallbackCombine:
    def test_empty_list(self):
        m = MultiAgent()
        assert m._fallback_combine([]) == ""

    def test_single(self):
        m = MultiAgent()
        result = m._fallback_combine([{"expert": "net", "answer": "hi"}])
        assert "net" in result
        assert "hi" in result

    def test_multiple(self):
        m = MultiAgent()
        result = m._fallback_combine([
            {"expert": "a", "answer": "1"},
            {"expert": "b", "answer": "2"},
        ])
        assert "a" in result and "b" in result


class TestAsk:
    @pytest.mark.asyncio
    async def test_empty_question(self):
        m = MultiAgent()
        result = await m.ask("")
        assert "خالی" in result

    @pytest.mark.asyncio
    async def test_whitespace_question(self):
        m = MultiAgent()
        result = await m.ask("   ")
        assert "خالی" in result

    @pytest.mark.asyncio
    async def test_ask_with_mocked_experts(self):
        m = MultiAgent()
        m._expert_network = AsyncMock(return_value={"expert": "network", "answer": "n"})
        m._expert_code = AsyncMock(return_value={"expert": "code", "answer": "c"})
        m._expert_experience = AsyncMock(return_value={"expert": "exp", "answer": "e"})
        m._judge = AsyncMock(return_value="final answer")

        result = await m.ask("test question")
        assert result == "final answer"

    @pytest.mark.asyncio
    async def test_ask_handles_expert_exception(self):
        m = MultiAgent()
        m._expert_network = AsyncMock(side_effect=RuntimeError("boom"))
        m._expert_code = AsyncMock(return_value={"expert": "code", "answer": "c"})
        m._expert_experience = AsyncMock(return_value={"expert": "exp", "answer": "e"})
        m._judge = AsyncMock(return_value="ok")

        result = await m.ask("q")
        assert result == "ok"


class TestSingleton:
    def test_get_returns_same(self):
        a = get_multi_agent()
        b = get_multi_agent()
        assert a is b

    def test_reset_creates_new(self):
        a = get_multi_agent()
        reset_multi_agent()
        b = get_multi_agent()
        assert a is not b
