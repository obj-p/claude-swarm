"""Backward-compatibility shim — re-exports from coordination module."""

from claude_swarm.coordination import CoordinationManager as NoteManager
from claude_swarm.coordination import SharedNote

__all__ = ["NoteManager", "SharedNote"]
