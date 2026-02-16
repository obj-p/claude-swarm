"""Pydantic models for task plans, worker results, and swarm outcomes."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class OversightLevel(str, Enum):
    """How much human oversight a swarm run requires."""

    AUTONOMOUS = "autonomous"
    PR_GATED = "pr-gated"
    CHECKPOINT = "checkpoint"


class RunStatus(str, Enum):
    """Status of an overall swarm run."""

    PLANNING = "planning"
    EXECUTING = "executing"
    INTEGRATING = "integrating"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    PAUSED_CHECKPOINT = "paused_checkpoint"


class WorkerStatus(str, Enum):
    """Status of an individual worker within a run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkerTask(BaseModel):
    """A subtask assigned to a single worker agent."""

    worker_id: str = Field(description="Unique identifier for this worker (e.g., 'worker-1')")
    title: str = Field(description="Short title describing the subtask")
    description: str = Field(description="Detailed instructions for the worker")
    target_files: list[str] = Field(default_factory=list, description="Files this worker will likely modify")
    acceptance_criteria: list[str] = Field(default_factory=list, description="Conditions for this subtask to be considered complete")
    coordination_notes: str = Field(default="", description="Instructions for what this worker should write to or read from shared notes")
    coupled_with: list[str] = Field(default_factory=list, description="Worker IDs this task is tightly coupled with")
    shared_interfaces: list[str] = Field(default_factory=list, description="Shared interface contracts with coupled workers")


class TaskPlan(BaseModel):
    """The orchestrator's decomposition of a task into parallel subtasks."""

    original_task: str = Field(description="The original task description")
    reasoning: str = Field(description="Explanation of why the task was decomposed this way")
    tasks: list[WorkerTask] = Field(description="Subtasks to execute in parallel")
    integration_notes: str = Field(default="", description="Notes for the integration step about how pieces fit together")
    test_command: str | None = Field(default=None, description="Command to run tests after integration")
    build_command: str | None = Field(default=None, description="Command to build the project after integration")


class WorkerResult(BaseModel):
    """Result from a single worker's execution."""

    worker_id: str
    success: bool
    cost_usd: float | None = None
    duration_ms: int | None = None
    summary: str | None = None
    files_changed: list[str] = Field(default_factory=list)
    error: str | None = None
    attempt: int = 1
    model_used: str | None = None


class IssueConfig(BaseModel):
    """Configuration extracted from a GitHub issue for swarm processing."""

    issue_number: int
    owner: str
    repo_name: str
    title: str
    body: str
    labels: list[str] = Field(default_factory=list)
    # Extracted overrides (None = use default)
    oversight: str | None = None
    model: str | None = None
    max_workers: int | None = None
    max_cost: float | None = None
    max_worker_cost: float | None = None

    @field_validator("oversight")
    @classmethod
    def _validate_oversight(cls, v: str | None) -> str | None:
        if v is not None and v not in {level.value for level in OversightLevel}:
            return None
        return v

    @property
    def task_description(self) -> str:
        title = self.title.removeprefix("[swarm]").strip()
        if self.body:
            return f"{title}\n\n{self.body}"
        return title


class SwarmResult(BaseModel):
    """Final result of a complete swarm run."""

    run_id: str
    task: str
    plan: TaskPlan
    worker_results: list[WorkerResult] = Field(default_factory=list)
    integration_success: bool = False
    pr_url: str | None = None
    total_cost_usd: float = 0.0
    duration_ms: int = 0
