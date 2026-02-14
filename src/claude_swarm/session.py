"""JSONL session recording and cost tracking."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SessionRecorder:
    """Records swarm events to JSONL log files.

    Writes events to .claude-swarm/logs/<run_id>/events.jsonl
    and a summary to metadata.json at the end.
    """

    def __init__(self, repo_path: Path, run_id: str) -> None:
        self.run_id = run_id
        self.log_dir = repo_path / ".claude-swarm" / "logs" / run_id
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._events_path = self.log_dir / "events.jsonl"
        self._start_time = time.monotonic()
        self._worker_costs: dict[str, float] = {}
        self._total_cost: float = 0.0
        self._worker_count: int = 0
        self._success_count: int = 0
        self._failure_count: int = 0

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start_time) * 1000)

    def record(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Append an event to the JSONL log."""
        event = {
            "timestamp": self._now(),
            "elapsed_ms": self._elapsed_ms(),
            "event": event_type,
            **(data or {}),
        }
        with open(self._events_path, "a") as f:
            f.write(json.dumps(event) + "\n")

    def plan_start(self, task: str) -> None:
        self.record("plan_start", {"task": task})

    def plan_complete(self, num_subtasks: int, cost_usd: float | None = None) -> None:
        if cost_usd is not None:
            self._total_cost += cost_usd
        self.record("plan_complete", {"num_subtasks": num_subtasks, "cost_usd": cost_usd})

    def worker_start(self, worker_id: str, title: str) -> None:
        self._worker_count += 1
        self.record("worker_start", {"worker_id": worker_id, "title": title})

    def worker_complete(
        self,
        worker_id: str,
        *,
        success: bool,
        cost_usd: float | None = None,
        duration_ms: int | None = None,
        files_changed: list[str] | None = None,
        summary: str | None = None,
    ) -> None:
        if cost_usd is not None:
            self._worker_costs[worker_id] = cost_usd
            self._total_cost += cost_usd
        if success:
            self._success_count += 1
        else:
            self._failure_count += 1
        self.record("worker_complete", {
            "worker_id": worker_id,
            "success": success,
            "cost_usd": cost_usd,
            "duration_ms": duration_ms,
            "files_changed": files_changed,
            "summary": summary,
        })

    def worker_error(self, worker_id: str, error: str) -> None:
        self._failure_count += 1
        self.record("worker_error", {"worker_id": worker_id, "error": error})

    def worker_retry(self, worker_id: str, attempt: int, reason: str) -> None:
        self.record("worker_retry", {"worker_id": worker_id, "attempt": attempt, "reason": reason})

    def conflict_resolution(self, *, success: bool, branches: list[str], error: str | None = None) -> None:
        self.record("conflict_resolution", {"success": success, "branches": branches, "error": error})

    def integration_start(self) -> None:
        self.record("integration_start")

    def merge_result(self, *, success: bool, branches: list[str], error: str | None = None) -> None:
        self.record("merge_result", {"success": success, "branches": branches, "error": error})

    def test_result(self, *, success: bool, command: str, output: str | None = None) -> None:
        self.record("test_result", {"success": success, "command": command, "output": output})

    def pr_created(self, url: str) -> None:
        self.record("pr_created", {"url": url})

    def write_metadata(self) -> None:
        """Write a summary metadata.json at the end of the session."""
        metadata = {
            "run_id": self.run_id,
            "total_cost_usd": self._total_cost,
            "duration_ms": self._elapsed_ms(),
            "worker_count": self._worker_count,
            "success_count": self._success_count,
            "failure_count": self._failure_count,
            "worker_costs": self._worker_costs,
        }
        with open(self.log_dir / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)
