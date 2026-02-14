"""Persistent state management for swarm runs.

State is stored at <repo>/.claude-swarm/state.json and supports
atomic writes, run/worker lifecycle tracking, and resumption queries.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from claude_swarm.config import SwarmConfig
from claude_swarm.models import RunStatus, TaskPlan, WorkerStatus

logger = logging.getLogger(__name__)


class WorkerState(BaseModel):
    """Persistent state for a single worker."""

    worker_id: str
    title: str
    status: WorkerStatus = WorkerStatus.PENDING
    branch: str
    worktree_path: str | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    summary: str | None = None
    error: str | None = None
    files_changed: list[str] = Field(default_factory=list)
    attempt: int = 1
    model_used: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class RunState(BaseModel):
    """Persistent state for a single swarm run."""

    run_id: str
    task: str
    status: RunStatus = RunStatus.PLANNING
    base_branch: str = "main"
    plan: TaskPlan | None = None
    workers: dict[str, WorkerState] = Field(default_factory=dict)
    integration_branch: str | None = None
    pr_url: str | None = None
    total_cost_usd: float = 0.0
    error: str | None = None
    started_at: str
    updated_at: str
    config_snapshot: dict = Field(default_factory=dict)


class SwarmState(BaseModel):
    """Top-level persistent state containing all runs."""

    version: int = 1
    active_run: str | None = None
    runs: dict[str, RunState] = Field(default_factory=dict)


class StateManager:
    """Manages persistent state for swarm runs.

    State file lives at <repo>/.claude-swarm/state.json.
    All writes are atomic (write to temp file, then os.replace).
    """

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path.resolve()
        self._state_dir = self.repo_path / ".claude-swarm"
        self._state_path = self._state_dir / "state.json"

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # -- I/O --

    def load(self) -> SwarmState:
        """Load state from disk, creating a new empty state if missing."""
        if not self._state_path.exists():
            return SwarmState()
        try:
            data = json.loads(self._state_path.read_text())
            return SwarmState.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Corrupt state file, starting fresh: %s", e)
            return SwarmState()

    def save(self, state: SwarmState) -> None:
        """Atomically write state to disk via os.replace."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        data = state.model_dump_json(indent=2)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._state_dir), suffix=".tmp", prefix="state-"
        )
        closed = False
        try:
            os.write(fd, data.encode())
            os.close(fd)
            closed = True
            os.replace(tmp_path, str(self._state_path))
        except BaseException:
            if not closed:
                os.close(fd)
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    # -- Run lifecycle --

    def start_run(self, run_id: str, task: str, config: SwarmConfig) -> RunState:
        """Register a new run as the active run."""
        state = self.load()

        if state.active_run and state.active_run in state.runs:
            existing = state.runs[state.active_run]
            if existing.status not in (RunStatus.COMPLETED, RunStatus.FAILED):
                logger.warning(
                    "Existing active run %s (status=%s) — overriding",
                    state.active_run,
                    existing.status,
                )
                existing.status = RunStatus.INTERRUPTED
                existing.updated_at = self._now()

        now = self._now()
        run_state = RunState(
            run_id=run_id,
            task=task,
            status=RunStatus.PLANNING,
            base_branch=config.base_branch or "main",
            started_at=now,
            updated_at=now,
            config_snapshot={
                "max_workers": config.max_workers,
                "model": config.model,
                "orchestrator_model": config.orchestrator_model,
                "max_cost": config.max_cost,
                "max_worker_cost": config.max_worker_cost,
                "max_worker_retries": config.max_worker_retries,
                "escalation_model": config.escalation_model,
                "enable_escalation": config.enable_escalation,
                "resolve_conflicts": config.resolve_conflicts,
                "oversight": config.oversight,
            },
        )

        state.runs[run_id] = run_state
        state.active_run = run_id
        self.save(state)
        return run_state

    def set_run_status(self, run_id: str, status: RunStatus) -> None:
        """Update the status of a run."""
        state = self.load()
        if run_id not in state.runs:
            logger.warning("set_run_status: unknown run %s", run_id)
            return
        state.runs[run_id].status = status
        state.runs[run_id].updated_at = self._now()
        self.save(state)

    def set_run_plan(self, run_id: str, plan: TaskPlan) -> None:
        """Store the plan for a run."""
        state = self.load()
        if run_id not in state.runs:
            return
        state.runs[run_id].plan = plan
        state.runs[run_id].updated_at = self._now()
        self.save(state)

    def complete_run(self, run_id: str, *, pr_url: str | None = None) -> None:
        """Mark a run as completed and clear it as the active run."""
        state = self.load()
        if run_id not in state.runs:
            logger.warning("complete_run: unknown run %s", run_id)
            return
        run = state.runs[run_id]
        run.status = RunStatus.COMPLETED
        run.pr_url = pr_url
        run.total_cost_usd = sum(
            w.cost_usd or 0 for w in run.workers.values()
        )
        run.updated_at = self._now()
        if state.active_run == run_id:
            state.active_run = None
        self.save(state)

    def fail_run(self, run_id: str, error: str) -> None:
        """Mark a run as failed."""
        state = self.load()
        if run_id not in state.runs:
            logger.warning("fail_run: unknown run %s", run_id)
            return
        run = state.runs[run_id]
        run.status = RunStatus.FAILED
        run.error = error
        run.updated_at = self._now()
        if state.active_run == run_id:
            state.active_run = None
        self.save(state)

    # -- Worker lifecycle --

    def register_worker(
        self, run_id: str, worker_id: str, title: str, branch: str
    ) -> None:
        """Register a worker in the run's state."""
        state = self.load()
        if run_id not in state.runs:
            return
        state.runs[run_id].workers[worker_id] = WorkerState(
            worker_id=worker_id,
            title=title,
            branch=branch,
            started_at=self._now(),
        )
        state.runs[run_id].updated_at = self._now()
        self.save(state)

    def update_worker(self, run_id: str, worker_id: str, **fields) -> None:
        """Update fields on a worker's state."""
        state = self.load()
        if run_id not in state.runs:
            return
        run = state.runs[run_id]
        if worker_id not in run.workers:
            return
        worker = run.workers[worker_id]
        for key, value in fields.items():
            if hasattr(worker, key):
                setattr(worker, key, value)
        run.updated_at = self._now()
        self.save(state)

    # -- Resumption queries --

    def get_active_run(self) -> RunState | None:
        """Return the currently active run, if any."""
        state = self.load()
        if state.active_run and state.active_run in state.runs:
            return state.runs[state.active_run]
        return None

    def get_resumable_workers(self, run_id: str) -> list[WorkerState]:
        """Return workers that need to be (re-)executed: PENDING or FAILED."""
        state = self.load()
        if run_id not in state.runs:
            return []
        return [
            w
            for w in state.runs[run_id].workers.values()
            if w.status in (WorkerStatus.PENDING, WorkerStatus.FAILED)
        ]

    def has_active_run(self) -> bool:
        """Check if there is an active (non-terminal) run."""
        return self.get_active_run() is not None

    def get_run(self, run_id: str) -> RunState | None:
        """Return a specific run by ID."""
        state = self.load()
        return state.runs.get(run_id)

    def get_last_interrupted_run(self) -> RunState | None:
        """Find the most recent interrupted run."""
        state = self.load()
        interrupted = [
            r for r in state.runs.values() if r.status == RunStatus.INTERRUPTED
        ]
        if not interrupted:
            return None
        return max(interrupted, key=lambda r: r.updated_at)

    # -- Cleanup --

    def clear_run(self, run_id: str) -> None:
        """Remove a run from state."""
        state = self.load()
        state.runs.pop(run_id, None)
        if state.active_run == run_id:
            state.active_run = None
        self.save(state)

    def clear_all(self) -> None:
        """Remove all state."""
        state = SwarmState()
        self.save(state)
