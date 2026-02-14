"""Tests for Orchestrator (mocked run_agent + real git)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_swarm.config import SwarmConfig
from claude_swarm.errors import PlanningError
from claude_swarm.models import RunStatus, TaskPlan, WorkerResult, WorkerTask
from claude_swarm.orchestrator import Orchestrator
from claude_swarm.state import StateManager


def _make_orchestrator(tmp_git_repo, **overrides) -> Orchestrator:
    defaults = dict(task="test task", repo_path=tmp_git_repo)
    defaults.update(overrides)
    config = SwarmConfig(**defaults)
    return Orchestrator(config)


class TestPlanParsing:
    @pytest.mark.asyncio
    async def test_parse_from_structured_output(self, tmp_git_repo, make_result_message, sample_task_plan_dict):
        orch = _make_orchestrator(tmp_git_repo)
        msg = make_result_message(structured_output=sample_task_plan_dict)
        with patch("claude_swarm.orchestrator.run_agent", new_callable=AsyncMock, return_value=msg):
            plan = await orch._plan_task()
            assert isinstance(plan, TaskPlan)
            assert plan.original_task == "Add logging"

    @pytest.mark.asyncio
    async def test_parse_fallback_from_result_json(self, tmp_git_repo, make_result_message, sample_task_plan_dict):
        orch = _make_orchestrator(tmp_git_repo)
        msg = make_result_message(result=json.dumps(sample_task_plan_dict), structured_output=None)
        with patch("claude_swarm.orchestrator.run_agent", new_callable=AsyncMock, return_value=msg):
            plan = await orch._plan_task()
            assert isinstance(plan, TaskPlan)

    @pytest.mark.asyncio
    async def test_max_workers_truncates(self, tmp_git_repo, make_result_message):
        plan_dict = {
            "original_task": "big task",
            "reasoning": "many pieces",
            "tasks": [
                {"worker_id": f"w{i}", "title": f"t{i}", "description": f"d{i}"}
                for i in range(10)
            ],
        }
        orch = _make_orchestrator(tmp_git_repo, max_workers=2)
        msg = make_result_message(structured_output=plan_dict)
        with patch("claude_swarm.orchestrator.run_agent", new_callable=AsyncMock, return_value=msg):
            plan = await orch._plan_task()
            assert len(plan.tasks) == 2

    @pytest.mark.asyncio
    async def test_is_error_raises_planning_error(self, tmp_git_repo, make_result_message):
        orch = _make_orchestrator(tmp_git_repo)
        msg = make_result_message(is_error=True, result="agent failed")
        with patch("claude_swarm.orchestrator.run_agent", new_callable=AsyncMock, return_value=msg):
            with pytest.raises(PlanningError, match="Planning agent failed"):
                await orch._plan_task()


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_stops_after_plan(self, tmp_git_repo, make_result_message, sample_task_plan_dict):
        orch = _make_orchestrator(tmp_git_repo, dry_run=True)
        msg = make_result_message(structured_output=sample_task_plan_dict)
        with patch("claude_swarm.orchestrator.run_agent", new_callable=AsyncMock, return_value=msg):
            result = await orch.run()
            assert result.worker_results == []
            assert result.run_id == orch.run_id


class TestPrintSummary:
    def test_print_summary_no_crash(self, tmp_git_repo):
        orch = _make_orchestrator(tmp_git_repo)
        results = [
            WorkerResult(worker_id="w1", success=True, cost_usd=0.05, duration_ms=1000, files_changed=["a.py"]),
            WorkerResult(worker_id="w2", success=False, error="failed"),
        ]
        # Should not raise
        orch._print_summary(results, total_cost=0.05, duration_ms=2000, pr_url=None)


class TestPlanErrorPaths:
    @pytest.mark.asyncio
    async def test_malformed_json_raises_planning_error(self, tmp_git_repo, make_result_message):
        orch = _make_orchestrator(tmp_git_repo)
        msg = make_result_message(result="not valid json {{{", structured_output=None)
        with patch("claude_swarm.orchestrator.run_agent", new_callable=AsyncMock, return_value=msg):
            with pytest.raises(PlanningError, match="Failed to parse plan"):
                await orch._plan_task()

    @pytest.mark.asyncio
    async def test_no_output_raises_planning_error(self, tmp_git_repo, make_result_message):
        orch = _make_orchestrator(tmp_git_repo)
        msg = make_result_message(result=None, structured_output=None)
        with patch("claude_swarm.orchestrator.run_agent", new_callable=AsyncMock, return_value=msg):
            with pytest.raises(PlanningError, match="no output"):
                await orch._plan_task()


class TestExecuteWorkers:
    @pytest.mark.asyncio
    async def test_workers_spawned_and_results_collected(self, tmp_git_repo):
        """Mock spawn_worker, verify results are collected for each task."""
        orch = _make_orchestrator(tmp_git_repo, max_workers=2)
        plan = TaskPlan(
            original_task="test",
            reasoning="test",
            tasks=[
                WorkerTask(worker_id="w1", title="t1", description="d1"),
                WorkerTask(worker_id="w2", title="t2", description="d2"),
            ],
        )

        async def fake_spawn(task, path, **kwargs):
            return WorkerResult(
                worker_id=task.worker_id, success=True,
                cost_usd=0.01, duration_ms=100, summary="ok",
            )

        with patch("claude_swarm.orchestrator.spawn_worker_with_retry", side_effect=fake_spawn):
            results = await orch._execute_workers(plan)

        assert len(results) == 2
        assert all(r.success for r in results)
        assert {r.worker_id for r in results} == {"w1", "w2"}

    @pytest.mark.asyncio
    async def test_worker_exception_converted_to_result(self, tmp_git_repo):
        """When spawn_worker raises, the exception is caught and converted to a failed WorkerResult."""
        orch = _make_orchestrator(tmp_git_repo, max_workers=1)
        plan = TaskPlan(
            original_task="test",
            reasoning="test",
            tasks=[
                WorkerTask(worker_id="w1", title="t1", description="d1"),
            ],
        )

        async def failing_spawn(task, path, **kwargs):
            raise RuntimeError("agent crashed")

        with patch("claude_swarm.orchestrator.spawn_worker_with_retry", side_effect=failing_spawn):
            results = await orch._execute_workers(plan)

        assert len(results) == 1
        assert results[0].success is False
        assert "agent crashed" in results[0].error


class TestStateIntegration:
    @pytest.mark.asyncio
    async def test_dry_run_records_state(self, tmp_git_repo, make_result_message, sample_task_plan_dict):
        orch = _make_orchestrator(tmp_git_repo, dry_run=True)
        msg = make_result_message(structured_output=sample_task_plan_dict)
        with patch("claude_swarm.orchestrator.run_agent", new_callable=AsyncMock, return_value=msg):
            await orch.run()
        mgr = StateManager(tmp_git_repo)
        run = mgr.get_run(orch.run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_execute_records_worker_states(self, tmp_git_repo):
        orch = _make_orchestrator(tmp_git_repo, max_workers=2)
        # Must start_run first so state exists for worker registration
        orch.state_mgr.start_run(orch.run_id, "test", orch.config)
        plan = TaskPlan(
            original_task="test",
            reasoning="test",
            tasks=[
                WorkerTask(worker_id="w1", title="t1", description="d1"),
            ],
        )

        async def fake_spawn(task, path, **kwargs):
            return WorkerResult(
                worker_id=task.worker_id, success=True,
                cost_usd=0.01, duration_ms=100, summary="ok",
            )

        with patch("claude_swarm.orchestrator.spawn_worker_with_retry", side_effect=fake_spawn):
            await orch._execute_workers(plan)

        mgr = StateManager(tmp_git_repo)
        run = mgr.get_run(orch.run_id)
        assert run is not None
        assert "w1" in run.workers
        assert run.workers["w1"].status.value == "completed"

    @pytest.mark.asyncio
    async def test_cleanup_marks_interrupted(self, tmp_git_repo):
        orch = _make_orchestrator(tmp_git_repo)
        orch.state_mgr.start_run(orch.run_id, "test", orch.config)
        await orch.cleanup()
        run = orch.state_mgr.get_run(orch.run_id)
        assert run is not None
        assert run.status == RunStatus.INTERRUPTED
