"""Tests for WorktreeManager (async, real git)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_swarm.errors import WorktreeError
from claude_swarm.worktree import WorktreeManager, _run_git


class TestWorktreeBasics:
    @pytest.mark.asyncio
    async def test_create_worktree_exists(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")
        path = await mgr.create_worktree("w1", "main")
        assert path.exists()
        assert path.is_dir()

    @pytest.mark.asyncio
    async def test_create_worktree_correct_branch(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")
        path = await mgr.create_worktree("w1", "main")
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=path, capture_output=True, text=True,
        )
        assert result.stdout.strip() == "swarm/run-1/w1"

    @pytest.mark.asyncio
    async def test_worktree_has_repo_content(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")
        path = await mgr.create_worktree("w1", "main")
        assert (path / "README.md").exists()

    @pytest.mark.asyncio
    async def test_create_multiple_worktrees(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")
        paths = []
        for i in range(3):
            p = await mgr.create_worktree(f"w{i}", "main")
            paths.append(p)
        assert len(set(paths)) == 3
        for p in paths:
            assert p.exists()


class TestWorktreeRemoval:
    @pytest.mark.asyncio
    async def test_remove_worktree(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")
        path = await mgr.create_worktree("w1", "main")
        assert path.exists()
        await mgr.remove_worktree("w1")
        assert not path.exists()

    @pytest.mark.asyncio
    async def test_cleanup_all_no_force_preserves_branches(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")
        await mgr.create_worktree("w1", "main")
        branch = mgr.get_branch_name("w1")
        await mgr.cleanup_all(force=False)
        # Branch should still exist
        result = subprocess.run(
            ["git", "branch", "--list", branch],
            cwd=tmp_git_repo, capture_output=True, text=True,
        )
        assert "swarm/run-1/w1" in result.stdout

    @pytest.mark.asyncio
    async def test_cleanup_all_force_removes_branches(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")
        await mgr.create_worktree("w1", "main")
        await mgr.cleanup_all(force=True)
        result = subprocess.run(
            ["git", "branch", "--list", "swarm/*"],
            cwd=tmp_git_repo, capture_output=True, text=True,
        )
        assert result.stdout.strip() == ""
        # .swarm-worktrees should be gone
        assert not (tmp_git_repo / ".swarm-worktrees").exists()


class TestWorktreeInfo:
    @pytest.mark.asyncio
    async def test_get_base_branch(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")
        branch = await mgr.get_base_branch()
        assert branch == "main"

    @pytest.mark.asyncio
    async def test_get_branch_name(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")
        assert mgr.get_branch_name("w1") == "swarm/run-1/w1"

    @pytest.mark.asyncio
    async def test_worker_branches_excludes_integration(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")
        await mgr.create_worktree("w1", "main")
        await mgr.create_integration_worktree("main")
        branches = mgr.worker_branches
        assert "swarm/run-1/w1" in branches
        assert "swarm/run-1/integration" not in branches


class TestWorktreeDiffs:
    @pytest.mark.asyncio
    async def test_get_worktree_changed_files(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")
        path = await mgr.create_worktree("w1", "main")
        # Create, add, commit a new file in the worktree
        new_file = path / "new.txt"
        new_file.write_text("hello\n")
        subprocess.run(["git", "add", "new.txt"], cwd=path, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=T", "commit", "-m", "add new"],
            cwd=path, check=True, capture_output=True,
        )
        files = await mgr.get_worktree_changed_files("w1")
        assert "new.txt" in files

    @pytest.mark.asyncio
    async def test_get_worktree_diff(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")
        path = await mgr.create_worktree("w1", "main")
        # Stage a change without committing
        (path / "README.md").write_text("# Modified\n")
        subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
        diff = await mgr.get_worktree_diff("w1")
        # git diff HEAD shows staged changes
        assert "Modified" in diff


class TestWorktreeGC:
    @pytest.mark.asyncio
    async def test_disable_gc(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")
        await mgr.disable_gc()
        result = subprocess.run(
            ["git", "config", "gc.auto"], cwd=tmp_git_repo,
            capture_output=True, text=True,
        )
        assert result.stdout.strip() == "0"

    @pytest.mark.asyncio
    async def test_restore_gc(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")
        await mgr.disable_gc()
        await mgr.restore_gc()
        result = subprocess.run(
            ["git", "config", "gc.auto"], cwd=tmp_git_repo,
            capture_output=True, text=True,
        )
        # Should be unset (non-zero exit)
        assert result.returncode != 0 or result.stdout.strip() != "0"


class TestRunGitRetry:
    @pytest.mark.asyncio
    async def test_lock_retry(self, tmp_git_repo):
        """Mock a process that fails with 'lock' then succeeds."""
        call_count = 0

        async def fake_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_proc = AsyncMock()
            if call_count == 1:
                mock_proc.communicate.return_value = (b"", b"Unable to create lock file")
                mock_proc.returncode = 128
            else:
                mock_proc.communicate.return_value = (b"success", b"")
                mock_proc.returncode = 0
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await _run_git(["status"], tmp_git_repo, retries=3)
            assert result == "success"
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_lock_exhaustion_raises(self, tmp_git_repo):
        """All retries fail with lock contention -> WorktreeError."""
        call_count = 0

        async def always_locked(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"", b"Unable to create lock file")
            mock_proc.returncode = 128
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=always_locked):
            with pytest.raises(WorktreeError, match="failed after 3 retries"):
                await _run_git(["status"], tmp_git_repo, retries=3)
        # Should have retried before failing (3 attempts total)
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_check_false_returns_on_failure(self, tmp_git_repo):
        """With check=False, failed commands return output instead of raising."""
        result = await _run_git(
            ["log", "--oneline", "nonexistent-ref"], tmp_git_repo, check=False,
        )
        # Should return without raising (result may be empty or contain error text)
        assert isinstance(result, str)


class TestWorktreeErrorPaths:
    @pytest.mark.asyncio
    async def test_get_worktree_diff_unknown_worker(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")
        with pytest.raises(WorktreeError, match="No worktree found"):
            await mgr.get_worktree_diff("nonexistent")

    @pytest.mark.asyncio
    async def test_get_worktree_changed_files_unknown_worker(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")
        with pytest.raises(WorktreeError, match="No worktree found"):
            await mgr.get_worktree_changed_files("nonexistent")
