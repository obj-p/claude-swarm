"""Tests for worker retry and model escalation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_swarm.models import WorkerResult, WorkerTask
from claude_swarm.worker import spawn_worker_with_retry


def _make_task() -> WorkerTask:
    return WorkerTask(worker_id="w1", title="Test task", description="Do the thing")


def _ok_result(**kwargs) -> WorkerResult:
    return WorkerResult(worker_id="w1", success=True, summary="done", **kwargs)


def _fail_result(**kwargs) -> WorkerResult:
    return WorkerResult(worker_id="w1", success=False, error="something broke", **kwargs)


class TestRetryBasics:
    @pytest.mark.asyncio
    async def test_single_attempt_success(self, tmp_path):
        """Success on first attempt — no retry needed."""
        mock_spawn = AsyncMock(return_value=_ok_result())

        with patch("claude_swarm.worker._spawn_single_attempt", mock_spawn):
            result = await spawn_worker_with_retry(_make_task(), tmp_path, max_retries=2)

        assert result.success is True
        assert result.attempt == 1
        mock_spawn.assert_called_once()

    @pytest.mark.asyncio
    async def test_first_fails_second_succeeds(self, tmp_path):
        """First attempt fails, second succeeds."""
        mock_spawn = AsyncMock(side_effect=[_fail_result(), _ok_result()])

        with patch("claude_swarm.worker._spawn_single_attempt", mock_spawn):
            result = await spawn_worker_with_retry(_make_task(), tmp_path, max_retries=2)

        assert result.success is True
        assert result.attempt == 2
        assert mock_spawn.call_count == 2

    @pytest.mark.asyncio
    async def test_all_attempts_fail(self, tmp_path):
        """All attempts fail — returns last failed result."""
        mock_spawn = AsyncMock(side_effect=[_fail_result(), _fail_result(), _fail_result()])

        with patch("claude_swarm.worker._spawn_single_attempt", mock_spawn):
            result = await spawn_worker_with_retry(_make_task(), tmp_path, max_retries=3)

        assert result.success is False
        assert result.attempt == 3
        assert mock_spawn.call_count == 3

    @pytest.mark.asyncio
    async def test_max_retries_one_no_retry(self, tmp_path):
        """max_retries=1 means only one attempt, no retry."""
        mock_spawn = AsyncMock(return_value=_fail_result())

        with patch("claude_swarm.worker._spawn_single_attempt", mock_spawn):
            result = await spawn_worker_with_retry(_make_task(), tmp_path, max_retries=1)

        assert result.success is False
        assert result.attempt == 1
        mock_spawn.assert_called_once()


class TestModelEscalation:
    @pytest.mark.asyncio
    async def test_escalation_on_retry(self, tmp_path):
        """First attempt uses base model, retry escalates."""
        mock_spawn = AsyncMock(side_effect=[_fail_result(), _ok_result()])

        with patch("claude_swarm.worker._spawn_single_attempt", mock_spawn):
            result = await spawn_worker_with_retry(
                _make_task(), tmp_path,
                model="sonnet", max_retries=2,
                escalation_model="opus", enable_escalation=True,
            )

        assert result.success is True
        assert result.model_used == "opus"
        # First call should use sonnet
        first_call_kwargs = mock_spawn.call_args_list[0].kwargs
        assert first_call_kwargs["model"] == "sonnet"
        # Second call should use opus
        second_call_kwargs = mock_spawn.call_args_list[1].kwargs
        assert second_call_kwargs["model"] == "opus"

    @pytest.mark.asyncio
    async def test_no_escalation_flag(self, tmp_path):
        """enable_escalation=False keeps same model on retry."""
        mock_spawn = AsyncMock(side_effect=[_fail_result(), _ok_result()])

        with patch("claude_swarm.worker._spawn_single_attempt", mock_spawn):
            result = await spawn_worker_with_retry(
                _make_task(), tmp_path,
                model="sonnet", max_retries=2,
                enable_escalation=False,
            )

        assert result.success is True
        assert result.model_used == "sonnet"
        second_call_kwargs = mock_spawn.call_args_list[1].kwargs
        assert second_call_kwargs["model"] == "sonnet"


class TestRetryContext:
    @pytest.mark.asyncio
    async def test_error_context_included_in_retry(self, tmp_path):
        """Error from first attempt is passed as extra_context on retry."""
        mock_spawn = AsyncMock(side_effect=[_fail_result(), _ok_result()])

        with patch("claude_swarm.worker._spawn_single_attempt", mock_spawn):
            await spawn_worker_with_retry(_make_task(), tmp_path, max_retries=2)

        # First call should have no extra context
        first_call_kwargs = mock_spawn.call_args_list[0].kwargs
        assert first_call_kwargs["extra_context"] == ""
        # Second call should include error context
        second_call_kwargs = mock_spawn.call_args_list[1].kwargs
        assert "something broke" in second_call_kwargs["extra_context"]
        assert "Previous Attempt Failed" in second_call_kwargs["extra_context"]

    @pytest.mark.asyncio
    async def test_result_fields_set_correctly(self, tmp_path):
        """Result has correct attempt and model_used fields."""
        mock_spawn = AsyncMock(return_value=_ok_result())

        with patch("claude_swarm.worker._spawn_single_attempt", mock_spawn):
            result = await spawn_worker_with_retry(
                _make_task(), tmp_path,
                model="sonnet", max_retries=1,
            )

        assert result.attempt == 1
        assert result.model_used == "sonnet"
