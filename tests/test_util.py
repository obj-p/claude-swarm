"""Tests for util.run_agent (mocked SDK)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from claude_swarm.errors import SwarmError
from claude_swarm.util import run_agent


async def _async_gen(*items):
    for item in items:
        yield item


class FakeResultMessage:
    """Fake that will pass isinstance checks when we patch ResultMessage."""
    def __init__(self, result="done", is_error=False, total_cost_usd=0.01, structured_output=None):
        self.result = result
        self.is_error = is_error
        self.total_cost_usd = total_cost_usd
        self.structured_output = structured_output


class TestRunAgent:
    @pytest.mark.asyncio
    async def test_returns_result_message(self):
        msg = FakeResultMessage(result="done")
        with (
            patch("claude_swarm.util.query", return_value=_async_gen(msg)),
            patch("claude_swarm.util.ResultMessage", FakeResultMessage),
        ):
            result = await run_agent(prompt="test", options=None)
            assert result.result == "done"

    @pytest.mark.asyncio
    async def test_raises_when_no_result(self):
        class NotAResult:
            pass

        with (
            patch("claude_swarm.util.query", return_value=_async_gen(NotAResult())),
            patch("claude_swarm.util.ResultMessage", FakeResultMessage),
        ):
            with pytest.raises(SwarmError, match="no ResultMessage"):
                await run_agent(prompt="test", options=None)

    @pytest.mark.asyncio
    async def test_prompt_and_options_forwarded(self):
        msg = FakeResultMessage()
        with (
            patch("claude_swarm.util.query", return_value=_async_gen(msg)) as mock_q,
            patch("claude_swarm.util.ResultMessage", FakeResultMessage),
        ):
            opts = object()
            await run_agent(prompt="hello", options=opts)
            mock_q.assert_called_once_with(prompt="hello", options=opts)

    @pytest.mark.asyncio
    async def test_returns_last_result_message(self):
        """When stream yields multiple ResultMessages, the last one is returned."""
        msg1 = FakeResultMessage(result="first")
        msg2 = FakeResultMessage(result="second")
        with (
            patch("claude_swarm.util.query", return_value=_async_gen(msg1, msg2)),
            patch("claude_swarm.util.ResultMessage", FakeResultMessage),
        ):
            result = await run_agent(prompt="test", options=None)
            assert result.result == "second"
