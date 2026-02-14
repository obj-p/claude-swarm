"""Tests for integrator (mix of real commands and mocks)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_swarm.errors import IntegrationError, MergeConflictError
from claude_swarm.integrator import _check_gh_installed, _run_command, create_pr, integrate_results
from claude_swarm.models import WorkerResult
from claude_swarm.worktree import WorktreeManager


class TestRunCommand:
    @pytest.mark.asyncio
    async def test_success(self, tmp_path):
        ok, output = await _run_command("echo hello", tmp_path)
        assert ok is True
        assert "hello" in output

    @pytest.mark.asyncio
    async def test_failure(self, tmp_path):
        ok, output = await _run_command("false", tmp_path)
        assert ok is False


class TestCheckGhInstalled:
    def test_raises_when_missing(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(IntegrationError, match="gh"):
                _check_gh_installed()


class TestIntegrateNoWorkers:
    @pytest.mark.asyncio
    async def test_no_successful_workers_raises(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")
        failed = [WorkerResult(worker_id="w1", success=False, error="nope")]
        with pytest.raises(IntegrationError, match="No successful workers"):
            await integrate_results(
                mgr, failed, "main",
                run_id="run-1", should_create_pr=False,
            )


class TestRealMerge:
    @pytest.mark.asyncio
    async def test_merge_non_conflicting_branches(self, tmp_git_repo):
        """Create 2 branches with non-conflicting changes, merge via integrate_results."""
        mgr = WorktreeManager(tmp_git_repo, "run-1")

        # Create two worktrees
        path1 = await mgr.create_worktree("w1", "main")
        path2 = await mgr.create_worktree("w2", "main")

        # Worker 1: create file_a.txt
        (path1 / "file_a.txt").write_text("from worker 1\n")
        subprocess.run(["git", "add", "file_a.txt"], cwd=path1, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=T", "commit", "-m", "w1 work"],
            cwd=path1, check=True, capture_output=True,
        )

        # Worker 2: create file_b.txt
        (path2 / "file_b.txt").write_text("from worker 2\n")
        subprocess.run(["git", "add", "file_b.txt"], cwd=path2, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=T", "commit", "-m", "w2 work"],
            cwd=path2, check=True, capture_output=True,
        )

        worker_results = [
            WorkerResult(worker_id="w1", success=True, summary="done w1"),
            WorkerResult(worker_id="w2", success=True, summary="done w2"),
        ]

        success, pr_url, error = await integrate_results(
            mgr, worker_results, "main",
            run_id="run-1", should_create_pr=False,
        )

        assert success is True
        assert pr_url is None
        assert error is None

        # Verify the integration worktree has both files
        integration_path = mgr.get_worktree_path("integration")
        assert (integration_path / "file_a.txt").exists()
        assert (integration_path / "file_b.txt").exists()

    @pytest.mark.asyncio
    async def test_merge_conflicting_branches(self, tmp_git_repo):
        """Two workers modify the same file with conflicting content -> MergeConflictError."""
        mgr = WorktreeManager(tmp_git_repo, "run-1")

        path1 = await mgr.create_worktree("w1", "main")
        path2 = await mgr.create_worktree("w2", "main")

        # Both workers modify README.md with different content
        (path1 / "README.md").write_text("Worker 1 was here\n")
        subprocess.run(["git", "add", "README.md"], cwd=path1, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=T", "commit", "-m", "w1 edit"],
            cwd=path1, check=True, capture_output=True,
        )

        (path2 / "README.md").write_text("Worker 2 was here\n")
        subprocess.run(["git", "add", "README.md"], cwd=path2, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=T", "commit", "-m", "w2 edit"],
            cwd=path2, check=True, capture_output=True,
        )

        worker_results = [
            WorkerResult(worker_id="w1", success=True, summary="done w1"),
            WorkerResult(worker_id="w2", success=True, summary="done w2"),
        ]

        with pytest.raises(MergeConflictError) as exc_info:
            await integrate_results(
                mgr, worker_results, "main",
                run_id="run-1", should_create_pr=False,
                resolve_conflicts=False,
            )

        assert len(exc_info.value.conflicting_branches) > 0


class TestConflictResolution:
    @pytest.mark.asyncio
    async def test_conflict_resolution_succeeds(self, tmp_git_repo):
        """Mock _resolve_merge_conflict returning True -> integration succeeds."""
        mgr = WorktreeManager(tmp_git_repo, "run-1")

        path1 = await mgr.create_worktree("w1", "main")
        path2 = await mgr.create_worktree("w2", "main")

        # Both modify README.md -> conflict
        (path1 / "README.md").write_text("Worker 1 was here\n")
        subprocess.run(["git", "add", "README.md"], cwd=path1, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=T", "commit", "-m", "w1 edit"],
            cwd=path1, check=True, capture_output=True,
        )

        (path2 / "README.md").write_text("Worker 2 was here\n")
        subprocess.run(["git", "add", "README.md"], cwd=path2, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=T", "commit", "-m", "w2 edit"],
            cwd=path2, check=True, capture_output=True,
        )

        worker_results = [
            WorkerResult(worker_id="w1", success=True, summary="done w1"),
            WorkerResult(worker_id="w2", success=True, summary="done w2"),
        ]

        with patch("claude_swarm.integrator._resolve_merge_conflict", AsyncMock(return_value=True)):
            success, pr_url, error = await integrate_results(
                mgr, worker_results, "main",
                run_id="run-1", should_create_pr=False,
                resolve_conflicts=True,
            )

        assert success is True
        assert error is None

    @pytest.mark.asyncio
    async def test_conflict_resolution_fails_raises(self, tmp_git_repo):
        """Mock _resolve_merge_conflict returning False -> MergeConflictError raised."""
        mgr = WorktreeManager(tmp_git_repo, "run-1")

        path1 = await mgr.create_worktree("w1", "main")
        path2 = await mgr.create_worktree("w2", "main")

        (path1 / "README.md").write_text("Worker 1 was here\n")
        subprocess.run(["git", "add", "README.md"], cwd=path1, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=T", "commit", "-m", "w1 edit"],
            cwd=path1, check=True, capture_output=True,
        )

        (path2 / "README.md").write_text("Worker 2 was here\n")
        subprocess.run(["git", "add", "README.md"], cwd=path2, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=T", "commit", "-m", "w2 edit"],
            cwd=path2, check=True, capture_output=True,
        )

        worker_results = [
            WorkerResult(worker_id="w1", success=True, summary="done w1"),
            WorkerResult(worker_id="w2", success=True, summary="done w2"),
        ]

        with patch("claude_swarm.integrator._resolve_merge_conflict", AsyncMock(return_value=False)):
            with pytest.raises(MergeConflictError):
                await integrate_results(
                    mgr, worker_results, "main",
                    run_id="run-1", should_create_pr=False,
                    resolve_conflicts=True,
                )


class TestCreatePrPublic:
    def test_create_pr_is_importable(self):
        """Verify create_pr is a public callable (renamed from _create_pr)."""
        assert callable(create_pr)
