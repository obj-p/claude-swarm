"""Tests for shared notes (NoteManager + SharedNote)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from claude_swarm.notes import NoteManager, SharedNote


def _write_note(notes_dir: Path, worker_id: str, data: dict) -> None:
    """Helper: write a JSON note file."""
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


class TestSharedNote:
    def test_minimal_note(self):
        note = SharedNote(
            worker_id="w1",
            timestamp="2025-01-01T00:00:00Z",
            topic="schema",
            content="Uses snake_case",
        )
        assert note.worker_id == "w1"
        assert note.tags == []

    def test_full_note_with_tags(self):
        note = SharedNote(
            worker_id="w1",
            timestamp="2025-01-01T00:00:00Z",
            topic="schema",
            content="Uses snake_case",
            tags=["api", "naming"],
        )
        assert note.tags == ["api", "naming"]

    def test_roundtrip_json(self):
        note = SharedNote(**_valid_note_dict())
        json_str = note.model_dump_json()
        note2 = SharedNote.model_validate_json(json_str)
        assert note2.worker_id == note.worker_id
        assert note2.topic == note.topic
        assert note2.content == note.content
        assert note2.tags == note.tags

    def test_missing_field_raises(self):
        with pytest.raises(ValidationError):
            SharedNote(worker_id="w1", timestamp="2025-01-01T00:00:00Z")


class TestNoteManagerSetup:
    def test_creates_directory(self, tmp_path):
        mgr = NoteManager(tmp_path, "run-1")
        result = mgr.setup()
        assert result.exists()
        assert result.is_dir()

    def test_returns_absolute_path(self, tmp_path):
        mgr = NoteManager(tmp_path, "run-1")
        result = mgr.setup()
        assert result.is_absolute()

    def test_idempotent(self, tmp_path):
        mgr = NoteManager(tmp_path, "run-1")
        path1 = mgr.setup()
        path2 = mgr.setup()
        assert path1 == path2
        assert path1.exists()


class TestNoteManagerRead:
    def test_read_valid(self, tmp_path):
        mgr = NoteManager(tmp_path, "run-1")
        notes_dir = mgr.setup()
        _write_note(notes_dir, "w1", _valid_note_dict())
        note = mgr.read_note("w1")
        assert note is not None
        assert note.worker_id == "w1"
        assert note.topic == "api-schema"

    def test_read_missing_returns_none(self, tmp_path):
        mgr = NoteManager(tmp_path, "run-1")
        mgr.setup()
        assert mgr.read_note("nonexistent") is None

    def test_read_invalid_json_returns_none(self, tmp_path):
        mgr = NoteManager(tmp_path, "run-1")
        notes_dir = mgr.setup()
        (notes_dir / "w1.json").write_text("not valid json {{{")
        assert mgr.read_note("w1") is None

    def test_read_all_notes(self, tmp_path):
        mgr = NoteManager(tmp_path, "run-1")
        notes_dir = mgr.setup()
        _write_note(notes_dir, "w1", _valid_note_dict(worker_id="w1"))
        _write_note(notes_dir, "w2", _valid_note_dict(worker_id="w2", topic="db"))
        notes = mgr.read_all_notes()
        assert len(notes) == 2
        assert {n.worker_id for n in notes} == {"w1", "w2"}

    def test_read_all_empty_dir(self, tmp_path):
        mgr = NoteManager(tmp_path, "run-1")
        mgr.setup()
        assert mgr.read_all_notes() == []

    def test_read_all_skips_invalid(self, tmp_path):
        mgr = NoteManager(tmp_path, "run-1")
        notes_dir = mgr.setup()
        _write_note(notes_dir, "w1", _valid_note_dict(worker_id="w1"))
        (notes_dir / "w2.json").write_text("broken")
        notes = mgr.read_all_notes()
        assert len(notes) == 1
        assert notes[0].worker_id == "w1"


class TestNoteManagerList:
    def test_list_note_files(self, tmp_path):
        mgr = NoteManager(tmp_path, "run-1")
        notes_dir = mgr.setup()
        _write_note(notes_dir, "w1", _valid_note_dict())
        _write_note(notes_dir, "w2", _valid_note_dict())
        assert mgr.list_note_files() == ["w1", "w2"]


class TestNoteManagerFormat:
    def test_format_summary(self, tmp_path):
        mgr = NoteManager(tmp_path, "run-1")
        notes_dir = mgr.setup()
        _write_note(notes_dir, "w1", _valid_note_dict(worker_id="w1", topic="api-schema", tags=["api"]))
        summary = mgr.format_notes_summary()
        assert "## Worker Notes" in summary
        assert "w1" in summary
        assert "api-schema" in summary
        assert "[api]" in summary

    def test_format_multiple_notes(self, tmp_path):
        mgr = NoteManager(tmp_path, "run-1")
        notes_dir = mgr.setup()
        _write_note(notes_dir, "w1", _valid_note_dict(worker_id="w1", topic="api-schema"))
        _write_note(notes_dir, "w2", _valid_note_dict(worker_id="w2", topic="db-schema", tags=[]))
        summary = mgr.format_notes_summary()
        assert "w1" in summary
        assert "w2" in summary
        assert "api-schema" in summary
        assert "db-schema" in summary

    def test_format_note_without_tags(self, tmp_path):
        mgr = NoteManager(tmp_path, "run-1")
        notes_dir = mgr.setup()
        _write_note(notes_dir, "w1", _valid_note_dict(worker_id="w1", tags=[]))
        summary = mgr.format_notes_summary()
        assert "w1" in summary
        assert "[" not in summary.split("###")[1]  # No tag brackets in the note header

    def test_format_empty(self, tmp_path):
        mgr = NoteManager(tmp_path, "run-1")
        mgr.setup()
        assert mgr.format_notes_summary() == ""


class TestNoteManagerCleanup:
    def test_cleanup_removes_dir(self, tmp_path):
        mgr = NoteManager(tmp_path, "run-1")
        notes_dir = mgr.setup()
        _write_note(notes_dir, "w1", _valid_note_dict())
        assert notes_dir.exists()
        mgr.cleanup()
        assert not notes_dir.exists()


class TestBackwardCompatShim:
    def test_notemanager_is_coordinationmanager(self):
        from claude_swarm.coordination import CoordinationManager
        assert NoteManager is CoordinationManager

    def test_sharednote_is_same_class(self):
        from claude_swarm.coordination import SharedNote as CoordSharedNote
        assert SharedNote is CoordSharedNote

    def test_notemanager_has_coordination_methods(self, tmp_path):
        mgr = NoteManager(tmp_path, "run-1")
        assert hasattr(mgr, "coordination_dir")
        assert hasattr(mgr, "read_inbox")
        assert hasattr(mgr, "read_all_messages")
        assert hasattr(mgr, "read_all_statuses")
        assert hasattr(mgr, "format_coordination_summary")
