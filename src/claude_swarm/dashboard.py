"""Real-time terminal dashboard for monitoring swarm worker execution."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from claude_swarm.coordination import CoordinationManager
    from claude_swarm.state import RunState, StateManager

logger = logging.getLogger(__name__)


def _format_elapsed(started_at: str | None, completed_at: str | None = None) -> str:
    """Format elapsed time as a human-readable string like '2m14s'.

    For running workers (no completed_at): uses now - started_at.
    For completed workers: uses completed_at - started_at.
    Returns '--' if started_at is None.
    """
    if started_at is None:
        return "--"
    try:
        start = datetime.fromisoformat(started_at)
        if completed_at is not None:
            end = datetime.fromisoformat(completed_at)
        else:
            end = datetime.now(timezone.utc)
        delta = (end - start).total_seconds()
        if delta < 0:
            return "--"
        minutes = int(delta // 60)
        seconds = int(delta % 60)
        if minutes > 0:
            return f"{minutes}m{seconds:02d}s"
        return f"{seconds}s"
    except (ValueError, TypeError):
        return "--"


def _tail_events(path: Path | None, n: int = 3) -> list[dict]:
    """Read the last N events from a JSONL file.

    Seeks from end of file for efficiency. Returns [] if path is None
    or file doesn't exist.
    """
    if path is None or not path.exists():
        return []
    try:
        size = path.stat().st_size
        if size == 0:
            return []
        with open(path, "rb") as f:
            # Read from end to find last N lines
            chunk_size = min(size, 8192)
            f.seek(-chunk_size, os.SEEK_END)
            data = f.read().decode("utf-8", errors="replace")
        # Discard partial first line if we didn't read from start of file
        if chunk_size < size:
            first_nl = data.find("\n")
            if first_nl >= 0:
                data = data[first_nl + 1:]
        lines = [line for line in data.strip().split("\n") if line.strip()]
        events = []
        for line in lines[-n:]:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events
    except (OSError, ValueError):
        return []


def _format_event(event: dict) -> str:
    """Convert an event dict to a human-readable string."""
    event_type = event.get("event", "unknown")
    worker_id = event.get("worker_id", "")

    if event_type == "worker_start":
        title = event.get("title", "")
        return f"{worker_id} started: {title}" if title else f"{worker_id} started"

    if event_type == "worker_complete":
        success = event.get("success", False)
        cost = event.get("cost_usd")
        cost_str = f" (${cost:.2f})" if cost is not None else ""
        status = "completed" if success else "failed"
        return f"{worker_id} {status}{cost_str}"

    if event_type == "worker_error":
        error = event.get("error", "unknown error")
        return f"{worker_id} error: {error}"

    if event_type == "worker_retry":
        attempt = event.get("attempt", "?")
        return f"{worker_id} retry (attempt {attempt})"

    if event_type == "plan_start":
        return "Planning started"

    if event_type == "plan_complete":
        n = event.get("num_subtasks", "?")
        return f"Plan ready: {n} subtask(s)"

    if event_type == "integration_start":
        return "Integration started"

    if event_type == "merge_result":
        success = event.get("success", False)
        return "Merge successful" if success else "Merge failed"

    if event_type == "pr_created":
        url = event.get("url", "")
        return f"PR created: {url}"

    # Fallback
    return f"{event_type}: {worker_id}" if worker_id else event_type


class SwarmDashboard:
    """Rich renderable that displays live swarm execution status.

    Implements the ``__rich__()`` protocol — returns a ``Group`` of panels
    and tables. Designed to be used with ``rich.live.Live`` for auto-refresh.
    """

    def __init__(
        self,
        state_mgr: StateManager,
        run_id: str,
        task: str,
        coord_mgr: CoordinationManager | None = None,
        events_path: Path | None = None,
    ) -> None:
        self.state_mgr = state_mgr
        self.run_id = run_id
        self.task = task
        self.coord_mgr = coord_mgr
        self.events_path = events_path
        self._last_state: RunState | None = None

    def _read_state(self):
        """Load the current RunState, falling back to cache on failure."""
        try:
            state = self.state_mgr.load()
            run = state.runs.get(self.run_id)
            if run is not None:
                self._last_state = run
            return run
        except Exception:
            logger.debug("Dashboard: state read failed, using cache")
            return self._last_state

    def _build_header(self, run) -> Panel:
        """Build the header panel with run overview."""
        status_styles = {
            "planning": "blue",
            "executing": "blue",
            "integrating": "blue",
            "completed": "green",
            "failed": "red",
            "interrupted": "yellow",
            "paused_checkpoint": "yellow",
        }
        status_val = run.status.value
        style = status_styles.get(status_val, "white")

        # Count workers by status
        total_workers = len(run.workers)
        active = sum(1 for w in run.workers.values() if w.status.value == "running")
        completed = sum(1 for w in run.workers.values() if w.status.value == "completed")
        failed = sum(1 for w in run.workers.values() if w.status.value == "failed")
        total_cost = sum(w.cost_usd or 0 for w in run.workers.values())

        elapsed = _format_elapsed(run.started_at)

        header_text = Text()
        header_text.append(f"Task: ", style="dim")
        header_text.append(f"{self.task}\n")
        header_text.append(f"Status: ", style="dim")
        header_text.append(f"{status_val}", style=style)
        header_text.append(f"  Workers: ", style="dim")
        header_text.append(f"{active} active", style="blue")
        header_text.append(f" / {completed} done", style="green")
        if failed:
            header_text.append(f" / {failed} failed", style="red")
        header_text.append(f" / {total_workers} total")
        header_text.append(f"  Cost: ", style="dim")
        header_text.append(f"${total_cost:.2f}")
        header_text.append(f"  Elapsed: ", style="dim")
        header_text.append(elapsed)

        return Panel(header_text, title="claude-swarm", border_style="blue")

    def _build_worker_table(self, run) -> Table:
        """Build worker status table."""
        table = Table(show_header=True, expand=True)
        table.add_column("Worker", style="cyan", no_wrap=True)
        table.add_column("Status")
        table.add_column("Cost", justify="right")
        table.add_column("Elapsed", justify="right")

        if not run.workers:
            table.add_row("No workers", "", "", "")
            return table

        # Get peer statuses for enriching running worker display
        peer_statuses: dict[str, str] = {}
        if self.coord_mgr is not None:
            try:
                for ps in self.coord_mgr.read_all_statuses():
                    if ps.milestone:
                        peer_statuses[ps.worker_id] = ps.milestone
            except Exception:
                pass

        status_styles = {
            "pending": "dim",
            "running": "blue",
            "completed": "green",
            "failed": "red",
        }

        for w in run.workers.values():
            ws = status_styles.get(w.status.value, "white")

            # For running workers, prefer peer milestone if available
            if w.status.value == "running" and w.worker_id in peer_statuses:
                status_text = Text(peer_statuses[w.worker_id], style="blue")
            elif w.error and w.error.startswith("Skipped:"):
                status_text = Text("skipped", style="yellow")
            else:
                status_text = Text(w.status.value, style=ws)

            cost = f"${w.cost_usd:.2f}" if w.cost_usd is not None else "-"
            elapsed = _format_elapsed(w.started_at, w.completed_at)
            table.add_row(w.worker_id, status_text, cost, elapsed)

        return table

    def _build_log_panel(self) -> Panel | None:
        """Build the event log panel showing last 3 events."""
        events = _tail_events(self.events_path)
        if not events:
            return None
        lines = [_format_event(e) for e in events]
        log_text = Text("\n".join(lines), style="dim")
        return Panel(log_text, title="Recent Events", border_style="dim")

    def __rich__(self) -> Group:
        """Render the dashboard as a Group of Rich renderables."""
        run = self._read_state()
        if run is None:
            return Group(Panel("Waiting for run data...", border_style="yellow"))

        parts = [
            self._build_header(run),
            self._build_worker_table(run),
        ]
        log_panel = self._build_log_panel()
        if log_panel is not None:
            parts.append(log_panel)

        return Group(*parts)
