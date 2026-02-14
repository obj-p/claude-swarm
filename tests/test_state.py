"""Tests for StateManager and persistent state models."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_swarm.config import SwarmConfig
from claude_swarm.models import RunStatus, TaskPlan, WorkerStatus, WorkerTask
from claude_swarm.state import RunState, StateManager, SwarmState, WorkerState


@pytest.fixture()
def state_mgr(tmp_path: Path) -> StateManager:
    """Create a StateManager pointing at a temporary directory."""
    return StateManager(tmp_path)


@pytest.fixture()
def sample_config(tmp_path: Path) -> SwarmConfig:
    return SwarmConfig(task="test task", repo_path=tmp_path)


class TestStateManagerIO:
    def test_load_creates_if_missing(self, state_mgr):
        state = state_mgr.load()
        assert isinstance(state, SwarmState)
        assert state.active_run is None
        assert state.runs == {}

    def test_save_load_roundtrip(self, state_mgr):
        state = SwarmState(version=1, active_run="run-1")
        state_mgr.save(state)
        loaded = state_mgr.load()
        assert loaded.active_run == "run-1"
        assert loaded.version == 1

    def test_atomic_write(self, state_mgr):
        """Save creates the file atomically (state.json exists after save)."""
        state = SwarmState()
        state_mgr.save(state)
        assert state_mgr._state_path.exists()
        # Verify it's valid JSON
        data = json.loads(state_mgr._state_path.read_text())
        assert data["version"] == 1

    def test_version_present(self, state_mgr):
        state = SwarmState()
        state_mgr.save(state)
        data = json.loads(state_mgr._state_path.read_text())
        assert "version" in data
        assert data["version"] == 1

    def test_corrupt_file_returns_fresh(self, state_mgr):
        """A corrupt state file results in a fresh SwarmState."""
        state_mgr._state_dir.mkdir(parents=True, exist_ok=True)
        state_mgr._state_path.write_text("not valid json {{{")
        state = state_mgr.load()
        assert isinstance(state, SwarmState)
        assert state.runs == {}


class TestRunLifecycle:
    def test_start_run(self, state_mgr, sample_config):
        run = state_mgr.start_run("run-1", "test task", sample_config)
        assert run.run_id == "run-1"
        assert run.task == "test task"
        assert run.status == RunStatus.PLANNING

    def test_start_run_sets_active(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test task", sample_config)
        state = state_mgr.load()
        assert state.active_run == "run-1"

    def test_status_transitions(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test", sample_config)

        state_mgr.set_run_status("run-1", RunStatus.EXECUTING)
        run = state_mgr.get_run("run-1")
        assert run.status == RunStatus.EXECUTING

        state_mgr.set_run_status("run-1", RunStatus.INTEGRATING)
        run = state_mgr.get_run("run-1")
        assert run.status == RunStatus.INTEGRATING

    def test_complete_clears_active(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test", sample_config)
        state_mgr.complete_run("run-1", pr_url="https://github.com/test/pr/1")
        state = state_mgr.load()
        assert state.active_run is None
        run = state.runs["run-1"]
        assert run.status == RunStatus.COMPLETED
        assert run.pr_url == "https://github.com/test/pr/1"

    def test_fail_records_error(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test", sample_config)
        state_mgr.fail_run("run-1", "integration failed")
        run = state_mgr.get_run("run-1")
        assert run.status == RunStatus.FAILED
        assert run.error == "integration failed"

    def test_fail_clears_active(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test", sample_config)
        state_mgr.fail_run("run-1", "error")
        state = state_mgr.load()
        assert state.active_run is None

    def test_start_when_active_warns(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "task 1", sample_config)
        # Starting a new run while one is active should mark the old one interrupted
        state_mgr.start_run("run-2", "task 2", sample_config)
        state = state_mgr.load()
        assert state.active_run == "run-2"
        assert state.runs["run-1"].status == RunStatus.INTERRUPTED

    def test_set_run_plan(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test", sample_config)
        plan = TaskPlan(
            original_task="test",
            reasoning="test",
            tasks=[WorkerTask(worker_id="w1", title="t1", description="d1")],
        )
        state_mgr.set_run_plan("run-1", plan)
        run = state_mgr.get_run("run-1")
        assert run.plan is not None
        assert run.plan.original_task == "test"
        assert len(run.plan.tasks) == 1


class TestWorkerLifecycle:
    def test_register_worker(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test", sample_config)
        state_mgr.register_worker("run-1", "w1", "Worker 1", "swarm/run-1/w1")
        run = state_mgr.get_run("run-1")
        assert "w1" in run.workers
        assert run.workers["w1"].title == "Worker 1"
        assert run.workers["w1"].branch == "swarm/run-1/w1"
        assert run.workers["w1"].status == WorkerStatus.PENDING

    def test_update_status(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test", sample_config)
        state_mgr.register_worker("run-1", "w1", "Worker 1", "swarm/run-1/w1")
        state_mgr.update_worker("run-1", "w1", status=WorkerStatus.RUNNING)
        run = state_mgr.get_run("run-1")
        assert run.workers["w1"].status == WorkerStatus.RUNNING

    def test_update_cost_duration(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test", sample_config)
        state_mgr.register_worker("run-1", "w1", "Worker 1", "swarm/run-1/w1")
        state_mgr.update_worker("run-1", "w1", cost_usd=0.05, duration_ms=1500)
        run = state_mgr.get_run("run-1")
        assert run.workers["w1"].cost_usd == 0.05
        assert run.workers["w1"].duration_ms == 1500

    def test_update_files_changed(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test", sample_config)
        state_mgr.register_worker("run-1", "w1", "Worker 1", "swarm/run-1/w1")
        state_mgr.update_worker("run-1", "w1", files_changed=["a.py", "b.py"])
        run = state_mgr.get_run("run-1")
        assert run.workers["w1"].files_changed == ["a.py", "b.py"]

    def test_update_unknown_worker_noop(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test", sample_config)
        # Should not raise
        state_mgr.update_worker("run-1", "nonexistent", status=WorkerStatus.RUNNING)

    def test_update_unknown_run_noop(self, state_mgr):
        # Should not raise
        state_mgr.update_worker("nonexistent", "w1", status=WorkerStatus.RUNNING)


class TestResumptionQueries:
    def test_active_run_none_when_empty(self, state_mgr):
        assert state_mgr.get_active_run() is None

    def test_active_run_returns_run(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test", sample_config)
        active = state_mgr.get_active_run()
        assert active is not None
        assert active.run_id == "run-1"

    def test_has_active_run(self, state_mgr, sample_config):
        assert state_mgr.has_active_run() is False
        state_mgr.start_run("run-1", "test", sample_config)
        assert state_mgr.has_active_run() is True

    def test_resumable_skips_completed(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test", sample_config)
        state_mgr.register_worker("run-1", "w1", "Worker 1", "swarm/run-1/w1")
        state_mgr.update_worker("run-1", "w1", status=WorkerStatus.COMPLETED)
        resumable = state_mgr.get_resumable_workers("run-1")
        assert len(resumable) == 0

    def test_resumable_includes_pending_and_failed(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test", sample_config)
        state_mgr.register_worker("run-1", "w1", "W1", "b1")
        state_mgr.register_worker("run-1", "w2", "W2", "b2")
        state_mgr.register_worker("run-1", "w3", "W3", "b3")
        state_mgr.update_worker("run-1", "w1", status=WorkerStatus.COMPLETED)
        state_mgr.update_worker("run-1", "w2", status=WorkerStatus.FAILED)
        # w3 remains PENDING
        resumable = state_mgr.get_resumable_workers("run-1")
        ids = {w.worker_id for w in resumable}
        assert ids == {"w2", "w3"}

    def test_get_last_interrupted_run(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test 1", sample_config)
        state_mgr.set_run_status("run-1", RunStatus.INTERRUPTED)
        run = state_mgr.get_last_interrupted_run()
        assert run is not None
        assert run.run_id == "run-1"

    def test_get_last_interrupted_none_when_clean(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test 1", sample_config)
        state_mgr.complete_run("run-1")
        run = state_mgr.get_last_interrupted_run()
        assert run is None


class TestCleanup:
    def test_clear_run(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test", sample_config)
        state_mgr.clear_run("run-1")
        state = state_mgr.load()
        assert "run-1" not in state.runs
        assert state.active_run is None

    def test_clear_all(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test 1", sample_config)
        state_mgr.start_run("run-2", "test 2", sample_config)
        state_mgr.clear_all()
        state = state_mgr.load()
        assert state.runs == {}
        assert state.active_run is None


class TestConfigSnapshot:
    def test_snapshot_stored(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test", sample_config)
        run = state_mgr.get_run("run-1")
        assert run.config_snapshot != {}

    def test_snapshot_has_key_fields(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test", sample_config)
        run = state_mgr.get_run("run-1")
        snap = run.config_snapshot
        assert "max_workers" in snap
        assert "model" in snap
        assert "max_cost" in snap
        assert "max_worker_retries" in snap
        assert snap["max_workers"] == sample_config.max_workers
        assert snap["model"] == sample_config.model


class TestMultipleInterruptedRuns:
    def test_returns_most_recent(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "task 1", sample_config)
        state_mgr.set_run_status("run-1", RunStatus.INTERRUPTED)
        state_mgr.start_run("run-2", "task 2", sample_config)
        state_mgr.set_run_status("run-2", RunStatus.INTERRUPTED)
        run = state_mgr.get_last_interrupted_run()
        assert run is not None
        assert run.run_id == "run-2"


class TestCompleteCostAccumulation:
    def test_complete_sums_worker_costs(self, state_mgr, sample_config):
        state_mgr.start_run("run-1", "test", sample_config)
        state_mgr.register_worker("run-1", "w1", "W1", "b1")
        state_mgr.register_worker("run-1", "w2", "W2", "b2")
        state_mgr.update_worker("run-1", "w1", cost_usd=0.10)
        state_mgr.update_worker("run-1", "w2", cost_usd=0.20)
        state_mgr.complete_run("run-1")
        run = state_mgr.get_run("run-1")
        assert abs(run.total_cost_usd - 0.30) < 0.001
