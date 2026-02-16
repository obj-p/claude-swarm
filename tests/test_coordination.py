"""Tests for CoordinationManager, Message, and WorkerPeerStatus."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from claude_swarm.coordination import (
    CoordinationManager,
    Message,
    MessageType,
    PeerStatus,
    SharedNote,
    WorkerPeerStatus,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _write_note(notes_dir: Path, worker_id: str, data: dict) -> None:
    (notes_dir / f"{worker_id}.json").write_text(json.dumps(data))


def _valid_note_dict(**overrides) -> dict:
    defaults = {
        "worker_id": "w1",
        "timestamp": "2025-01-01T00:00:00Z",
        "topic": "api-schema",
        "content": "The API uses snake_case fields.",
        "tags": ["api"],
    }
    defaults.update(overrides)
    return defaults


def _write_message(messages_dir: Path, to_worker: str, filename: str, data: dict) -> None:
    inbox = messages_dir / to_worker
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / filename).write_text(json.dumps(data))


def _valid_message_dict(**overrides) -> dict:
    defaults = {
        "from_worker": "w1",
        "to_worker": "w2",
        "timestamp": "2025-01-01T00:00:00Z",
        "topic": "api-contract",
        "content": "Using snake_case for all fields.",
        "message_type": "info",
    }
    defaults.update(overrides)
    return defaults


def _write_status(status_dir: Path, worker_id: str, data: dict) -> None:
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / f"{worker_id}.json").write_text(json.dumps(data))


def _valid_status_dict(**overrides) -> dict:
    defaults = {
        "worker_id": "w1",
        "timestamp": "2025-01-01T00:00:00Z",
        "status": "in-progress",
        "milestone": "API endpoints defined",
        "details": "All 5 endpoints scaffolded.",
    }
    defaults.update(overrides)
    return defaults


# ── Message Model ────────────────────────────────────────────────────

class TestMessage:
    def test_minimal(self):
        msg = Message(
            from_worker="w1",
            to_worker="w2",
            timestamp="2025-01-01T00:00:00Z",
            topic="test",
            content="hello",
        )
        assert msg.message_type == MessageType.INFO

    def test_all_types(self):
        for mt in MessageType:
            msg = Message(
                from_worker="w1",
                to_worker="w2",
                timestamp="2025-01-01T00:00:00Z",
                topic="test",
                content="hello",
                message_type=mt,
            )
            assert msg.message_type == mt

    def test_roundtrip_json(self):
        msg = Message(**_valid_message_dict())
        json_str = msg.model_dump_json()
        msg2 = Message.model_validate_json(json_str)
        assert msg2.from_worker == msg.from_worker
        assert msg2.to_worker == msg.to_worker
        assert msg2.message_type == msg.message_type

    def test_missing_field_raises(self):
        with pytest.raises(ValidationError):
            Message(from_worker="w1", to_worker="w2")


# ── WorkerPeerStatus Model ──────────────────────────────────────────

class TestWorkerPeerStatus:
    def test_minimal(self):
        s = WorkerPeerStatus(
            worker_id="w1",
            timestamp="2025-01-01T00:00:00Z",
        )
        assert s.status == PeerStatus.STARTING
        assert s.milestone == ""
        assert s.details == ""

    def test_all_statuses(self):
        for ps in PeerStatus:
            s = WorkerPeerStatus(
                worker_id="w1",
                timestamp="2025-01-01T00:00:00Z",
                status=ps,
            )
            assert s.status == ps

    def test_roundtrip_json(self):
        s = WorkerPeerStatus(**_valid_status_dict())
        json_str = s.model_dump_json()
        s2 = WorkerPeerStatus.model_validate_json(json_str)
        assert s2.worker_id == s.worker_id
        assert s2.status == s.status
        assert s2.milestone == s.milestone

    def test_missing_field_raises(self):
        with pytest.raises(ValidationError):
            WorkerPeerStatus(worker_id="w1")


# ── CoordinationManager Setup ───────────────────────────────────────

class TestCoordinationManagerSetup:
    def test_creates_all_directories(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup()
        assert (mgr.coordination_dir / "notes").is_dir()
        assert (mgr.coordination_dir / "messages").is_dir()
        assert (mgr.coordination_dir / "status").is_dir()

    def test_creates_inbox_dirs_for_workers(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup(worker_ids=["w1", "w2"])
        assert (mgr.coordination_dir / "messages" / "w1").is_dir()
        assert (mgr.coordination_dir / "messages" / "w2").is_dir()

    def test_returns_notes_dir(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        result = mgr.setup()
        assert result == mgr.notes_dir.resolve()
        assert result.is_absolute()

    def test_idempotent(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        path1 = mgr.setup(worker_ids=["w1"])
        path2 = mgr.setup(worker_ids=["w1", "w2"])
        assert path1 == path2

    def test_notes_dir_property(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        assert mgr.notes_dir == mgr.coordination_dir / "notes"

    def test_coordination_dir_property(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        assert "coordination" in str(mgr.coordination_dir)
        assert "run-1" in str(mgr.coordination_dir)


# ── Notes (backward-compat) ─────────────────────────────────────────

class TestCoordinationManagerNotes:
    def test_read_valid(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        notes_dir = mgr.setup()
        _write_note(notes_dir, "w1", _valid_note_dict())
        note = mgr.read_note("w1")
        assert note is not None
        assert note.worker_id == "w1"

    def test_read_missing_returns_none(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup()
        assert mgr.read_note("nonexistent") is None

    def test_read_invalid_json_returns_none(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        notes_dir = mgr.setup()
        (notes_dir / "w1.json").write_text("not json {{{")
        assert mgr.read_note("w1") is None

    def test_read_all_notes(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        notes_dir = mgr.setup()
        _write_note(notes_dir, "w1", _valid_note_dict(worker_id="w1"))
        _write_note(notes_dir, "w2", _valid_note_dict(worker_id="w2", topic="db"))
        notes = mgr.read_all_notes()
        assert len(notes) == 2

    def test_list_note_files(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        notes_dir = mgr.setup()
        _write_note(notes_dir, "w1", _valid_note_dict())
        _write_note(notes_dir, "w2", _valid_note_dict())
        assert mgr.list_note_files() == ["w1", "w2"]

    def test_format_notes_summary(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        notes_dir = mgr.setup()
        _write_note(notes_dir, "w1", _valid_note_dict(worker_id="w1", tags=["api"]))
        summary = mgr.format_notes_summary()
        assert "## Worker Notes" in summary
        assert "w1" in summary
        assert "[api]" in summary

    def test_format_notes_empty(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup()
        assert mgr.format_notes_summary() == ""


# ── Messages ─────────────────────────────────────────────────────────

class TestCoordinationManagerMessages:
    def test_read_inbox(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup(worker_ids=["w1", "w2"])
        messages_dir = mgr.coordination_dir / "messages"
        _write_message(messages_dir, "w2", "001-from-w1.json", _valid_message_dict())
        inbox = mgr.read_inbox("w2")
        assert len(inbox) == 1
        assert inbox[0].from_worker == "w1"
        assert inbox[0].to_worker == "w2"

    def test_read_inbox_empty(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup(worker_ids=["w1"])
        assert mgr.read_inbox("w1") == []

    def test_read_inbox_nonexistent(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup()
        assert mgr.read_inbox("nonexistent") == []

    def test_read_inbox_skips_invalid(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup(worker_ids=["w2"])
        messages_dir = mgr.coordination_dir / "messages"
        _write_message(messages_dir, "w2", "001-from-w1.json", _valid_message_dict())
        (messages_dir / "w2" / "002-from-bad.json").write_text("invalid json")
        inbox = mgr.read_inbox("w2")
        assert len(inbox) == 1

    def test_read_inbox_sorted(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup(worker_ids=["w2"])
        messages_dir = mgr.coordination_dir / "messages"
        _write_message(
            messages_dir, "w2", "002-from-w3.json",
            _valid_message_dict(from_worker="w3", topic="second"),
        )
        _write_message(
            messages_dir, "w2", "001-from-w1.json",
            _valid_message_dict(from_worker="w1", topic="first"),
        )
        inbox = mgr.read_inbox("w2")
        assert len(inbox) == 2
        assert inbox[0].topic == "first"
        assert inbox[1].topic == "second"

    def test_read_all_messages(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup(worker_ids=["w1", "w2"])
        messages_dir = mgr.coordination_dir / "messages"
        _write_message(
            messages_dir, "w1", "001-from-w2.json",
            _valid_message_dict(from_worker="w2", to_worker="w1"),
        )
        _write_message(
            messages_dir, "w2", "001-from-w1.json",
            _valid_message_dict(from_worker="w1", to_worker="w2"),
        )
        all_msgs = mgr.read_all_messages()
        assert len(all_msgs) == 2

    def test_read_all_messages_empty(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup()
        assert mgr.read_all_messages() == []

    def test_format_messages_summary(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup(worker_ids=["w2"])
        messages_dir = mgr.coordination_dir / "messages"
        _write_message(messages_dir, "w2", "001-from-w1.json", _valid_message_dict())
        summary = mgr.format_messages_summary()
        assert "## Inter-Worker Messages" in summary
        assert "w1" in summary
        assert "w2" in summary
        assert "[info]" in summary

    def test_format_messages_summary_empty(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup()
        assert mgr.format_messages_summary() == ""

    def test_message_types_in_summary(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup(worker_ids=["w2"])
        messages_dir = mgr.coordination_dir / "messages"
        _write_message(
            messages_dir, "w2", "001-from-w1.json",
            _valid_message_dict(message_type="question"),
        )
        summary = mgr.format_messages_summary()
        assert "[question]" in summary


# ── Status ───────────────────────────────────────────────────────────

class TestCoordinationManagerStatus:
    def test_read_status(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup()
        status_dir = mgr.coordination_dir / "status"
        _write_status(status_dir, "w1", _valid_status_dict())
        s = mgr.read_status("w1")
        assert s is not None
        assert s.worker_id == "w1"
        assert s.status == PeerStatus.IN_PROGRESS

    def test_read_status_missing(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup()
        assert mgr.read_status("nonexistent") is None

    def test_read_status_invalid(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup()
        status_dir = mgr.coordination_dir / "status"
        (status_dir / "w1.json").write_text("broken")
        assert mgr.read_status("w1") is None

    def test_read_all_statuses(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup()
        status_dir = mgr.coordination_dir / "status"
        _write_status(status_dir, "w1", _valid_status_dict(worker_id="w1"))
        _write_status(status_dir, "w2", _valid_status_dict(worker_id="w2", status="done"))
        statuses = mgr.read_all_statuses()
        assert len(statuses) == 2

    def test_read_all_statuses_empty(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup()
        assert mgr.read_all_statuses() == []

    def test_format_status_summary(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup()
        status_dir = mgr.coordination_dir / "status"
        _write_status(status_dir, "w1", _valid_status_dict())
        summary = mgr.format_status_summary()
        assert "## Worker Status" in summary
        assert "w1" in summary
        assert "in-progress" in summary
        assert "API endpoints defined" in summary

    def test_format_status_summary_empty(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup()
        assert mgr.format_status_summary() == ""

    def test_format_status_without_milestone(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup()
        status_dir = mgr.coordination_dir / "status"
        _write_status(status_dir, "w1", _valid_status_dict(milestone=""))
        summary = mgr.format_status_summary()
        assert "w1" in summary
        # Should not contain the em-dash separator when no milestone
        assert " — " not in summary


# ── Combined Summary ─────────────────────────────────────────────────

class TestCoordinationManagerCombinedSummary:
    def test_all_channels(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup(worker_ids=["w1", "w2"])
        # Add a note
        notes_dir = mgr.notes_dir
        _write_note(notes_dir, "w1", _valid_note_dict())
        # Add a message
        messages_dir = mgr.coordination_dir / "messages"
        _write_message(messages_dir, "w2", "001-from-w1.json", _valid_message_dict())
        # Add a status
        status_dir = mgr.coordination_dir / "status"
        _write_status(status_dir, "w1", _valid_status_dict())

        summary = mgr.format_coordination_summary()
        assert "## Worker Notes" in summary
        assert "## Inter-Worker Messages" in summary
        assert "## Worker Status" in summary

    def test_partial_channels(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup()
        # Only add a note
        _write_note(mgr.notes_dir, "w1", _valid_note_dict())

        summary = mgr.format_coordination_summary()
        assert "## Worker Notes" in summary
        assert "## Inter-Worker Messages" not in summary
        assert "## Worker Status" not in summary

    def test_empty(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup()
        assert mgr.format_coordination_summary() == ""


# ── Cleanup ──────────────────────────────────────────────────────────

class TestCoordinationManagerCleanup:
    def test_cleanup_removes_entire_dir(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup(worker_ids=["w1"])
        _write_note(mgr.notes_dir, "w1", _valid_note_dict())
        messages_dir = mgr.coordination_dir / "messages"
        _write_message(messages_dir, "w1", "001-from-w2.json", _valid_message_dict())
        assert mgr.coordination_dir.exists()
        mgr.cleanup()
        assert not mgr.coordination_dir.exists()

    def test_cleanup_nonexistent_is_noop(self, tmp_path):
        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.cleanup()  # should not raise


# ── Worker Prompt Selection ──────────────────────────────────────────

class TestWorkerPromptSelection:
    """Test that worker.py selects the right prompt section based on coordination layout."""

    def _get_system_prompt(self, call_args) -> str:
        """Extract system_prompt from ClaudeAgentOptions call."""
        # ClaudeAgentOptions is called with keyword args
        return call_args.kwargs.get("system_prompt", "") or call_args.args[0] if call_args.args else ""

    @pytest.mark.asyncio
    async def test_coordination_dir_with_messages_uses_coordination_section(self, tmp_path):
        from unittest.mock import AsyncMock, patch

        from claude_swarm.models import WorkerTask

        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup(worker_ids=["w1"])

        task = WorkerTask(worker_id="w1", title="test", description="test task")
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        fake_result = AsyncMock()
        fake_result.is_error = False
        fake_result.total_cost_usd = 0.01
        fake_result.result = "done"

        with patch("claude_swarm.worker.run_agent", new_callable=AsyncMock, return_value=fake_result) as mock_agent, \
             patch("claude_swarm.worker.ClaudeAgentOptions") as mock_opts:
            from claude_swarm.worker import _spawn_single_attempt
            await _spawn_single_attempt(
                task, worktree,
                coordination_dir=mgr.coordination_dir,
            )
            # Check the system_prompt passed to ClaudeAgentOptions
            opts_call = mock_opts.call_args
            system_prompt = opts_call.kwargs["system_prompt"]
            assert "Coordination (Inter-Worker Communication)" in system_prompt
            assert "Directed Messages" in system_prompt
            # Should NOT have the legacy notes section header
            assert "Shared Notes (Inter-Worker Coordination)" not in system_prompt

    @pytest.mark.asyncio
    async def test_notes_dir_only_uses_legacy_section(self, tmp_path):
        from unittest.mock import AsyncMock, patch

        from claude_swarm.models import WorkerTask

        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()

        task = WorkerTask(worker_id="w1", title="test", description="test task")
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        fake_result = AsyncMock()
        fake_result.is_error = False
        fake_result.total_cost_usd = 0.01
        fake_result.result = "done"

        with patch("claude_swarm.worker.run_agent", new_callable=AsyncMock, return_value=fake_result), \
             patch("claude_swarm.worker.ClaudeAgentOptions") as mock_opts:
            from claude_swarm.worker import _spawn_single_attempt
            await _spawn_single_attempt(
                task, worktree,
                notes_dir=notes_dir,
            )
            system_prompt = mock_opts.call_args.kwargs["system_prompt"]
            assert "Shared Notes (Inter-Worker Coordination)" in system_prompt
            assert "Coordination (Inter-Worker Communication)" not in system_prompt

    @pytest.mark.asyncio
    async def test_no_dirs_uses_no_coordination_section(self, tmp_path):
        from unittest.mock import AsyncMock, patch

        from claude_swarm.models import WorkerTask

        task = WorkerTask(worker_id="w1", title="test", description="test task")
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        fake_result = AsyncMock()
        fake_result.is_error = False
        fake_result.total_cost_usd = 0.01
        fake_result.result = "done"

        with patch("claude_swarm.worker.run_agent", new_callable=AsyncMock, return_value=fake_result), \
             patch("claude_swarm.worker.ClaudeAgentOptions") as mock_opts:
            from claude_swarm.worker import _spawn_single_attempt
            await _spawn_single_attempt(task, worktree)
            system_prompt = mock_opts.call_args.kwargs["system_prompt"]
            assert "Shared Notes" not in system_prompt
            assert "Coordination" not in system_prompt

    @pytest.mark.asyncio
    async def test_coupled_with_appends_coupling_section(self, tmp_path):
        from unittest.mock import AsyncMock, patch

        from claude_swarm.models import WorkerTask

        mgr = CoordinationManager(tmp_path, "run-1")
        mgr.setup(worker_ids=["w1", "w2"])

        task = WorkerTask(
            worker_id="w1", title="test", description="test task",
            coupled_with=["w2"],
            shared_interfaces=["User API schema"],
        )
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        fake_result = AsyncMock()
        fake_result.is_error = False
        fake_result.total_cost_usd = 0.01
        fake_result.result = "done"

        with patch("claude_swarm.worker.run_agent", new_callable=AsyncMock, return_value=fake_result), \
             patch("claude_swarm.worker.ClaudeAgentOptions") as mock_opts:
            from claude_swarm.worker import _spawn_single_attempt
            await _spawn_single_attempt(
                task, worktree,
                coordination_dir=mgr.coordination_dir,
            )
            system_prompt = mock_opts.call_args.kwargs["system_prompt"]
            assert "Coupled Workers" in system_prompt
            assert "w2" in system_prompt
            assert "User API schema" in system_prompt
