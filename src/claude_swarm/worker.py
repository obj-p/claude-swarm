"""Worker agent spawning and execution."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions

from claude_swarm.errors import WorkerError
from claude_swarm.models import WorkerResult, WorkerTask
from claude_swarm.prompts import (
    WORKER_COORDINATION_INSTRUCTIONS,
    WORKER_NOTES_SECTION,
    WORKER_RETRY_CONTEXT,
    WORKER_SYSTEM_PROMPT,
)
from claude_swarm.util import run_agent

logger = logging.getLogger(__name__)


async def _spawn_single_attempt(
    task: WorkerTask,
    worktree_path: Path,
    *,
    model: str = "sonnet",
    extra_context: str = "",
    max_budget_usd: float = 5.0,
    max_turns: int = 50,
    notes_dir: Path | None = None,
) -> WorkerResult:
    """Spawn a single Claude Code worker attempt in an isolated worktree."""
    start = time.monotonic()

    system_prompt = WORKER_SYSTEM_PROMPT.format(
        task_description=task.description,
        target_files="\n".join(f"- {f}" for f in task.target_files) if task.target_files else "No specific files targeted.",
        acceptance_criteria="\n".join(f"- {c}" for c in task.acceptance_criteria) if task.acceptance_criteria else "Complete the task as described.",
    )

    if notes_dir is not None:
        system_prompt += WORKER_NOTES_SECTION.format(
            notes_dir_path=notes_dir,
            worker_id=task.worker_id,
        )
        if task.coordination_notes:
            system_prompt += WORKER_COORDINATION_INSTRUCTIONS.format(
                coordination_instructions=task.coordination_notes,
            )

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model,
        cwd=str(worktree_path),
        permission_mode="acceptEdits",
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        max_budget_usd=max_budget_usd,
        max_turns=max_turns,
        setting_sources=["project"],
    )

    prompt = f"## Task: {task.title}\n\n{task.description}"
    if extra_context:
        prompt += extra_context

    try:
        result = await run_agent(prompt=prompt, options=options)
        duration_ms = int((time.monotonic() - start) * 1000)

        if result.is_error:
            return WorkerResult(
                worker_id=task.worker_id,
                success=False,
                cost_usd=result.total_cost_usd,
                duration_ms=duration_ms,
                error=result.result or "Worker reported error",
            )

        return WorkerResult(
            worker_id=task.worker_id,
            success=True,
            cost_usd=result.total_cost_usd,
            duration_ms=duration_ms,
            summary=result.result,
        )

    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.error("Worker %s failed: %s", task.worker_id, e)
        raise WorkerError(f"Worker {task.worker_id} failed: {e}") from e


async def spawn_worker_with_retry(
    task: WorkerTask,
    worktree_path: Path,
    *,
    model: str = "sonnet",
    max_retries: int = 1,
    escalation_model: str = "opus",
    enable_escalation: bool = True,
    max_budget_usd: float = 5.0,
    max_turns: int = 50,
    notes_dir: Path | None = None,
) -> WorkerResult:
    """Spawn a worker with retry and optional model escalation.

    On failure, retries up to max_retries times. On the final attempt,
    escalates to escalation_model if enable_escalation is True.
    """
    last_result: WorkerResult | None = None

    for attempt in range(1, max_retries + 1):
        current_model = model
        # On later attempts, escalate model if enabled and previous attempt failed
        if attempt > 1 and enable_escalation and last_result and not last_result.success:
            current_model = escalation_model

        # Build retry context if this isn't the first attempt
        extra_context = ""
        if last_result and last_result.error:
            extra_context = WORKER_RETRY_CONTEXT.format(error_context=last_result.error)

        result = await _spawn_single_attempt(
            task, worktree_path,
            model=current_model,
            extra_context=extra_context,
            max_budget_usd=max_budget_usd,
            max_turns=max_turns,
            notes_dir=notes_dir,
        )
        result.attempt = attempt
        result.model_used = current_model

        if result.success:
            return result
        last_result = result

    return last_result  # type: ignore[return-value]


async def spawn_worker(
    task: WorkerTask,
    worktree_path: Path,
    *,
    model: str = "sonnet",
    max_budget_usd: float = 5.0,
    max_turns: int = 50,
    notes_dir: Path | None = None,
) -> WorkerResult:
    """Spawn a Claude Code worker agent in an isolated worktree.

    Thin wrapper around spawn_worker_with_retry with max_retries=1 (no retry).
    """
    return await spawn_worker_with_retry(
        task, worktree_path,
        model=model,
        max_retries=1,
        max_budget_usd=max_budget_usd,
        max_turns=max_turns,
        notes_dir=notes_dir,
    )
