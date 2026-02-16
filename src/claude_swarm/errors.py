"""Error hierarchy for claude-swarm."""


class SwarmError(Exception):
    """Base error for all swarm operations."""


class WorktreeError(SwarmError):
    """Error during git worktree operations."""


class WorkerError(SwarmError):
    """Error during worker agent execution."""


class IntegrationError(SwarmError):
    """Error during branch integration."""


class MergeConflictError(IntegrationError):
    """Merge conflict between worker branches."""

    def __init__(self, message: str, conflicting_branches: list[str] | None = None, diff_context: str | None = None):
        super().__init__(message)
        self.conflicting_branches = conflicting_branches or []
        self.diff_context = diff_context


class PlanningError(SwarmError):
    """Error during task planning/decomposition."""


class GitHubError(SwarmError):
    """Error during GitHub API operations (gh CLI calls)."""
