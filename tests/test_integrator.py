"""Tests for integrator (mix of real commands and mocks)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_swarm.errors import IntegrationError, MergeConflictError
from claude_swarm.guards import swarm_can_use_tool
from claude_swarm.integrator import (
    _check_gh_installed,
    _run_command,
    _run_semantic_review,
    create_pr,
    integrate_results,
)
from claude_swarm.models import WorkerResult
from claude_swarm.prompts import REVIEWER_SYSTEM_PROMPT
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

    @pytest.mark.asyncio
    async def test_create_pr_with_issue_number(self, tmp_git_repo):
        """Verify that create_pr includes 'Closes #N' when issue_number is provided."""
        mgr = WorktreeManager(tmp_git_repo, "run-1")

        path1 = await mgr.create_worktree("w1", "main")
        (path1 / "file_a.txt").write_text("worker 1\n")
        subprocess.run(["git", "add", "file_a.txt"], cwd=path1, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=T", "commit", "-m", "w1"],
            cwd=path1, check=True, capture_output=True,
        )

        worker_results = [WorkerResult(worker_id="w1", success=True, summary="done")]

        # Mock _run_git for push and asyncio.create_subprocess_exec for gh pr create
        captured_body = {}

        async def mock_create_subprocess_exec(*args, **kwargs):
            # Capture the --body argument from gh pr create
            args_list = list(args)
            if "gh" in args_list and "pr" in args_list:
                body_idx = args_list.index("--body") + 1
                captured_body["value"] = args_list[body_idx]

            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"https://github.com/o/r/pull/1\n", b""))
            return proc

        with patch("claude_swarm.integrator._run_git", AsyncMock(return_value="")), \
             patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec):
            pr_url = await create_pr(
                path1,
                "swarm/run-1/integration",
                "main",
                run_id="run-1",
                task_description="Fix tests",
                worker_results=worker_results,
                issue_number=42,
            )

        assert "Closes #42" in captured_body["value"]
        assert pr_url == "https://github.com/o/r/pull/1"


class TestSemanticReview:
    async def test_calls_run_agent_with_correct_options(self, tmp_path):
        with patch("claude_swarm.integrator.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AsyncMock(is_error=False)
            await _run_semantic_review(tmp_path, "opus")
            options = mock_run.call_args.kwargs["options"]
            assert options.system_prompt == REVIEWER_SYSTEM_PROMPT
            assert options.model == "opus"
            assert options.max_budget_usd == 3.0
            assert options.max_turns == 20
            assert options.cwd == str(tmp_path)
            assert options.can_use_tool is swarm_can_use_tool

    async def test_forwards_notes_summary(self, tmp_path):
        with patch("claude_swarm.integrator.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AsyncMock(is_error=False)
            await _run_semantic_review(tmp_path, "opus", notes_summary="Worker notes here")
            prompt = mock_run.call_args.kwargs["prompt"]
            assert "Worker notes here" in prompt

    async def test_no_notes_summary(self, tmp_path):
        with patch("claude_swarm.integrator.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AsyncMock(is_error=False)
            await _run_semantic_review(tmp_path, "opus", notes_summary="")
            prompt = mock_run.call_args.kwargs["prompt"]
            # Should be the base prompt only, no extra newline/notes
            assert prompt == "Review the merged changes for semantic conflicts and fix any issues you find."

    async def test_reviewer_allowed_tools(self, tmp_path):
        with patch("claude_swarm.integrator.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AsyncMock(is_error=False)
            await _run_semantic_review(tmp_path, "opus")
            options = mock_run.call_args.kwargs["options"]
            assert options.allowed_tools == ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]
            assert options.permission_mode == "acceptEdits"


class TestIntegrateResultsWithReview:
    async def test_review_true_invokes_semantic_review(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")

        path1 = await mgr.create_worktree("w1", "main")
        (path1 / "file_a.txt").write_text("from worker 1\n")
        subprocess.run(["git", "add", "file_a.txt"], cwd=path1, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=T", "commit", "-m", "w1 work"],
            cwd=path1, check=True, capture_output=True,
        )

        worker_results = [WorkerResult(worker_id="w1", success=True, summary="done w1")]

        with patch("claude_swarm.integrator._run_semantic_review", new_callable=AsyncMock) as mock_review:
            success, pr_url, error = await integrate_results(
                mgr, worker_results, "main",
                run_id="run-1", should_create_pr=False,
                review=True, orchestrator_model="opus", notes_summary="some notes",
            )

        assert success is True
        mock_review.assert_called_once()
        call_kwargs = mock_review.call_args
        assert call_kwargs[0][1] == "opus"
        assert call_kwargs[1]["notes_summary"] == "some notes"

    async def test_review_false_skips_semantic_review(self, tmp_git_repo):
        mgr = WorktreeManager(tmp_git_repo, "run-1")

        path1 = await mgr.create_worktree("w1", "main")
        (path1 / "file_a.txt").write_text("from worker 1\n")
        subprocess.run(["git", "add", "file_a.txt"], cwd=path1, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=T", "commit", "-m", "w1 work"],
            cwd=path1, check=True, capture_output=True,
        )

        worker_results = [WorkerResult(worker_id="w1", success=True, summary="done w1")]

        with patch("claude_swarm.integrator._run_semantic_review", new_callable=AsyncMock) as mock_review:
            success, pr_url, error = await integrate_results(
                mgr, worker_results, "main",
                run_id="run-1", should_create_pr=False,
                review=False,
            )

        assert success is True
        mock_review.assert_not_called()
