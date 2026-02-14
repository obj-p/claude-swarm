"""Integration: merge worker branches, run tests, create PR."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions

from claude_swarm.errors import IntegrationError, MergeConflictError
from claude_swarm.models import WorkerResult
from claude_swarm.prompts import CONFLICT_RESOLVER_SYSTEM_PROMPT, REVIEWER_SYSTEM_PROMPT
from claude_swarm.util import run_agent
from claude_swarm.worktree import WorktreeManager, _run_git

logger = logging.getLogger(__name__)


def _check_gh_installed() -> None:
    """Check that the GitHub CLI is available."""
    if shutil.which("gh") is None:
        raise IntegrationError(
            "GitHub CLI (gh) is not installed. "
            "Install it from https://cli.github.com/ to enable PR creation."
        )


async def integrate_results(
    worktree_mgr: WorktreeManager,
    worker_results: list[WorkerResult],
    base_branch: str,
    *,
    run_id: str,
    test_command: str | None = None,
    build_command: str | None = None,
    create_pr: bool = True,
    review: bool = False,
    task_description: str = "",
    orchestrator_model: str = "opus",
    resolve_conflicts: bool = True,
) -> tuple[bool, str | None, str | None]:
    """Merge worker branches, optionally run tests and create a PR.

    Returns (success, pr_url, error_message).
    """
    successful_workers = [r for r in worker_results if r.success]
    if not successful_workers:
        raise IntegrationError("No successful workers to integrate")

    if create_pr:
        _check_gh_installed()

    # Create integration worktree
    integration_path = await worktree_mgr.create_integration_worktree(base_branch)
    integration_branch = worktree_mgr.get_branch_name("integration")

    try:
        # Merge each successful worker's branch (fail fast on conflict)
        merged_branches: list[str] = []

        for wr in successful_workers:
            branch = worktree_mgr.get_branch_name(wr.worker_id)
            try:
                await _run_git(
                    ["merge", "--no-ff", "-m", f"Merge {wr.worker_id}: {wr.summary or 'completed'}", branch],
                    integration_path,
                )
                merged_branches.append(branch)
            except Exception as e:
                logger.error("Merge conflict with %s: %s", branch, e)

                if resolve_conflicts:
                    # Don't abort — leave conflict markers for the resolver
                    resolved = await _resolve_merge_conflict(
                        integration_path, branch, wr,
                        orchestrator_model=orchestrator_model,
                    )
                    if resolved:
                        merged_branches.append(branch)
                        continue

                # Abort the failed merge
                await _run_git(["merge", "--abort"], integration_path, check=False)

                # Get conflict context
                diff_context = await _run_git(["diff", base_branch, branch], worktree_mgr.repo_path, check=False)
                raise MergeConflictError(
                    f"Merge conflict when integrating {branch}",
                    conflicting_branches=[branch] + merged_branches,
                    diff_context=diff_context[:2000],
                ) from e

        # Run build command if specified
        if build_command:
            build_result = await _run_command(build_command, integration_path)
            if not build_result[0]:
                return False, None, f"Build failed: {build_result[1]}"

        # Run test command if specified
        test_output: str | None = None
        if test_command:
            test_success, test_output = await _run_command(test_command, integration_path)
            if not test_success:
                return False, None, f"Tests failed: {test_output}"

        # Optional semantic review
        if review:
            await _run_semantic_review(integration_path, orchestrator_model)

        # Create PR
        pr_url: str | None = None
        if create_pr:
            pr_url = await _create_pr(
                integration_path,
                integration_branch,
                base_branch,
                run_id=run_id,
                task_description=task_description,
                worker_results=successful_workers,
            )

        return True, pr_url, None

    except MergeConflictError:
        raise
    except Exception as e:
        return False, None, str(e)


async def _run_command(command: str, cwd: Path) -> tuple[bool, str]:
    """Run a shell command and return (success, output)."""
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    output = stdout.decode() + stderr.decode()
    return proc.returncode == 0, output.strip()


async def _run_semantic_review(integration_path: Path, model: str) -> None:
    """Spawn a review agent to check for semantic conflicts."""
    options = ClaudeAgentOptions(
        system_prompt=REVIEWER_SYSTEM_PROMPT,
        model=model,
        cwd=str(integration_path),
        permission_mode="acceptEdits",
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        max_budget_usd=3.0,
        max_turns=20,
        setting_sources=["project"],
    )
    await run_agent(
        prompt="Review the merged changes for semantic conflicts and fix any issues you find.",
        options=options,
    )


async def _resolve_merge_conflict(
    integration_path: Path,
    branch: str,
    worker_result: WorkerResult,
    *,
    orchestrator_model: str = "opus",
) -> bool:
    """Attempt to resolve a merge conflict using a Claude agent. Returns True on success."""
    options = ClaudeAgentOptions(
        system_prompt=CONFLICT_RESOLVER_SYSTEM_PROMPT,
        model=orchestrator_model,
        cwd=str(integration_path),
        permission_mode="acceptEdits",
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        max_budget_usd=3.0,
        max_turns=20,
    )
    prompt = f"Resolve the merge conflicts from branch {branch} (worker: {worker_result.worker_id})."
    try:
        result = await run_agent(prompt=prompt, options=options)
        return not result.is_error
    except Exception:
        # Abort the merge so we're in a clean state
        await _run_git(["merge", "--abort"], integration_path, check=False)
        return False


async def _create_pr(
    integration_path: Path,
    integration_branch: str,
    base_branch: str,
    *,
    run_id: str,
    task_description: str,
    worker_results: list[WorkerResult],
) -> str:
    """Push branch and create a PR via gh CLI."""
    # Push the integration branch
    await _run_git(["push", "-u", "origin", integration_branch], integration_path)

    # Build PR body
    worker_summary = "\n".join(
        (
            f"- **{wr.worker_id}**: {wr.summary or 'completed'} (${wr.cost_usd:.2f})"
            if wr.cost_usd is not None
            else f"- **{wr.worker_id}**: {wr.summary or 'completed'}"
        )
        for wr in worker_results
    )
    total_cost = sum(wr.cost_usd or 0 for wr in worker_results)

    body = (
        f"## Task\n{task_description}\n\n"
        f"## Workers\n{worker_summary}\n\n"
        f"**Total cost**: ${total_cost:.2f}\n\n"
        f"---\n"
        f"Generated by [claude-swarm](https://github.com/obj-p/claude-swarm) (run: `{run_id}`)"
    )

    # Create PR
    proc = await asyncio.create_subprocess_exec(
        "gh", "pr", "create",
        "--title", f"[swarm] {task_description[:60]}",
        "--body", body,
        "--base", base_branch,
        "--head", integration_branch,
        cwd=str(integration_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        error = stderr.decode().strip()
        raise IntegrationError(f"Failed to create PR: {error}")

    pr_url = stdout.decode().strip()
    return pr_url
