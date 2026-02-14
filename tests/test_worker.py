"""Tests for worker spawning (mocked run_agent)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_swarm.errors import WorkerError
from claude_swarm.models import WorkerTask
from claude_swarm.worker import spawn_worker


def _make_task(**kwargs) -> WorkerTask:
    defaults = dict(
        worker_id="w1",
        title="Test task",
        description="Do the thing",
        target_files=["file.py"],
        acceptance_criteria=["works"],
    )
    defaults.update(kwargs)
    return WorkerTask(**defaults)


class TestSpawnWorker:
    @pytest.mark.asyncio
    async def test_success_path(self, make_result_message, tmp_path):
        msg = make_result_message(result="All done", total_cost_usd=0.05)
        with patch("claude_swarm.worker.run_agent", new_callable=AsyncMock, return_value=msg):
            result = await spawn_worker(_make_task(), tmp_path)
            assert result.success is True
            assert result.cost_usd == 0.05
            assert result.duration_ms is not None
            assert result.summary == "All done"

    @pytest.mark.asyncio
    async def test_error_result(self, make_result_message, tmp_path):
        msg = make_result_message(result="Something broke", is_error=True, total_cost_usd=0.02)
        with patch("claude_swarm.worker.run_agent", new_callable=AsyncMock, return_value=msg):
            result = await spawn_worker(_make_task(), tmp_path)
            assert result.success is False
            assert result.error == "Something broke"

    @pytest.mark.asyncio
    async def test_exception_raises_worker_error(self, tmp_path):
        with patch("claude_swarm.worker.run_agent", new_callable=AsyncMock, side_effect=RuntimeError("network")):
            with pytest.raises(WorkerError, match="w1"):
                await spawn_worker(_make_task(), tmp_path)

    @pytest.mark.asyncio
    async def test_system_prompt_contains_task_fields(self, make_result_message, tmp_path):
        msg = make_result_message()
        with patch("claude_swarm.worker.run_agent", new_callable=AsyncMock, return_value=msg) as mock_run:
            await spawn_worker(_make_task(description="Fix the bug"), tmp_path)
            # Check the options passed to run_agent
            options = mock_run.call_args.kwargs["options"]
            assert "Fix the bug" in options.system_prompt
