"""Worker agent spawning and execution."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions

from claude_swarm.errors import WorkerError
from claude_swarm.models import WorkerResult, WorkerTask
from claude_swarm.prompts import WORKER_SYSTEM_PROMPT
from claude_swarm.util import run_agent

logger = logging.getLogger(__name__)


async def spawn_worker(
    task: WorkerTask,
    worktree_path: Path,
    *,
    model: str = "sonnet",
    max_budget_usd: float = 5.0,
    max_turns: int = 50,
) -> WorkerResult:
    """Spawn a Claude Code worker agent in an isolated worktree.

    The worker executes its assigned subtask and returns results.
    """
    start = time.monotonic()

    system_prompt = WORKER_SYSTEM_PROMPT.format(
        task_description=task.description,
        target_files="\n".join(f"- {f}" for f in task.target_files) if task.target_files else "No specific files targeted.",
        acceptance_criteria="\n".join(f"- {c}" for c in task.acceptance_criteria) if task.acceptance_criteria else "Complete the task as described.",
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
