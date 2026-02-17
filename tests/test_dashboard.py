"""Tests for the real-time terminal dashboard."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from rich.console import Console

from claude_swarm.dashboard import (
    SwarmDashboard,
    _format_elapsed,
    _format_event,
    _tail_events,
)


# ── _format_elapsed ────────────────────────────────────────────────


class TestFormatElapsed:
    def test_none_returns_dash(self):
        assert _format_elapsed(None) == "--"

    def test_seconds_only(self):
        now = datetime.now(timezone.utc)
        started = (now - timedelta(seconds=5)).isoformat()
        result = _format_elapsed(started)
        assert result in ("5s", "6s")  # allow for timing

    def test_minutes_formatting(self):
        now = datetime.now(timezone.utc)
        started = (now - timedelta(minutes=2, seconds=14)).isoformat()
        result = _format_elapsed(started)
        assert result.startswith("2m")

    def test_completed_worker_uses_delta(self):
        started = "2025-01-01T10:00:00+00:00"
        completed = "2025-01-01T10:02:30+00:00"
        result = _format_elapsed(started, completed)
        assert result == "2m30s"

    def test_zero_seconds(self):
        ts = "2025-01-01T10:00:00+00:00"
        result = _format_elapsed(ts, ts)
        assert result == "0s"

    def test_invalid_timestamp(self):
        assert _format_elapsed("not-a-date") == "--"


# ── _tail_events ───────────────────────────────────────────────────


class TestTailEvents:
    def test_nonexistent_file(self):
        assert _tail_events(Path("/tmp/nonexistent-events.jsonl")) == []

    def test_none_path(self):
        assert _tail_events(None) == []

    def test_last_n_from_many(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        lines = []
        for i in range(10):
            lines.append(json.dumps({"event": f"event-{i}", "i": i}))
        events_file.write_text("\n".join(lines) + "\n")

        result = _tail_events(events_file, n=3)
        assert len(result) == 3
        assert result[0]["i"] == 7
        assert result[1]["i"] == 8
        assert result[2]["i"] == 9

    def test_fewer_than_n(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            json.dumps({"event": "only-one"}) + "\n"
        )
        result = _tail_events(events_file, n=5)
        assert len(result) == 1
        assert result[0]["event"] == "only-one"

    def test_empty_file(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text("")
        assert _tail_events(events_file) == []


# ── _format_event ──────────────────────────────────────────────────


class TestFormatEvent:
    def test_worker_start(self):
        result = _format_event({"event": "worker_start", "worker_id": "w-1", "title": "Add tests"})
        assert "w-1" in result
        assert "started" in result
        assert "Add tests" in result

    def test_worker_complete_success(self):
        result = _format_event({
            "event": "worker_complete",
            "worker_id": "w-1",
            "success": True,
            "cost_usd": 0.15,
        })
        assert "w-1" in result
        assert "completed" in result
        assert "$0.15" in result

    def test_worker_complete_failure(self):
        result = _format_event({
            "event": "worker_complete",
            "worker_id": "w-1",
            "success": False,
            "cost_usd": 0.05,
        })
        assert "failed" in result

    def test_worker_complete_no_cost(self):
        result = _format_event({
            "event": "worker_complete",
            "worker_id": "w-1",
            "success": True,
        })
        assert "completed" in result
        assert "$" not in result

    def test_unknown_event(self):
        result = _format_event({"event": "some_custom_event", "worker_id": "w-2"})
        assert "some_custom_event" in result

    def test_unknown_event_no_worker(self):
        result = _format_event({"event": "some_custom_event"})
        assert "some_custom_event" in result

    def test_plan_complete(self):
        result = _format_event({"event": "plan_complete", "num_subtasks": 4})
        assert "4" in result
        assert "Plan ready" in result

    def test_pr_created(self):
        result = _format_event({"event": "pr_created", "url": "https://github.com/test/pr/1"})
        assert "PR created" in result


# ── SwarmDashboard rendering ───────────────────────────────────────


def _make_mock_state_mgr(run_id, workers=None, status="executing", task="test task", started_at=None):
    """Create a mock StateManager that returns a RunState-like object."""
    from claude_swarm.models import RunStatus, WorkerStatus

    if started_at is None:
        started_at = datetime.now(timezone.utc).isoformat()

    class FakeWorker:
        def __init__(self, wid, ws="running", cost=None, error=None, s_at=None, c_at=None):
            self.worker_id = wid
            self.title = f"Task for {wid}"
            self.status = WorkerStatus(ws)
            self.cost_usd = cost
            self.duration_ms = None
            self.summary = None
            self.error = error
            self.files_changed = []
            self.started_at = s_at or started_at
            self.completed_at = c_at

    class FakeRun:
        def __init__(self):
            self.run_id = run_id
            self.task = task
            self.status = RunStatus(status)
            self.started_at = started_at
            self.workers = {}
            if workers:
                for w in workers:
                    fw = FakeWorker(**w)
                    self.workers[fw.worker_id] = fw

    class FakeState:
        def __init__(self):
            self.runs = {run_id: FakeRun()}

    mgr = MagicMock()
    mgr.load.return_value = FakeState()
    return mgr


class TestSwarmDashboardRendering:
    def test_renders_without_crash(self):
        mgr = _make_mock_state_mgr("run-1", workers=[{"wid": "w-1"}])
        dash = SwarmDashboard(state_mgr=mgr, run_id="run-1", task="Test task")
        c = Console(record=True, width=120)
        c.print(dash)
        output = c.export_text()
        assert len(output) > 0

    def test_output_contains_task_name(self):
        mgr = _make_mock_state_mgr("run-1", workers=[{"wid": "w-1"}], task="Implement feature X")
        dash = SwarmDashboard(state_mgr=mgr, run_id="run-1", task="Implement feature X")
        c = Console(record=True, width=120)
        c.print(dash)
        output = c.export_text()
        assert "Implement feature X" in output

    def test_output_contains_worker_ids(self):
        mgr = _make_mock_state_mgr("run-1", workers=[
            {"wid": "worker-1"},
            {"wid": "worker-2"},
        ])
        dash = SwarmDashboard(state_mgr=mgr, run_id="run-1", task="test")
        c = Console(record=True, width=120)
        c.print(dash)
        output = c.export_text()
        assert "worker-1" in output
        assert "worker-2" in output

    def test_output_contains_cost(self):
        mgr = _make_mock_state_mgr("run-1", workers=[
            {"wid": "w-1", "ws": "completed", "cost": 1.25},
        ])
        dash = SwarmDashboard(state_mgr=mgr, run_id="run-1", task="test")
        c = Console(record=True, width=120)
        c.print(dash)
        output = c.export_text()
        assert "$1.25" in output

    def test_missing_run_shows_waiting(self):
        mgr = MagicMock()
        fake_state = MagicMock()
        fake_state.runs = {}
        mgr.load.return_value = fake_state
        dash = SwarmDashboard(state_mgr=mgr, run_id="missing-run", task="test")
        c = Console(record=True, width=120)
        c.print(dash)
        output = c.export_text()
        assert "Waiting" in output

    def test_with_peer_status_shows_milestone(self):
        mgr = _make_mock_state_mgr("run-1", workers=[{"wid": "w-1", "ws": "running"}])
        coord = MagicMock()

        class FakePeerStatus:
            worker_id = "w-1"
            milestone = "Running tests"
            status = "in-progress"

        coord.read_all_statuses.return_value = [FakePeerStatus()]
        dash = SwarmDashboard(state_mgr=mgr, run_id="run-1", task="test", coord_mgr=coord)
        c = Console(record=True, width=120)
        c.print(dash)
        output = c.export_text()
        assert "Running tests" in output

    def test_with_events_shows_log_panel(self, tmp_path):
        mgr = _make_mock_state_mgr("run-1", workers=[{"wid": "w-1"}])
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            json.dumps({"event": "worker_start", "worker_id": "w-1", "title": "Do stuff"}) + "\n"
        )
        dash = SwarmDashboard(
            state_mgr=mgr, run_id="run-1", task="test",
            events_path=events_file,
        )
        c = Console(record=True, width=120)
        c.print(dash)
        output = c.export_text()
        assert "Recent Events" in output
        assert "w-1" in output

    def test_no_workers_shows_placeholder(self):
        mgr = _make_mock_state_mgr("run-1", workers=[])
        dash = SwarmDashboard(state_mgr=mgr, run_id="run-1", task="test")
        c = Console(record=True, width=120)
        c.print(dash)
        output = c.export_text()
        assert "No workers" in output

    def test_skipped_worker_shows_skipped(self):
        mgr = _make_mock_state_mgr("run-1", workers=[
            {"wid": "w-1", "ws": "failed", "error": "Skipped: cost limit exceeded"},
        ])
        dash = SwarmDashboard(state_mgr=mgr, run_id="run-1", task="test")
        c = Console(record=True, width=120)
        c.print(dash)
        output = c.export_text()
        assert "skipped" in output


# ── Resilience ─────────────────────────────────────────────────────


class TestSwarmDashboardResilience:
    def test_corrupt_state_uses_cache(self):
        # First call succeeds, second fails
        mgr = _make_mock_state_mgr("run-1", workers=[{"wid": "w-1"}], task="cached task")
        dash = SwarmDashboard(state_mgr=mgr, run_id="run-1", task="cached task")

        # First render — populates cache
        c = Console(record=True, width=120)
        c.print(dash)
        output1 = c.export_text()
        assert "cached task" in output1

        # Make load() raise
        mgr.load.side_effect = Exception("corrupt file")

        c2 = Console(record=True, width=120)
        c2.print(dash)
        output2 = c2.export_text()
        # Should still render using cached state
        assert "cached task" in output2

    def test_empty_events_file_no_crash(self, tmp_path):
        mgr = _make_mock_state_mgr("run-1", workers=[{"wid": "w-1"}])
        events_file = tmp_path / "events.jsonl"
        events_file.write_text("")
        dash = SwarmDashboard(
            state_mgr=mgr, run_id="run-1", task="test",
            events_path=events_file,
        )
        c = Console(record=True, width=120)
        c.print(dash)
        output = c.export_text()
        assert "Recent Events" not in output  # no events, no panel


# ── Live integration ───────────────────────────────────────────────


class TestLiveIntegration:
    async def test_live_refresh_no_crash(self):
        """Verify Live + asyncio refresh works without crashes."""
        import asyncio
        from rich.live import Live

        mgr = _make_mock_state_mgr("run-1", workers=[{"wid": "w-1"}])
        dash = SwarmDashboard(state_mgr=mgr, run_id="run-1", task="test")
        c = Console(record=True, width=120)

        with Live(dash, console=c, auto_refresh=False) as live_display:
            async def _refresh():
                try:
                    while True:
                        live_display.update(dash)
                        live_display.refresh()
                        await asyncio.sleep(0.1)
                except asyncio.CancelledError:
                    pass

            refresh_task = asyncio.create_task(_refresh())
            # Let it run for a few cycles
            await asyncio.sleep(0.35)
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass

        output = c.export_text()
        assert len(output) > 0

    async def test_refresh_task_cancelled_properly(self):
        """Verify the refresh task is properly cancelled."""
        import asyncio
        from rich.live import Live

        mgr = _make_mock_state_mgr("run-1", workers=[])
        dash = SwarmDashboard(state_mgr=mgr, run_id="run-1", task="test")
        c = Console(record=True, width=120)

        with Live(dash, console=c, auto_refresh=False) as live_display:
            async def _refresh():
                try:
                    while True:
                        live_display.update(dash)
                        live_display.refresh()
                        await asyncio.sleep(0.1)
                except asyncio.CancelledError:
                    pass

            refresh_task = asyncio.create_task(_refresh())
            await asyncio.sleep(0.15)
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass

        assert refresh_task.done()
