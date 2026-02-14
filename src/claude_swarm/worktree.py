"""Git worktree manager for isolating worker agents."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from claude_swarm.errors import WorktreeError

logger = logging.getLogger(__name__)

GIT_LOCK_RETRIES = 3
GIT_LOCK_BACKOFF = 0.5


async def _run_git(
    args: list[str],
    cwd: Path,
    *,
    retries: int = GIT_LOCK_RETRIES,
    check: bool = True,
) -> str:
    """Run a git command with lock-contention retry."""
    cmd = ["git"] + args
    for attempt in range(retries):
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        out = stdout.decode().strip()
        err = stderr.decode().strip()

        if proc.returncode == 0:
            return out

        if "lock" in err.lower():
            if attempt < retries - 1:
                logger.debug("Git lock contention, retrying in %.1fs (attempt %d/%d)", GIT_LOCK_BACKOFF * (attempt + 1), attempt + 1, retries)
                await asyncio.sleep(GIT_LOCK_BACKOFF * (attempt + 1))
            continue

        # Non-lock error — fail immediately
        if check:
            raise WorktreeError(f"git {' '.join(args)} failed: {err}")
        return out

    # Only reachable if all attempts hit lock contention
    if check:
        raise WorktreeError(f"git {' '.join(args)} failed after {retries} retries (lock contention)")
    return ""


class WorktreeManager:
    """Manages git worktrees for swarm workers.

    Creates isolated worktrees under .swarm-worktrees/<run_id>/<worker_id>,
    each on its own branch named swarm/<run_id>/<worker_id>.
    """

    def __init__(self, repo_path: Path, run_id: str) -> None:
        self.repo_path = repo_path.resolve()
        self.run_id = run_id
        self._worktrees: dict[str, Path] = {}
        self._branches: list[str] = []
        self._gc_disabled = False

    async def get_base_branch(self) -> str:
        """Get the current branch of the main repo."""
        return await _run_git(["rev-parse", "--abbrev-ref", "HEAD"], self.repo_path)

    async def disable_gc(self) -> None:
        """Disable git gc during parallel operations."""
        if not self._gc_disabled:
            await _run_git(["config", "gc.auto", "0"], self.repo_path)
            self._gc_disabled = True

    async def restore_gc(self) -> None:
        """Re-enable git gc."""
        if self._gc_disabled:
            await _run_git(["config", "--unset", "gc.auto"], self.repo_path, check=False)
            self._gc_disabled = False

    async def create_worktree(self, worker_id: str, base_branch: str) -> Path:
        """Create an isolated worktree for a worker.

        Returns the path to the new worktree.
        """
        worktree_dir = self.repo_path / ".swarm-worktrees" / self.run_id / worker_id
        branch_name = f"swarm/{self.run_id}/{worker_id}"

        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        await _run_git(
            ["worktree", "add", "-b", branch_name, str(worktree_dir), base_branch],
            self.repo_path,
        )

        self._worktrees[worker_id] = worktree_dir
        self._branches.append(branch_name)
        self._base_branch = base_branch
        logger.debug("Created worktree %s at %s (branch %s)", worker_id, worktree_dir, branch_name)
        return worktree_dir

    async def create_integration_worktree(self, base_branch: str) -> Path:
        """Create a worktree for the integration step."""
        return await self.create_worktree("integration", base_branch)

    async def remove_worktree(self, worker_id: str) -> None:
        """Remove a specific worktree."""
        worktree_path = self._worktrees.get(worker_id)
        if worktree_path and worktree_path.exists():
            await _run_git(["worktree", "remove", str(worktree_path), "--force"], self.repo_path, check=False)
            self._worktrees.pop(worker_id, None)

    async def remove_branch(self, branch_name: str) -> None:
        """Delete a branch."""
        await _run_git(["branch", "-D", branch_name], self.repo_path, check=False)

    async def cleanup_all(self, force: bool = False) -> None:
        """Remove all swarm worktrees and branches.

        If force=True, also cleans up worktrees from previous runs.
        """
        if force:
            # Find all swarm worktrees
            out = await _run_git(["worktree", "list", "--porcelain"], self.repo_path, check=False)
            for line in out.splitlines():
                if line.startswith("worktree ") and ".swarm-worktrees" in line:
                    wt_path = line.split("worktree ", 1)[1]
                    await _run_git(["worktree", "remove", wt_path, "--force"], self.repo_path, check=False)

            # Clean up swarm branches
            branches_out = await _run_git(["branch", "--list", "swarm/*"], self.repo_path, check=False)
            for branch in branches_out.splitlines():
                branch = branch.strip().lstrip("* ")
                if branch:
                    await _run_git(["branch", "-D", branch], self.repo_path, check=False)

            # Remove leftover directories
            swarm_dir = self.repo_path / ".swarm-worktrees"
            if swarm_dir.exists():
                import shutil
                shutil.rmtree(swarm_dir, ignore_errors=True)
        else:
            # Only clean up this run's worktrees (keep branches for PR)
            for worker_id in list(self._worktrees):
                await self.remove_worktree(worker_id)

            run_dir = self.repo_path / ".swarm-worktrees" / self.run_id
            if run_dir.exists():
                import shutil
                shutil.rmtree(run_dir, ignore_errors=True)

        await self.restore_gc()
        logger.debug("Cleanup complete")

    async def get_worktree_diff(self, worker_id: str) -> str:
        """Get the diff of changes in a worker's worktree compared to its base."""
        worktree_path = self._worktrees.get(worker_id)
        if not worktree_path:
            raise WorktreeError(f"No worktree found for worker {worker_id}")
        return await _run_git(["diff", "HEAD"], worktree_path)

    async def get_worktree_changed_files(self, worker_id: str) -> list[str]:
        """Get list of files changed in a worker's worktree."""
        worktree_path = self._worktrees.get(worker_id)
        if not worktree_path:
            raise WorktreeError(f"No worktree found for worker {worker_id}")
        # Show committed changes relative to the base branch
        base = getattr(self, "_base_branch", "HEAD")
        out = await _run_git(["diff", "--name-only", f"{base}..HEAD"], worktree_path, check=False)
        if not out:
            # Fallback: show any uncommitted changes
            out = await _run_git(["diff", "--name-only", "HEAD"], worktree_path, check=False)
        return [f for f in out.splitlines() if f.strip()]

    def get_branch_name(self, worker_id: str) -> str:
        """Get the branch name for a worker."""
        return f"swarm/{self.run_id}/{worker_id}"

    def get_worktree_path(self, worker_id: str) -> Path | None:
        """Get the filesystem path of a worker's worktree."""
        return self._worktrees.get(worker_id)

    @property
    def worker_branches(self) -> list[str]:
        """All branches created by this manager (excluding integration)."""
        return [b for b in self._branches if not b.endswith("/integration")]
