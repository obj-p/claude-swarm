"""Shared notes for inter-worker coordination."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SharedNote(BaseModel):
    """A structured note written by a worker for other workers to read."""

    worker_id: str
    timestamp: str
    topic: str
    content: str
    tags: list[str] = Field(default_factory=list)


class NoteManager:
    """Manages shared notes at <repo>/.claude-swarm/notes/<run_id>/."""

    def __init__(self, repo_path: Path, run_id: str) -> None:
        self.repo_path = repo_path.resolve()
        self.run_id = run_id
        self._notes_dir = self.repo_path / ".claude-swarm" / "notes" / run_id

    @property
    def notes_dir(self) -> Path:
        return self._notes_dir

    def setup(self) -> Path:
        """Create the notes directory and return its absolute path."""
        self._notes_dir.mkdir(parents=True, exist_ok=True)
        return self._notes_dir.resolve()

    def read_note(self, worker_id: str) -> SharedNote | None:
        """Read a single worker's note file. Returns None if missing or invalid."""
        note_path = self._notes_dir / f"{worker_id}.json"
        if not note_path.exists():
            return None
        try:
            raw = json.loads(note_path.read_text())
            return SharedNote.model_validate(raw)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Invalid note file: %s", note_path)
            return None

    def read_all_notes(self) -> list[SharedNote]:
        """Read all valid notes in the notes directory."""
        if not self._notes_dir.exists():
            return []
        notes: list[SharedNote] = []
        for path in sorted(self._notes_dir.glob("*.json")):
            worker_id = path.stem
            note = self.read_note(worker_id)
            if note is not None:
                notes.append(note)
        return notes

    def list_note_files(self) -> list[str]:
        """Return worker IDs that have note files."""
        if not self._notes_dir.exists():
            return []
        return sorted(p.stem for p in self._notes_dir.glob("*.json"))

    def format_notes_summary(self) -> str:
        """Format all notes as a Markdown summary for the reviewer."""
        notes = self.read_all_notes()
        if not notes:
            return ""
        lines = ["## Worker Notes\n"]
        for note in notes:
            tags = f" [{', '.join(note.tags)}]" if note.tags else ""
            lines.append(f"### {note.worker_id}: {note.topic}{tags}\n")
            lines.append(f"{note.content}\n")
        return "\n".join(lines)

    def cleanup(self) -> None:
        """Remove the run's notes directory."""
        if self._notes_dir.exists():
            shutil.rmtree(self._notes_dir)
