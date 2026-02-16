"""Coordination system for inter-worker messaging, status tracking, and shared notes."""

from __future__ import annotations

import json
import logging
import shutil
from enum import Enum
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


class MessageType(str, Enum):
    """Type of inter-worker message."""

    INFO = "info"
    QUESTION = "question"
    DECISION = "decision"
    BLOCKER = "blocker"


class Message(BaseModel):
    """A directed message from one worker to another."""

    from_worker: str
    to_worker: str
    timestamp: str
    topic: str
    content: str
    message_type: MessageType = MessageType.INFO


class PeerStatus(str, Enum):
    """Self-reported worker progress status."""

    STARTING = "starting"
    IN_PROGRESS = "in-progress"
    MILESTONE_REACHED = "milestone-reached"
    BLOCKED = "blocked"
    DONE = "done"


class WorkerPeerStatus(BaseModel):
    """Self-reported progress status from a worker."""

    worker_id: str
    timestamp: str
    status: PeerStatus = PeerStatus.STARTING
    milestone: str = ""
    details: str = ""


class CoordinationManager:
    """Manages shared notes, directed messages, and peer status.

    Directory layout:
        .claude-swarm/coordination/<run_id>/
            notes/                  — one JSON file per worker
            messages/               — per-worker inbox directories
                <worker_id>/
                    NNN-from-<sender>.json
            status/                 — self-reported progress per worker
                <worker_id>.json
    """

    def __init__(self, repo_path: Path, run_id: str) -> None:
        self.repo_path = repo_path.resolve()
        self.run_id = run_id
        self._base_dir = self.repo_path / ".claude-swarm" / "coordination" / run_id

    @property
    def notes_dir(self) -> Path:
        """Backward-compatible notes directory path."""
        return self._base_dir / "notes"

    @property
    def coordination_dir(self) -> Path:
        """Root coordination directory for this run."""
        return self._base_dir

    def setup(self, worker_ids: list[str] | None = None) -> Path:
        """Create coordination directories and return the notes directory path.

        Returns the notes directory for backward compatibility with NoteManager.setup().
        """
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        messages_dir = self._base_dir / "messages"
        messages_dir.mkdir(parents=True, exist_ok=True)
        status_dir = self._base_dir / "status"
        status_dir.mkdir(parents=True, exist_ok=True)

        if worker_ids:
            for wid in worker_ids:
                (messages_dir / wid).mkdir(exist_ok=True)

        return self.notes_dir.resolve()

    # ── Notes (backward-compatible with NoteManager) ─────────────────

    def read_note(self, worker_id: str) -> SharedNote | None:
        """Read a single worker's note file. Returns None if missing or invalid."""
        note_path = self.notes_dir / f"{worker_id}.json"
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
        if not self.notes_dir.exists():
            return []
        notes: list[SharedNote] = []
        for path in sorted(self.notes_dir.glob("*.json")):
            worker_id = path.stem
            note = self.read_note(worker_id)
            if note is not None:
                notes.append(note)
        return notes

    def list_note_files(self) -> list[str]:
        """Return worker IDs that have note files."""
        if not self.notes_dir.exists():
            return []
        return sorted(p.stem for p in self.notes_dir.glob("*.json"))

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

    # ── Messages ─────────────────────────────────────────────────────

    def read_inbox(self, worker_id: str) -> list[Message]:
        """Read all messages in a worker's inbox."""
        inbox_dir = self._base_dir / "messages" / worker_id
        if not inbox_dir.exists():
            return []
        messages: list[Message] = []
        for path in sorted(inbox_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text())
                messages.append(Message.model_validate(raw))
            except (json.JSONDecodeError, ValueError):
                logger.warning("Invalid message file: %s", path)
        return messages

    def read_all_messages(self) -> list[Message]:
        """Read all messages across all inboxes."""
        messages_dir = self._base_dir / "messages"
        if not messages_dir.exists():
            return []
        all_messages: list[Message] = []
        for inbox_dir in sorted(messages_dir.iterdir()):
            if inbox_dir.is_dir():
                all_messages.extend(self.read_inbox(inbox_dir.name))
        return all_messages

    def format_messages_summary(self) -> str:
        """Format all messages as a Markdown summary."""
        messages = self.read_all_messages()
        if not messages:
            return ""
        lines = ["## Inter-Worker Messages\n"]
        for msg in messages:
            lines.append(
                f"### {msg.from_worker} → {msg.to_worker}: {msg.topic} [{msg.message_type.value}]\n"
            )
            lines.append(f"{msg.content}\n")
        return "\n".join(lines)

    # ── Status ───────────────────────────────────────────────────────

    def read_status(self, worker_id: str) -> WorkerPeerStatus | None:
        """Read a worker's self-reported status."""
        status_path = self._base_dir / "status" / f"{worker_id}.json"
        if not status_path.exists():
            return None
        try:
            raw = json.loads(status_path.read_text())
            return WorkerPeerStatus.model_validate(raw)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Invalid status file: %s", status_path)
            return None

    def read_all_statuses(self) -> list[WorkerPeerStatus]:
        """Read all valid worker status files."""
        status_dir = self._base_dir / "status"
        if not status_dir.exists():
            return []
        statuses: list[WorkerPeerStatus] = []
        for path in sorted(status_dir.glob("*.json")):
            status = self.read_status(path.stem)
            if status is not None:
                statuses.append(status)
        return statuses

    def format_status_summary(self) -> str:
        """Format all worker statuses as a Markdown summary."""
        statuses = self.read_all_statuses()
        if not statuses:
            return ""
        lines = ["## Worker Status\n"]
        for s in statuses:
            milestone = f" — {s.milestone}" if s.milestone else ""
            lines.append(f"- **{s.worker_id}**: {s.status.value}{milestone}\n")
            if s.details:
                lines.append(f"  {s.details}\n")
        return "\n".join(lines)

    # ── Combined Summary ─────────────────────────────────────────────

    def format_coordination_summary(self) -> str:
        """Combine notes, messages, and status into one summary."""
        parts = [
            self.format_notes_summary(),
            self.format_messages_summary(),
            self.format_status_summary(),
        ]
        return "\n".join(p for p in parts if p)

    # ── Cleanup ──────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Remove the run's entire coordination directory."""
        if self._base_dir.exists():
            shutil.rmtree(self._base_dir)
