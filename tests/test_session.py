"""Tests for SessionRecorder."""

import json
from pathlib import Path

import pytest

from claude_swarm.session import SessionRecorder


def _read_events(session: SessionRecorder) -> list[dict]:
    """Read all events from the JSONL file."""
    lines = session._events_path.read_text().strip().splitlines()
    return [json.loads(line) for line in lines]


class TestSessionRecorder:
    def test_directory_structure_created(self, tmp_path):
        s = SessionRecorder(tmp_path, "run-1")
        assert s.log_dir.exists()
        assert s.log_dir == tmp_path / ".claude-swarm" / "logs" / "run-1"

    def test_events_append_to_jsonl(self, tmp_path):
        s = SessionRecorder(tmp_path, "run-1")
        s.record("test_event", {"key": "value"})
        s.record("test_event_2")
        events = _read_events(s)
        assert len(events) == 2

    def test_event_has_required_fields(self, tmp_path):
        s = SessionRecorder(tmp_path, "run-1")
        s.record("test_event", {"data": 1})
        event = _read_events(s)[0]
        assert "timestamp" in event
        assert "elapsed_ms" in event
        assert "event" in event
        assert event["event"] == "test_event"
        assert event["data"] == 1

    def test_full_lifecycle(self, tmp_path):
        s = SessionRecorder(tmp_path, "run-1")
        s.plan_start("task")
        s.plan_complete(2, cost_usd=0.05)
        s.worker_start("w1", "Title 1")
        s.worker_complete("w1", success=True, cost_usd=0.10, duration_ms=1000)
        events = _read_events(s)
        types = [e["event"] for e in events]
        assert types == ["plan_start", "plan_complete", "worker_start", "worker_complete"]

    def test_worker_error_increments_failure(self, tmp_path):
        s = SessionRecorder(tmp_path, "run-1")
        s.worker_error("w1", "boom")
        assert s._failure_count == 1
        s.worker_error("w2", "crash")
        assert s._failure_count == 2

    def test_cost_tracking_accumulates(self, tmp_path):
        s = SessionRecorder(tmp_path, "run-1")
        s.plan_complete(1, cost_usd=0.05)
        s.worker_complete("w1", success=True, cost_usd=0.10)
        s.worker_complete("w2", success=True, cost_usd=0.20)
        assert s._total_cost == pytest.approx(0.35)
        assert s._worker_costs == {"w1": 0.10, "w2": 0.20}

    def test_write_metadata(self, tmp_path):
        s = SessionRecorder(tmp_path, "run-1")
        s.plan_complete(2, cost_usd=0.05)
        s.worker_start("w1", "Task 1")
        s.worker_complete("w1", success=True, cost_usd=0.10)
        s.worker_start("w2", "Task 2")
        s.worker_complete("w2", success=False, cost_usd=0.08)
        s.write_metadata()
        meta_path = s.log_dir / "metadata.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["run_id"] == "run-1"
        assert meta["total_cost_usd"] == pytest.approx(0.23)
        assert meta["worker_count"] == 2
        assert meta["success_count"] == 1
        assert meta["failure_count"] == 1
        assert "duration_ms" in meta
        assert "worker_costs" in meta

    def test_success_count_tracks(self, tmp_path):
        s = SessionRecorder(tmp_path, "run-1")
        s.worker_complete("w1", success=True)
        s.worker_complete("w2", success=True)
        assert s._success_count == 2

    def test_elapsed_ms_increases(self, tmp_path):
        import time
        s = SessionRecorder(tmp_path, "run-1")
        s.record("first")
        time.sleep(0.01)
        s.record("second")
        events = _read_events(s)
        assert events[1]["elapsed_ms"] >= events[0]["elapsed_ms"]
