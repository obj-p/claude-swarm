"""Swarm configuration from CLI arguments."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SwarmConfig:
    """Configuration for a swarm run, populated from CLI arguments."""

    task: str = ""
    repo_path: Path = field(default_factory=lambda: Path.cwd())
    max_workers: int = 4
    model: str = "sonnet"
    orchestrator_model: str = "opus"
    max_cost: float = 50.0
    max_worker_cost: float = 5.0
    create_pr: bool = True
    dry_run: bool = False
    review: bool = False
    verbose: bool = False
    base_branch: str | None = None
    max_worker_retries: int = 1
    escalation_model: str = "opus"
    enable_escalation: bool = True
    resolve_conflicts: bool = True
    oversight: str = "pr-gated"
    issue_number: int | None = None

    def __post_init__(self) -> None:
        self.repo_path = Path(self.repo_path).resolve()

    @property
    def run_id(self) -> str:
        """Lazily set by Orchestrator at run start."""
        if not hasattr(self, "_run_id"):
            from datetime import datetime, timezone
            self._run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return self._run_id

    @run_id.setter
    def run_id(self, value: str) -> None:
        self._run_id = value
